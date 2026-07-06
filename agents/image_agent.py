"""Auto-generates the scene images a topic needs via pollinations.ai (Flux model,
free, no API key). Switched from Nano Banana 2 (OpenRouter/Gemini Flash Image)
2026-07-06 after OpenRouter credits ran out. Called from scheduler.py only when
a topic has no images locally and none in Supabase Storage yet.
"""
import io
import json
import time
import urllib.parse
import urllib.request
import urllib.error

from PIL import Image
from config import IMAGES_DIR
import agents.llm as _llm

IMAGE_COUNT = 12  # fallback only — generate_images() is normally called with an explicit
                  # duration-derived count, see images_for_duration() below

# Replaced 2026-07-06 with a 3-segment pacing model benchmarked directly off
# @Psyphoria7 (785K subs, the channel this whole visual format is modeled on)
# via claude-watch scene-change detection on "The Silent Damage Caused by Your
# Ego" (30:44, 90 scenes detected): hook segment holds images 3-16s (~8s avg),
# main content holds 28-31s per image (confirmed via manual frame diff — two
# frames 30s apart were genuinely different paintings, not a pan/zoom), and
# one energetic "dynamic point" burst runs 4-8s/image. The old flat
# 6s/shot target (based on a different, faster-cut reference channel) needed
# 50-60 images per video; this needs roughly half that for the same duration,
# at both lower image-generation cost and a pacing that actually matches the
# reference channel instead of a generic essay-video guess. See project
# memory project_apophenia_retention_data.md for the full analysis.
HOOK_SECONDS = 40          # matches script_agent's own confirmed 0:05-0:40 retention window
HOOK_SHOT_SECONDS = 8
DYNAMIC_SECONDS = 60       # one energetic burst, placed around the babak 3->4 reveal
DYNAMIC_SHOT_SECONDS = 7
MAIN_SHOT_SECONDS = 29
_MIN_IMAGES, _MAX_IMAGES = 12, 40


def images_for_duration(duration_sec):
    hook_images = round(HOOK_SECONDS / HOOK_SHOT_SECONDS)
    dynamic_images = round(DYNAMIC_SECONDS / DYNAMIC_SHOT_SECONDS)
    main_seconds = max(0, duration_sec - HOOK_SECONDS - DYNAMIC_SECONDS)
    main_images = round(main_seconds / MAIN_SHOT_SECONDS)
    total = hook_images + main_images + dynamic_images
    return max(_MIN_IMAGES, min(_MAX_IMAGES, total))

# Rendering TREATMENT borrowed from "Pendongeng Tidur" — chiaroscuro composition, painterly
# illustration, a small figure within its surroundings. The OBJECTS/SETTING are NOT borrowed
# (those come fresh from each archetype's emotional content below), and as of 2026-06-21 the
# COLOR PALETTE is deliberately its OWN branding, not copied from Pendongeng Tidur's literal
# look: vintage-comic warm amber/gold against deep indigo, colorful and slightly surreal,
# explicitly NOT somber/desaturated and NOT mythology/period-costume coded (early versions
# of this prompt still read as "mythology character" — user feedback, see project memory
# project_apophenia.md). See also agents/thumbnail_agent.py for the separate Kee-style
# thumbnail layout, which is its own decision and not tied to this palette.
_STYLE_SUFFIX = (
    ", vintage comic illustration style with visible halftone-dot grain texture, gouache and "
    "ink rendering, bold colorful palette — warm amber and golden-orange light against deep "
    "indigo-navy shadow, vivid and saturated rather than desaturated or muted, slightly surreal "
    "dreamlike quality, sharp compositional contrast between warm light and cool dark areas but "
    "rendered in vivid color rather than near-black emptiness, small human figure within its "
    "surroundings in modern or timeless everyday clothing, cinematic wide shot, no text, no "
    "watermark, NOT mythological, NOT period-costume, NOT epic-fantasy styled. The illustration "
    "must completely fill the frame edge-to-edge with no border, no white margin, no comic-panel "
    "gutter, and no frame line of any kind — full-bleed artwork only, like a single uncropped "
    "painting, not a panel cut from a comic page. ABSOLUTELY NO white or cream paper-colored "
    "border strip around any edge of the image, no rounded comic-panel corners, no halftone-dot "
    "edge fading into blank white — the painted background color must run flush to all four "
    "edges of the canvas with zero exposed white space anywhere in the frame."
)

_PROMPTS_SYSTEM = """You write short visual scene descriptions for a psychology-essay YouTube \
channel (visual treatment: vintage-comic painterly illustration, warm amber-gold light against deep \
indigo shadow, colorful and slightly surreal — think a dreamlike full-bleed storybook illustration, \
not a literal therapy-office scene, and not a dark/desaturated mythology illustration. The image must \
fill the entire frame with no border, margin, or comic-panel outline of any kind).

Given a psychological archetype and its angle, output exactly {n} distinct visual scenes that work \
as SYMBOLIC/METAPHORICAL imagery for the emotional content of this pattern — not literal depictions \
of "a person in therapy" or "a sad person at a desk". Think in metaphor: a figure at a doorway half \
in shadow, a single lit window in a dark building, someone walking down a long corridor that keeps \
extending, a small boat on a vast dark sea, a figure carrying a too-large object, empty chairs around \
one lit chair, a house with all the windows dark but one. Each scene is a single vivid sentence \
describing a wide cinematic shot — setting, light, mood — built around contrast and scale, never a \
close-up on a face. No identifiable real person, no modern logos/text, no literal clinical settings.

Return ONLY a JSON array of {n} strings, nothing else."""


def _generate_scene_prompts(topic, angle, n=IMAGE_COUNT):
    text = _llm.call(
        f"Archetype: {topic}\nAngle: {angle}",
        system=_PROMPTS_SYSTEM.format(n=n),
        max_tokens=max(1500, n * 130),
    )
    start, end = text.find("["), text.rfind("]")
    scenes = json.loads(text[start:end + 1])
    return scenes[:n]


def _call_nano_banana(prompt, retries=3):
    """Generate an image via pollinations.ai (Flux, free, no API key).
    Kept the original name so all call-sites are unchanged."""
    styled = (
        prompt
        + ", vintage comic painterly illustration, warm amber-gold light, deep indigo shadows, "
        "dreamlike full-bleed wide cinematic shot, no borders, no white margins, no text"
    )
    encoded = urllib.parse.quote(styled)
    # Seed derived from prompt so the same scene always re-generates the same
    # image on retries/cache-misses — deterministic across runs.
    seed = abs(hash(prompt)) % 2_000_000
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1920&height=1080&nologo=true&model=flux&seed={seed}"
    )
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                img_bytes = resp.read()
            if len(img_bytes) < 5000:
                raise RuntimeError(f"Response suspiciously small ({len(img_bytes)} bytes)")
            return img_bytes
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(6 * (attempt + 1))
                continue
            raise RuntimeError(f"Pollinations failed after {retries} attempts: {e}")


# Deterministic backstop for the _STYLE_SUFFIX's "no white border" instruction
# above — Nano Banana ignores it often enough (confirmed visually on multiple
# published episodes, 2026-06-23) that a prompt-only fix isn't reliable, same
# reasoning as script_agent.py's regex backstop for AI-speak surviving the LLM
# audit pass. Trims any near-white/cream band creeping in from an edge before
# the image is ever written to disk. Capped at max_frac of each dimension so a
# legitimately bright scene (a lit window, daylight sky) in the middle of the
# frame is never mistaken for a border.
def _strip_white_border(img_bytes, threshold=235, max_frac=0.10):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    px = img.load()
    step_x, step_y = max(1, w // 40), max(1, h // 40)

    def row_is_border(y):
        return all(all(c >= threshold for c in px[x, y]) for x in range(0, w, step_x))

    def col_is_border(x):
        return all(all(c >= threshold for c in px[x, y]) for y in range(0, h, step_y))

    top = 0
    while top < h * max_frac and row_is_border(top):
        top += 1
    bottom = h - 1
    while bottom > h * (1 - max_frac) and row_is_border(bottom):
        bottom -= 1
    left = 0
    while left < w * max_frac and col_is_border(left):
        left += 1
    right = w - 1
    while right > w * (1 - max_frac) and col_is_border(right):
        right -= 1

    if top == 0 and bottom == h - 1 and left == 0 and right == w - 1:
        return img_bytes  # no border detected — skip re-encoding to avoid any quality loss

    cropped = img.crop((left, top, right + 1, bottom + 1))
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=92)
    print(f"    (trimmed white border: {left}/{top}/{w - 1 - right}/{h - 1 - bottom}px L/T/R/B)")
    return buf.getvalue()


def generate_images(topic, angle, category, slug, count=IMAGE_COUNT):
    """Generates `count` distinct scene images for a topic and writes them to
    images/{category}/{slug}/scene_NN.jpg. Returns the list of paths written."""
    out_dir = IMAGES_DIR / category.lower() / slug.lower()
    out_dir.mkdir(parents=True, exist_ok=True)

    scenes = _generate_scene_prompts(topic, angle, count)
    paths = []
    for i, scene_prompt in enumerate(scenes, start=1):
        img_bytes = _call_nano_banana(scene_prompt)
        img_bytes = _strip_white_border(img_bytes)
        out_path = out_dir / f"scene_{i:02d}.jpg"
        out_path.write_bytes(img_bytes)
        paths.append(out_path)
        print(f"    image {i}/{len(scenes)}: {scene_prompt[:70]}...")
        if i < len(scenes):
            time.sleep(4)  # pollinations rate-limit buffer between requests
    return paths
