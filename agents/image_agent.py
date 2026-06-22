"""Auto-generates the scene images a topic needs, via Nano Banana 2
(Gemini 3.1 Flash Image). Reuses the same GEMINI_IMAGE_API_KEY / GCP project
as narava-pipeline on purpose — see project memory project_apophenia.md.
Called from scheduler.py only when a topic has no images locally and none in
Supabase Storage yet.
"""
import base64
import json
import time
import urllib.request
import urllib.error

import anthropic
from config import ANTHROPIC_API_KEY, GEMINI_IMAGE_API_KEY, NANO_BANANA_MODEL, HAIKU_MODEL, IMAGES_DIR

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

IMAGE_COUNT = 12  # fallback only — generate_images() is normally called with an explicit
                  # duration-derived count, see images_for_duration() below

# Benchmark from analyzing the actual "Kee" reference channel via claude-watch scene-change
# detection (2026-06-21): 80 detected shot changes over an 18-min/1081s video = ~13.5s average
# shot duration. That's the right reference for THIS content (active-engagement essay) — NOT
# "Pendongeng Tidur" (45s avg shot duration), which is sleep/ambient content deliberately paced
# slower. The old fixed IMAGE_COUNT=12 + assembly_agent capping to 10 candidates meant a 9-min
# video cycled through only 10 images ~5 times each — visibly repetitive, flagged by user
# feedback on the first published video. See project memory project_apophenia.md.
# Tightened 2026-06-22 (topic #4 onward) — images now change roughly every
# sentence (see assembly_agent._sentence_slots), averaging ~5s/sentence, not
# the old fixed 13.5s slide grid. A smaller _TARGET_SHOT_SECONDS means more
# unique generated images per video, so the sentence-cadence cycle repeats
# the same image less often.
_TARGET_SHOT_SECONDS = 6.0
_MIN_IMAGES, _MAX_IMAGES = 24, 60


def images_for_duration(duration_sec):
    ideal = round(duration_sec / _TARGET_SHOT_SECONDS)
    return max(_MIN_IMAGES, min(_MAX_IMAGES, ideal))

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
    msg = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=max(1500, n * 130),  # scales for the higher end of images_for_duration()'s range
        system=_PROMPTS_SYSTEM.format(n=n),
        messages=[{"role": "user", "content": f"Archetype: {topic}\nAngle: {angle}"}],
    )
    text = msg.content[0].text.strip()
    start, end = text.find("["), text.rfind("]")
    scenes = json.loads(text[start:end + 1])
    return scenes[:n]


def _call_nano_banana(prompt, retries=3):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{NANO_BANANA_MODEL}:generateContent?key={GEMINI_IMAGE_API_KEY}"
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt + _STYLE_SUFFIX}]}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            for cand in data.get("candidates", []):
                for part in cand["content"]["parts"]:
                    if "inlineData" in part:
                        return base64.b64decode(part["inlineData"]["data"])
            raise RuntimeError(f"No image in Nano Banana response: {data}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Nano Banana {e.code}: {e.read().decode()}")
    raise RuntimeError("Nano Banana: exhausted retries")


def generate_images(topic, angle, category, slug, count=IMAGE_COUNT):
    """Generates `count` distinct scene images for a topic and writes them to
    images/{category}/{slug}/scene_NN.jpg. Returns the list of paths written."""
    out_dir = IMAGES_DIR / category.lower() / slug.lower()
    out_dir.mkdir(parents=True, exist_ok=True)

    scenes = _generate_scene_prompts(topic, angle, count)
    paths = []
    for i, scene_prompt in enumerate(scenes, start=1):
        img_bytes = _call_nano_banana(scene_prompt)
        out_path = out_dir / f"scene_{i:02d}.jpg"
        out_path.write_bytes(img_bytes)
        paths.append(out_path)
        print(f"    image {i}/{len(scenes)}: {scene_prompt[:70]}...")
    return paths
