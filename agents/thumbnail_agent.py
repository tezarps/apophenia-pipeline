"""Generates 2 thumbnail variants per video (Kee-style) for the sequential
A/B rotation driven by ab_test_check.py.

Layout, 3rd correction 2026-06-21 (see project memory project_apophenia.md
for the full back-and-forth — 1st attempt: flat color box + separate
character cutout, wrong. 2nd: whole-frame rough painterly poster texture,
also wrong. 3rd: flat zero-texture background, also too far — user shared a
close-up reference crop showing the background DOES have visible canvas/paper
grain texture, just not heavy brushwork, and the character IS vintage-comic
ink-illustrated, not a flat sticker): background is a TEXTURED canvas (subtle
grain, not flat-fill, not heavy brushstrokes either) using Apophenia's own
established palette (warm amber/gold + deep indigo — see agents/image_agent.py
_STYLE_SUFFIX, same brand identity, just at thumbnail/poster scale). Character
is a vintage-comic ink illustration with halftone-dot texture — a symbolic/
quirky object or figure (in the reference: a hand with a face drawn on the
palm), not a literal portrait. Text is off-white/cream, no outline, no shadow.

Font: Bebas Neue (assets/fonts/BebasNeue-Regular.ttf).
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

_SCENE_PROMPT_SYSTEM = """You write a single image-generation prompt for a YouTube thumbnail \
illustration in Apophenia's established visual identity: vintage-comic illustration with visible \
halftone-dot texture, gouache and ink rendering (see the channel's in-video illustration style).

Background: a textured canvas with subtle visible grain (like aged paper or canvas texture) — NOT a \
flat zero-texture fill, but also NOT covered in heavy visible brushstrokes; the grain should be subtle \
and even across the frame. Dominant background tone: {bg_color}.

Against that textured background, describe ONE vintage-comic ink-illustrated character or symbolic \
object — a little strange or quirky, like a single iconic visual idea (an object personified, a hand, \
an eye, a small figure), not a literal realistic portrait — positioned toward the {character_side} side \
of the frame. Leave the {text_side} side of the frame relatively open (just the textured background, no \
important detail) so text can be overlaid there later.

Given a psychological archetype, the character/object's emotional core should read through a simple, \
slightly surreal visual idea rather than a realistic distorted face (avoid words like grimace, twisted, \
contorted, anguished — these get flagged by the image model's safety filter). No text, no logos, no \
real/identifiable person.

Return ONLY the prompt string, nothing else."""

_SCENE_PROMPT_FALLBACK_TEMPLATE = (
    "Vintage-comic ink illustration with halftone-dot texture, a textured canvas background with "
    "subtle grain, dominant tone {bg_color}, one quirky symbolic illustrated object or small figure "
    "positioned toward the {character_side} side of the frame, {text_side} side left relatively open "
    "with just textured background, no text, no real person, not photorealistic"
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


def _generate_scene_prompt(topic, angle, bg_color, character_side, text_side):
    return _call_claude(
        _SCENE_PROMPT_SYSTEM.format(character_side=character_side, text_side=text_side, bg_color=bg_color),
        f"Archetype: {topic}\nAngle: {angle}",
    )


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


def _compose_thumbnail(scene_bytes, hook_text, text_side, out_path):
    canvas = _fit_cover(Image.open(io.BytesIO(scene_bytes)).convert("RGB"), THUMB_SIZE)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), 100)

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

    line_height = 100
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

    scene_prompt = _generate_scene_prompt(topic, angle, bg_color, character_side, text_side)
    print(f"    Scene prompt: {scene_prompt[:70]}...")
    try:
        scene_bytes = _call_nano_banana(scene_prompt)
    except RuntimeError as e:
        print(f"    Scene prompt refused ({e}) — retrying with safe fallback prompt...")
        scene_bytes = _call_nano_banana(_SCENE_PROMPT_FALLBACK_TEMPLATE.format(
            bg_color=bg_color, character_side=character_side, text_side=text_side,
        ))

    hooks = _generate_hook_variants(topic, angle)
    print(f"    Hook A: {hooks['a']} | Hook B: {hooks['b']}")

    path_a = out_dir / "thumb_A.jpg"
    path_b = out_dir / "thumb_B.jpg"
    _compose_thumbnail(scene_bytes, hooks["a"], text_side, path_a)
    _compose_thumbnail(scene_bytes, hooks["b"], text_side, path_b)
    return path_a, path_b
