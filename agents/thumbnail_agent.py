"""Generates 2 thumbnail variants per video (Kee-style) for the sequential
A/B rotation driven by ab_test_check.py.

Layout (corrected 2026-06-21 after direct comparison against real Kee
thumbnails — see project memory project_apophenia.md): ONE full-bleed
painterly/folk-art illustration covering the entire 1280x720 frame, character
painted directly into the same textured canvas (NOT a flat color block on one
side + a separate character cutout composited on top — that was the original,
wrong reading of the reference). Bold poster text is overlaid straight on top
of the painterly background, white fill with a black outline, no per-word
color highlight (the real references don't have one). Variant A and B differ
in hook phrasing only, sharing the same generated scene — isolates the thing
actually being tested.

Font: Anton (assets/fonts/Anton-Regular.ttf) — tall poster-bold condensed
sans, much closer to the reference than Archivo Black's squarer geometric look.
"""
import io
import json

import anthropic
from PIL import Image, ImageDraw, ImageFont

from config import ANTHROPIC_API_KEY, GEMINI_IMAGE_API_KEY, NANO_BANANA_MODEL, HAIKU_MODEL, BASE_DIR

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

THUMBNAILS_DIR = BASE_DIR / "thumbnails"
THUMBNAIL_FONT_PATH = BASE_DIR / "assets" / "fonts" / "Anton-Regular.ttf"
THUMB_SIZE = (1280, 720)

# Rotated by topic id, alternating which side stays open for text — Kee's own
# thumbnails put the character left, right, or center depending on the piece.
_TONE_PALETTE = ["deep blood red", "dark navy blue", "matte black", "warm mustard yellow", "forest green"]
_TEXT_SIDE = ["right", "left"]

_SCENE_PROMPT_SYSTEM = """You write a single image-generation prompt for a YouTube thumbnail \
illustration, in the style of bold folk-art / outsider-art poster painting (think a hand-painted \
horror-movie or true-crime-podcast poster, NOT a clean digital portrait, NOT photorealistic) — \
visible rough brush texture and grain covering the ENTIRE frame as one continuous painted surface, \
slightly naive/iconic character drawing rather than realistic anatomy, a bit unsettling or strange \
the way a striking poster illustration is.

Given a psychological archetype, describe ONE full-frame scene: a single iconic character or symbolic \
figure (composite/generic, not a real person) integrated directly into the painted background — same \
surface, same texture, no separate background — positioned toward the {character_side} side of the \
frame, leaving the {text_side} side comparatively open/uncluttered (just textured background, no \
important detail there) so text can be overlaid on it later. Dominant tone: {tone}. The character's \
emotional core should read through a strange or iconic visual (an object, a posture, a symbolic prop) \
rather than a realistic distorted face — avoid words like grimace, twisted, contorted, anguished (these \
get flagged by the image model's safety filter). No text, no logos, no real/identifiable person.

Return ONLY the prompt string, nothing else."""

_SCENE_PROMPT_FALLBACK_TEMPLATE = (
    "Bold folk-art poster painting style, rough brush texture and grain covering the entire frame, "
    "dominant tone {tone}, a single strange iconic generic figure positioned toward the {character_side} "
    "side of the frame, {text_side} side left open and uncluttered with just textured background, "
    "no text, no real person, not photorealistic"
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


def _generate_scene_prompt(topic, angle, tone, character_side, text_side):
    return _call_claude(
        _SCENE_PROMPT_SYSTEM.format(character_side=character_side, text_side=text_side, tone=tone),
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
    font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), 88)

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

    line_height = 96
    total_h = line_height * len(lines)
    y = (THUMB_SIZE[1] - total_h) // 2
    x = margin if text_side == "left" else THUMB_SIZE[0] // 2 + margin // 2
    for i, line in enumerate(lines):
        draw.text((x, y + i * line_height), line, font=font, fill=(255, 255, 255),
                   stroke_width=10, stroke_fill=(0, 0, 0))

    canvas.save(out_path, quality=95)


def generate_thumbnails(topic_data):
    """Generates thumb_A.jpg and thumb_B.jpg for a topic. Returns (path_a, path_b)."""
    topic_id, category, topic, angle = topic_data["id"], topic_data["category"], topic_data["topic"], topic_data["angle"]
    slug = topic.lower().replace(" ", "_")
    out_dir = THUMBNAILS_DIR / category.lower() / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    tone = _TONE_PALETTE[int(topic_id) % len(_TONE_PALETTE)]
    text_side = _TEXT_SIDE[int(topic_id) % len(_TEXT_SIDE)]
    character_side = "left" if text_side == "right" else "right"

    scene_prompt = _generate_scene_prompt(topic, angle, tone, character_side, text_side)
    print(f"    Scene prompt: {scene_prompt[:70]}...")
    try:
        scene_bytes = _call_nano_banana(scene_prompt)
    except RuntimeError as e:
        print(f"    Scene prompt refused ({e}) — retrying with safe fallback prompt...")
        scene_bytes = _call_nano_banana(_SCENE_PROMPT_FALLBACK_TEMPLATE.format(
            tone=tone, character_side=character_side, text_side=text_side,
        ))

    hooks = _generate_hook_variants(topic, angle)
    print(f"    Hook A: {hooks['a']} | Hook B: {hooks['b']}")

    path_a = out_dir / "thumb_A.jpg"
    path_b = out_dir / "thumb_B.jpg"
    _compose_thumbnail(scene_bytes, hooks["a"], text_side, path_a)
    _compose_thumbnail(scene_bytes, hooks["b"], text_side, path_b)
    return path_a, path_b
