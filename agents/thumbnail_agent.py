"""Generates 2 thumbnail variants per video (Kee-style layout) for the
sequential A/B rotation driven by ab_test_check.py.

Layout: flat saturated color background, a painterly character portrait
filling roughly half the frame (chiaroscuro treatment, same rendering style
as the in-video scene images — see agents/image_agent.py), bold hook text on
the empty half. Variant A and B differ in hook phrasing/emphasis and/or
background accent color, NOT in the underlying character art — this isolates
the thing actually being tested (the text hook) rather than confounding it
with a different illustration.

Font: Archivo Black (assets/fonts/ArchivoBlack-Regular.ttf) — heavy geometric
sans, matches the Kee thumbnail reference look. Cinzel-Bold (serif) is a
Narava mythology-era leftover, not used here.
"""
import json
import textwrap

import anthropic
from PIL import Image, ImageDraw, ImageFont

from config import ANTHROPIC_API_KEY, GEMINI_IMAGE_API_KEY, NANO_BANANA_MODEL, HAIKU_MODEL, BASE_DIR

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

THUMBNAILS_DIR = BASE_DIR / "thumbnails"
THUMBNAIL_FONT_PATH = BASE_DIR / "assets" / "fonts" / "ArchivoBlack-Regular.ttf"
THUMB_SIZE = (1280, 720)
HIGHLIGHT_COLOR = (255, 184, 28)  # same yellow/orange used for caption keyword highlights

# Rotated by topic id so the channel isn't always red, always blue — Kee's own
# thumbnails vary background color per video while keeping the same layout DNA.
_BG_PALETTE = [(20, 20, 20), (214, 40, 40), (245, 196, 0), (30, 70, 150)]

_CHARACTER_PROMPT_SYSTEM = """You write a single image-generation prompt for a thumbnail character \
portrait, in the visual style of dramatic chiaroscuro painterly illustration (sharp dark/light \
contrast, expressive/unsettling features, slightly exaggerated — NOT photorealistic, NOT a literal \
portrait of any real person). Given a psychological archetype, describe ONE character portrait, \
cropped at chest height, facing slightly off-camera, with an expression that hints at the archetype's \
emotional core (exhausted, masking, hypervigilant, etc. — whatever fits). No background detail \
(it will be composited onto a flat color). No text. No real/identifiable person.

Return ONLY the prompt string, nothing else."""

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


def _generate_character_prompt(topic, angle):
    return _call_claude(_CHARACTER_PROMPT_SYSTEM, f"Archetype: {topic}\nAngle: {angle}")


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


def _compose_thumbnail(bg_color, character_bytes, hook_text, out_path):
    import io

    canvas = Image.new("RGB", THUMB_SIZE, bg_color)
    char_img = Image.open(io.BytesIO(character_bytes)).convert("RGBA")

    # Character fills the right half, vertically centered, cropped to frame height.
    char_w = THUMB_SIZE[0] // 2
    char_h = THUMB_SIZE[1]
    char_ratio = char_img.width / char_img.height
    target_ratio = char_w / char_h
    if char_ratio > target_ratio:
        new_h = char_h
        new_w = int(new_h * char_ratio)
    else:
        new_w = char_w
        new_h = int(new_w / char_ratio)
    char_img = char_img.resize((new_w, new_h))
    left = (new_w - char_w) // 2
    top = (new_h - char_h) // 2
    char_img = char_img.crop((left, top, left + char_w, top + char_h))
    canvas.paste(char_img, (THUMB_SIZE[0] - char_w, 0), char_img)

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), 92)
    text_color = (255, 255, 255) if sum(bg_color) < 400 else (10, 10, 10)

    margin_x = 60
    max_width = THUMB_SIZE[0] // 2 - margin_x
    words = hook_text.split()
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
    for i, line in enumerate(lines):
        # Highlight the last line's last word in yellow/orange, matching caption style.
        color = HIGHLIGHT_COLOR if i == len(lines) - 1 else text_color
        draw.text((margin_x, y + i * line_height), line, font=font, fill=color, stroke_width=4,
                   stroke_fill=(0, 0, 0) if sum(bg_color) < 400 else (255, 255, 255))

    canvas.save(out_path, quality=95)


def generate_thumbnails(topic_data):
    """Generates thumb_A.jpg and thumb_B.jpg for a topic. Returns (path_a, path_b)."""
    topic_id, category, topic, angle = topic_data["id"], topic_data["category"], topic_data["topic"], topic_data["angle"]
    slug = topic.lower().replace(" ", "_")
    out_dir = THUMBNAILS_DIR / category.lower() / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    bg_color = _BG_PALETTE[int(topic_id) % len(_BG_PALETTE)]
    char_prompt = _generate_character_prompt(topic, angle)
    print(f"    Character prompt: {char_prompt[:70]}...")
    character_bytes = _call_nano_banana(char_prompt)

    hooks = _generate_hook_variants(topic, angle)
    print(f"    Hook A: {hooks['a']} | Hook B: {hooks['b']}")

    path_a = out_dir / "thumb_A.jpg"
    path_b = out_dir / "thumb_B.jpg"
    _compose_thumbnail(bg_color, character_bytes, hooks["a"], path_a)
    _compose_thumbnail(bg_color, character_bytes, hooks["b"], path_b)
    return path_a, path_b
