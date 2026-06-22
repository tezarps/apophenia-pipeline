"""Generates 2 thumbnail variants per video (Kee-style) for the sequential
A/B rotation driven by ab_test_check.py.

STYLE LOCKED 2026-06-21 — confirmed by user against a real Kee reference crop,
do not redesign again without new explicit direction (3 prior iterations
already rejected, see project memory project_apophenia.md for the full
back-and-forth): background is a TEXTURED canvas (subtle grain, NOT a flat
zero-texture fill, NOT heavy visible brushstrokes either) using Apophenia's
own established palette (warm amber/gold + deep indigo — see
agents/image_agent.py _STYLE_SUFFIX, same brand identity, just at thumbnail/
poster scale). Character is a vintage-comic ink illustration with
halftone-dot texture — a symbolic/quirky object or figure (e.g. trembling
clasped hands, a hand with a face drawn on the palm), not a literal portrait.
Text is off-white/cream Bebas Neue, no outline, no shadow.

Always generates exactly 2 variants (thumb_A.jpg, thumb_B.jpg) with a
SEPARATE generated scene image each (not the same image with different text)
— A/B testing a thumbnail that's only different by text is a weak test. Both
scenes share the same background tone/character-side/text-side layout rules
so they stay on-brand, but the symbolic object/character itself differs
between A and B, alongside the hook text. See _generate_scene_prompt_pair().
"""
import io
import json

import anthropic
from PIL import Image, ImageDraw, ImageFont

from config import ANTHROPIC_API_KEY, GEMINI_IMAGE_API_KEY, NANO_BANANA_MODEL, HAIKU_MODEL, BASE_DIR

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

THUMBNAILS_DIR = BASE_DIR / "thumbnails"
THUMBNAIL_FONT_PATH = BASE_DIR / "assets" / "fonts" / "BebasNeue-Regular.ttf"
THUMB_SIZE = (1280, 720)
TEXT_COLOR = (245, 238, 222)  # off-white/cream, not pure white — matches the vintage-aged palette

# Apophenia's own brand palette (see agents/image_agent.py) at thumbnail scale —
# variations on warm amber/gold vs. deep indigo, rotated by topic id, NOT
# arbitrary unrelated hues.
_BG_PALETTE = [
    "deep indigo-navy canvas with a warm amber undertone",
    "warm amber-gold canvas with deep indigo shadow undertone",
    "deep charcoal-indigo canvas",
    "muted burnt-orange canvas with dark undertone",
    "deep teal-indigo canvas with a warm accent",
]
_TEXT_SIDE = ["right", "left"]

_SCENE_PROMPT_PAIR_SYSTEM = """You write TWO image-generation prompts for a YouTube thumbnail A/B \
test, both in Apophenia's established visual identity: vintage-comic illustration with visible \
halftone-dot texture, gouache and ink rendering (see the channel's in-video illustration style).

Background for BOTH: a textured canvas with subtle visible grain (like aged paper or canvas texture) \
— NOT a flat zero-texture fill, but also NOT covered in heavy visible brushstrokes; the grain should \
be subtle and even across the frame. Dominant background tone for both: {bg_color}.

CRITICAL — full-bleed, no frame: the artwork must fill the ENTIRE image edge-to-edge with zero border. \
Do NOT include a postcard/photo frame, comic-panel border, picture-frame edge, rounded corners, vignette, \
deckled/torn-paper edge, or any decorative border of any kind around the scene — these get cropped \
awkwardly when composited and must be absent. The textured canvas itself IS the background, painted all \
the way to the image edges, not a separate frame around it.

Each prompt describes a DIFFERENT vintage-comic ink-illustrated character or symbolic object — a \
little strange or quirky, like a single iconic visual idea (an object personified, a hand, an eye, a \
small figure), not a literal realistic portrait — positioned toward the {character_side} side of the \
frame. Leave the {text_side} side of the frame relatively open (just the textured background, no \
important detail) so text can be overlaid there later.

The two ideas must be genuinely different visual concepts from each other (different object, pose, or \
symbol), not the same scene restated — A/B testing requires the two thumbnails to actually look \
different, not just carry different text. Both should still read as capturing the same archetype's \
emotional core through a simple, slightly surreal visual idea rather than a realistic distorted face \
(avoid words like grimace, twisted, contorted, anguished — these get flagged by the image model's \
safety filter). No text, no logos, no real/identifiable person.

Given a psychological archetype, return a JSON object: {{"a": "prompt for variant A", "b": "prompt for \
variant B"}}. Return ONLY that JSON object, nothing else."""

_SCENE_PROMPT_FALLBACK_TEMPLATE = (
    "Vintage-comic ink illustration with halftone-dot texture, a textured canvas background with "
    "subtle grain, dominant tone {bg_color}, one quirky symbolic illustrated object or small figure "
    "positioned toward the {character_side} side of the frame, {text_side} side left relatively open "
    "with just textured background, no text, no real person, not photorealistic. Full-bleed, no "
    "border, no frame, no vignette, no rounded corners, no postcard or comic-panel edge — artwork "
    "fills the entire image edge-to-edge."
)

_HOOK_TEXT_SYSTEM = """You write thumbnail hook text for a psychology-essay YouTube channel (style: \
"The Psychology of People Who..."). Given an archetype and its title/angle, return a JSON object with \
two SHORT punchy hook variants (3-6 words each, title-case, no period), each capturing the same core \
idea from a slightly different emphasis — these will be A/B tested against each other.

Format: {"a": "...", "b": "..."}
Return ONLY that JSON object, nothing else."""


def _call_claude(system, user, max_tokens=300):
    msg = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text.strip()


def _generate_scene_prompt_pair(topic, angle, bg_color, character_side, text_side, retries=3):
    """Claude occasionally returns a non-JSON response (empty/preamble-only) —
    transient, seen twice in production. Retry a few times before falling
    back to the no-frame template prompt for both variants, rather than
    crashing the whole pipeline run on a one-off API hiccup."""
    last_err = None
    for attempt in range(retries):
        try:
            text = _call_claude(
                _SCENE_PROMPT_PAIR_SYSTEM.format(character_side=character_side, text_side=text_side, bg_color=bg_color),
                f"Archetype: {topic}\nAngle: {angle}",
            )
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end == -1:
                raise ValueError(f"no JSON object in response: {text[:200]!r}")
            return json.loads(text[start:end + 1])
        except Exception as e:
            last_err = e
            print(f"    Scene prompt pair generation failed (attempt {attempt+1}/{retries}): {e}")

    print(f"    Falling back to template scene prompts for both variants ({last_err})")
    base = _SCENE_PROMPT_FALLBACK_TEMPLATE.format(
        bg_color=bg_color, character_side=character_side, text_side=text_side,
    )
    return {"a": base, "b": base + " Alternate composition and pose from variant A."}


def _generate_hook_variants(topic, angle):
    text = _call_claude(_HOOK_TEXT_SYSTEM, f"Archetype: {topic}\nAngle: {angle}")
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def _call_nano_banana(prompt):
    import base64
    import urllib.request

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{NANO_BANANA_MODEL}:generateContent?key={GEMINI_IMAGE_API_KEY}"
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    for cand in data.get("candidates", []):
        for part in cand["content"]["parts"]:
            if "inlineData" in part:
                return base64.b64decode(part["inlineData"]["data"])
    raise RuntimeError(f"No image in Nano Banana response: {data}")


def _fit_cover(img, size):
    """Resize+crop to fill `size` exactly, like CSS background-size: cover."""
    w0, h0 = img.size
    scale = max(size[0] / w0, size[1] / h0)
    img = img.resize((int(w0 * scale) + 1, int(h0 * scale) + 1))
    x0 = (img.width - size[0]) // 2
    y0 = (img.height - size[1]) // 2
    return img.crop((x0, y0, x0 + size[0], y0 + size[1]))


def _add_text_scrim(canvas, text_side):
    """Nano Banana doesn't reliably honor "leave this side open" — it has put
    light/cream speech-bubble art directly under the cream hook text (Topic
    #2 variant B, flagged 2026-06-22: "WHY YOU OWN" was unreadable against a
    light bubble background). Re-analyzing contrast and regenerating the
    scene costs another API round-trip and can still fail the same way.
    A deterministic dark gradient scrim behind the text half guarantees
    readability regardless of what the model drew there — same technique
    most YouTube thumbnails already use, applied unconditionally rather than
    only after detecting a problem."""
    overlay = Image.new("RGBA", THUMB_SIZE, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    half = THUMB_SIZE[0] // 2
    text_x0 = 0 if text_side == "left" else half
    fade_width = half // 2
    for col in range(half):
        x = text_x0 + col
        if text_side == "left":
            alpha = 165 if col < half - fade_width else int(165 * (half - col) / fade_width)
        else:
            alpha = int(165 * col / fade_width) if col < fade_width else 165
        odraw.line([(x, 0), (x, THUMB_SIZE[1])], fill=(10, 10, 14, max(0, alpha)))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def _compose_thumbnail(scene_bytes, hook_text, text_side, out_path):
    canvas = _fit_cover(Image.open(io.BytesIO(scene_bytes)).convert("RGB"), THUMB_SIZE)
    canvas = _add_text_scrim(canvas, text_side)
    draw = ImageDraw.Draw(canvas)
    # Bumped 100 -> 130 — at 100 the hook text read too small to clearly
    # parse at YouTube thumbnail-grid size, flagged by user feedback
    # 2026-06-22.
    font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), 130)

    margin = 56
    max_width = THUMB_SIZE[0] // 2 - margin
    words = hook_text.upper().split()
    lines, current = [], ""
    for w in words:
        trial = f"{current} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)

    line_height = 130
    total_h = line_height * len(lines)
    y = (THUMB_SIZE[1] - total_h) // 2
    x = margin if text_side == "left" else THUMB_SIZE[0] // 2 + margin // 2
    for i, line in enumerate(lines):
        draw.text((x, y + i * line_height), line, font=font, fill=TEXT_COLOR)

    canvas.save(out_path, quality=95)


def generate_thumbnails(topic_data):
    """Generates thumb_A.jpg and thumb_B.jpg for a topic. Returns (path_a, path_b)."""
    topic_id, category, topic, angle = topic_data["id"], topic_data["category"], topic_data["topic"], topic_data["angle"]
    slug = topic.lower().replace(" ", "_")
    out_dir = THUMBNAILS_DIR / category.lower() / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    bg_color = _BG_PALETTE[int(topic_id) % len(_BG_PALETTE)]
    text_side = _TEXT_SIDE[int(topic_id) % len(_TEXT_SIDE)]
    character_side = "left" if text_side == "right" else "right"

    prompts = _generate_scene_prompt_pair(topic, angle, bg_color, character_side, text_side)
    print(f"    Scene A: {prompts['a'][:70]}...")
    print(f"    Scene B: {prompts['b'][:70]}...")

    def _scene_with_fallback(prompt, label):
        try:
            return _call_nano_banana(prompt)
        except RuntimeError as e:
            print(f"    Scene {label} refused ({e}) — retrying with safe fallback prompt...")
            return _call_nano_banana(_SCENE_PROMPT_FALLBACK_TEMPLATE.format(
                bg_color=bg_color, character_side=character_side, text_side=text_side,
            ))

    scene_bytes_a = _scene_with_fallback(prompts["a"], "A")
    scene_bytes_b = _scene_with_fallback(prompts["b"], "B")

    hooks = _generate_hook_variants(topic, angle)
    print(f"    Hook A: {hooks['a']} | Hook B: {hooks['b']}")

    path_a = out_dir / "thumb_A.jpg"
    path_b = out_dir / "thumb_B.jpg"
    _compose_thumbnail(scene_bytes_a, hooks["a"], text_side, path_a)
    _compose_thumbnail(scene_bytes_b, hooks["b"], text_side, path_b)
    return path_a, path_b
