"""Generates 2 thumbnail variants per video for the sequential A/B rotation.

LAYOUT LOCKED 2026-06-26 — confirmed by user, do not change without explicit new direction:

  COMPOSITING APPROACH (locked):
  - Scene is generated FULL-WIDTH (1280×720) — NEVER cropped to half-width.
    Cropping causes the figure to be cut at the edge and the center to be empty.
  - Character/focal point leans toward character_side (left or right by topic id).
  - text_side half is covered by a solid dark panel (bg_rgb from palette)
    composited as an RGBA overlay with a 200px gradient fade at the boundary.
  - Text drawn on the solid panel — always readable, no scrim needed.

  SCENE STYLE (locked):
  - Variant A: vivid gouache painterly watercolor — NO ink outlines.
  - Variant B: vintage-comic halftone — style OK, but ZERO white border,
    ZERO frame line, ZERO vignette ring. Artwork bleeds edge-to-edge.
  - Figure must be LARGE and PROMINENT — not a tiny distant silhouette.
    Close enough to read posture and emotion at thumbnail size.
  - Visual weight leans toward character_side, NOT dead-center.

Always generates exactly 2 variants (thumb_A.jpg, thumb_B.jpg) with a
SEPARATE generated scene image each. See _generate_scene_prompt_pair().
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
    ("deep navy-indigo", "warm amber-gold"),
    ("deep forest-green", "dusty coral-rose"),
    ("deep burgundy-red", "pale gold"),
    ("deep slate-charcoal", "electric teal"),
    ("deep violet-purple", "warm amber"),
    ("dark teal", "soft warm cream-yellow"),
    ("deep cobalt-blue", "warm orange-gold"),
    ("dark olive-khaki", "cool lavender-blue"),
    ("deep crimson-maroon", "cool mint-teal"),
    ("dark petrol-blue", "muted dusty-rose"),
]
_TEXT_SIDE = ["right", "left"]

# Dark RGB value for the solid text-panel side, matched per bg palette entry.
# These are intentionally very dark — just enough tint to signal the brand hue.
_BG_PANEL_RGB = {
    "deep navy-indigo":    (6,  8, 32),
    "deep forest-green":   (4, 18,  8),
    "deep burgundy-red":  (26,  4,  8),
    "deep slate-charcoal":(12, 14, 18),
    "deep violet-purple": (14,  6, 26),
    "dark teal":           (4, 20, 22),
    "deep cobalt-blue":    (6,  8, 34),
    "dark olive-khaki":   (16, 18,  6),
    "deep crimson-maroon":(28,  4,  6),
    "dark petrol-blue":    (4, 12, 24),
}

_SCENE_PROMPT_PAIR_SYSTEM = """You write TWO image-generation prompts for a YouTube thumbnail A/B \
test. The two variants use DIFFERENT visual styles — the A/B test is style vs style, not just text \
vs text.

ENFORCED COLOR PALETTE — background: {bg}; accent/highlight: {accent}. \
Both variants MUST visually match this exact palette — do NOT default to teal+amber or \
indigo+gold unless that IS what {bg}/{accent} specify. If the palette says burgundy-red + pale gold, \
the scene must look burgundy-dominant with pale gold light. If forest-green + coral-rose, it must \
look green-dominant with coral/rose highlights. Varying the palette per video is intentional — enforce it.

SCENE STYLE — cinematic storytelling, full-bleed, no frame:
Each thumbnail is a FULL 1280×720 px wide cinematic scene. The figure or focal point must be \
prominent — large enough that posture and emotion read clearly at thumbnail size, not a tiny \
distant silhouette. Place the densest visual weight (main subject, strongest contrast) toward the \
{character_side} side so it remains visible after the {text_side} side is overlaid with a dark panel. \
CRITICAL: the artwork must bleed completely edge-to-edge with ZERO white space, ZERO margin, \
ZERO border, ZERO panel frame, ZERO vignette ring. The illustration fills every pixel corner. \
Strong light/shadow, symbolic setting, psychological metaphor readable in one glance.

COMPOSITION — visual weight toward {character_side}, {text_side} will be darkened:
Generate a full 1280×720 px wide landscape scene. Place the main figure and focal point clearly on \
the {character_side} side — this area stays fully visible. The {text_side} half will have a solid \
dark panel composited over it in post, so avoid putting critical detail there. \
The figure must be large and prominent — not a tiny distant silhouette; lean it toward the \
{character_side} edge, not dead-center.

VARIANT A STYLE — Vivid gouache painterly watercolor:
Bold gouache and watercolor illustration, visible wet brushstrokes, rich saturated color, painterly \
texture. Warm light against cool shadow, slightly dreamlike. \
Objects and figures must be fully visible and clearly rendered — NOT ghostly, NOT fading, NOT \
transparent washes. \
ABSOLUTELY NO white border, NO frame line, NO comic-panel box edge, NO vignette ring — \
the artwork bleeds completely to every pixel edge with zero empty margin.

VARIANT B STYLE — Vivid comic halftone:
Vintage-comic illustration with visible halftone-dot texture, gouache and ink, bold vivid saturated \
palette, sharp contrast between warm accent light and dark background shadow, slightly surreal and \
graphic. \
ABSOLUTELY NO white border, NO frame line, NO comic-panel box edge, NO vignette ring — \
the artwork bleeds completely to every pixel edge with zero empty margin. \
The background color of the scene must fill edge-to-edge; \
there must be NO white or light-colored gap between the illustration and the image boundary.

HOOK TEXT MATCH: The scene MUST visually reinforce its hook text — the image is a metaphor FOR the \
text, not a generic archetype illustration. Hook A goes with scene A, hook B with scene B — do not \
swap. Examples: "Why Every Yes Costs You" → a figure handing out glowing coins from an emptying jar. \
"The Weight of Always Being Needed" → a small figure bent under a pile of colorful gift-wrapped boxes.

The two prompts must describe DIFFERENT scene concepts (different setting, symbol, or situation), not \
the same scene restated. No text in the image, no logos, no real/identifiable person, no realistic \
distorted face (avoid: grimace, contorted, anguished — these get safety-filtered).

Given a psychological archetype AND its two hook-text variants, return a JSON object: \
{{"a": "prompt for variant A (matches hook A)", "b": "prompt for variant B (matches hook B)"}}. \
Return ONLY that JSON object, nothing else."""

_SCENE_PROMPT_FALLBACK_TEMPLATE_A = (
    "PORTRAIT composition (640×720 px) for the {character_side} half of a YouTube thumbnail. "
    "Bold gouache and watercolor illustration, visible wet brushstrokes, rich saturated color, slightly dreamlike. "
    "Enforced palette: background {bg}, accent/highlight {accent}. "
    "A human figure prominent in frame, set against a meaningful symbolic environment — depth, atmosphere, metaphor. "
    "ALL key visual elements within this {character_side} portrait half. "
    "ZERO white border, ZERO frame line, ZERO margin — artwork bleeds completely edge-to-edge. "
    "No text, no real person."
)
_SCENE_PROMPT_FALLBACK_TEMPLATE_B = (
    "PORTRAIT composition (640×720 px) for the {character_side} half of a YouTube thumbnail. "
    "Vintage-comic halftone-dot texture, gouache and ink, bold vivid saturated palette, slightly surreal and graphic. "
    "Enforced palette: background {bg}, accent/highlight {accent}. "
    "A human figure prominent in frame, strong light/shadow, symbolic setting. "
    "ALL key visual elements within this {character_side} portrait half. "
    "ZERO white border, ZERO frame line, ZERO margin — artwork bleeds completely edge-to-edge. "
    "No text, no real person."
)

_HOOK_TEXT_SYSTEM = """You write thumbnail hook text for a psychology-essay YouTube channel. The VIDEO \
TITLE already carries the formal/academic framing (e.g. "The Psychology of Social Invisibility") — the \
thumbnail text's only job is different: stop the scroll in under a second with plain, everyday words a \
12-year-old would understand, phrased as a blunt question or claim that creates a curiosity gap. Think \
"Why Crowds Make You Disappear" or "The Real Reason You Go Quiet" — NOT "The Psychology Of X", NOT \
clinical/academic vocabulary (avoid words like "invisibility", "phenomenon", "behavior pattern"), NOT a \
restatement of the title in different words.

The text must be SHORT (3-6 words, title-case, no period) and instantly readable at thumbnail size — \
one glance, zero effort, immediate curiosity. Bold, blunt, personal. A viewer should feel like it's \
talking directly about them before they've even decided to click.

Given an archetype and its title/angle, return a JSON object with two SHORT hook variants (3-6 words \
each, title-case, no period), each creating the same curiosity gap from a slightly different angle — \
these will be A/B tested against each other.

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


def _generate_scene_prompt_pair(topic, angle, bg, accent, character_side, text_side, hooks, retries=3):
    """hooks: {"a": hook_text_a, "b": hook_text_b} — passed so each scene image
    is generated TO MATCH its corresponding hook text rather than being
    generated independently from the archetype alone (image must reinforce text).
    Claude occasionally returns a non-JSON response (empty/preamble-only) —
    transient, seen twice in production. Retry a few times before falling
    back to the no-frame template prompt for both variants."""
    last_err = None
    for attempt in range(retries):
        try:
            text = _call_claude(
                _SCENE_PROMPT_PAIR_SYSTEM.format(character_side=character_side, text_side=text_side, bg=bg, accent=accent),
                f"Archetype: {topic}\nAngle: {angle}\nHook text A: {hooks['a']}\nHook text B: {hooks['b']}",
                max_tokens=4096,
            )
            # Strip markdown code fences if Claude wraps the JSON
            clean = text.replace("```json", "").replace("```", "").strip()
            start, end = clean.find("{"), clean.rfind("}")
            if start == -1 or end == -1:
                raise ValueError(f"no JSON object in response: {text[:200]!r}")
            return json.loads(clean[start:end + 1])
        except Exception as e:
            last_err = e
            print(f"    Scene prompt pair generation failed (attempt {attempt+1}/{retries}): {e}")

    print(f"    Falling back to template scene prompts for both variants ({last_err})")
    return {
        "a": _SCENE_PROMPT_FALLBACK_TEMPLATE_A.format(bg=bg, accent=accent, character_side=character_side, text_side=text_side),
        "b": _SCENE_PROMPT_FALLBACK_TEMPLATE_B.format(bg=bg, accent=accent, character_side=character_side, text_side=text_side),
    }


def _generate_hook_variants(topic, angle, category=None):
    mechanism_line = f"\nSpecific psychological mechanism/symptom this video centers on: {category}" if category else ""
    text = _call_claude(_HOOK_TEXT_SYSTEM, f"Archetype: {topic}\nAngle: {angle}{mechanism_line}")
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


def _add_text_scrim(canvas, text_side, max_alpha=210):
    """Nano Banana doesn't reliably honor "leave this side open" — it has put
    light/cream speech-bubble art directly under the cream hook text (Topic
    #2 variant B, flagged 2026-06-22: "WHY YOU OWN" was unreadable against a
    light bubble background). Re-analyzing contrast and regenerating the
    scene costs another API round-trip and can still fail the same way.
    A deterministic dark gradient scrim behind the text half guarantees
    readability regardless of what the model drew there — same technique
    most YouTube thumbnails already use, applied unconditionally rather than
    only after detecting a problem.
    max_alpha bumped 165→210 (2026-06-25): 65% opacity wasn't enough when
    the model drew a bright scene on the text side despite instructions."""
    overlay = Image.new("RGBA", THUMB_SIZE, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    half = THUMB_SIZE[0] // 2
    text_x0 = 0 if text_side == "left" else half
    fade_width = half // 2  # wide gentle fade matches reference visual style
    for col in range(half):
        x = text_x0 + col
        if text_side == "left":
            alpha = max_alpha if col < half - fade_width else int(max_alpha * (half - col) / fade_width)
        else:
            alpha = int(max_alpha * col / fade_width) if col < fade_width else max_alpha
        odraw.line([(x, 0), (x, THUMB_SIZE[1])], fill=(10, 10, 14, max(0, alpha)))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def _qa_text_readable(path, hook_text):
    from agents.qa_agent import check
    score, passed = check(path, hook_text)
    print(f"    QA score: {score}/5")
    return passed


def _compose_thumbnail(scene_bytes, hook_text, text_side, out_path, bg_rgb=(10, 10, 14)):
    """Full-width scene + solid dark panel overlay on text_side with gradient fade.
    Scene is never cropped — fits the full 1280×720 canvas so no figure gets cut.
    The text_side half is covered by a solid bg_rgb panel that fades in over 200px."""
    half = THUMB_SIZE[0] // 2  # 640

    # Scene fills the full canvas — never cropped to half-width
    canvas = _fit_cover(Image.open(io.BytesIO(scene_bytes)).convert("RGB"), THUMB_SIZE)

    # Solid dark panel overlay on text_side, fading in from character_side boundary
    fade_width = 200
    overlay = Image.new("RGBA", THUMB_SIZE, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for col in range(half):
        if text_side == "right":
            x = half + col
            alpha = int(255 * col / fade_width) if col < fade_width else 255
        else:
            x = half - 1 - col
            alpha = int(255 * col / fade_width) if col < fade_width else 255
        odraw.line([(x, 0), (x, THUMB_SIZE[1])], fill=(*bg_rgb, alpha))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    # Word-wrap hook text to fit inside the text-panel half
    font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), 130)
    margin = 56
    max_width = half - margin * 2

    _measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    words = hook_text.upper().split()
    lines, current = [], ""
    for w in words:
        trial = f"{current} {w}".strip()
        if _measure.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)

    line_height = 130
    total_h = line_height * len(lines)
    y = (THUMB_SIZE[1] - total_h) // 2
    x = margin if text_side == "left" else half + margin

    draw = ImageDraw.Draw(canvas)
    for i, line in enumerate(lines):
        draw.text((x, y + i * line_height), line, font=font, fill=TEXT_COLOR)

    canvas.save(out_path, quality=95)
    if _qa_text_readable(out_path, hook_text):
        print("    QA: pass")
    else:
        print("    QA: low score — text panel is solid so this is likely a false flag")


def generate_thumbnails(topic_data):
    """Generates thumb_A.jpg and thumb_B.jpg for a topic. Returns (path_a, path_b)."""
    topic_id, category, topic, angle = topic_data["id"], topic_data["category"], topic_data["topic"], topic_data["angle"]
    slug = topic.lower().replace(" ", "_")
    out_dir = THUMBNAILS_DIR / category.lower() / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    bg, accent = _BG_PALETTE[int(topic_id) % len(_BG_PALETTE)]
    bg_rgb = _BG_PANEL_RGB.get(bg, (10, 10, 14))
    text_side = _TEXT_SIDE[int(topic_id) % len(_TEXT_SIDE)]
    character_side = "left" if text_side == "right" else "right"

    # Hook text first — scene images are generated TO MATCH the hook text
    # (image must visually reinforce what the text says, not be generated
    # independently). Order matters: hooks → scene prompts → images → compose.
    hooks = _generate_hook_variants(topic, angle, category=category)
    print(f"    Hook A: {hooks['a']} | Hook B: {hooks['b']}")
    print(f"    Palette: bg={bg!r}, accent={accent!r}")

    prompts = _generate_scene_prompt_pair(topic, angle, bg, accent, character_side, text_side, hooks)
    print(f"    Scene A: {prompts['a'][:70]}...")
    print(f"    Scene B: {prompts['b'][:70]}...")

    def _scene_with_fallback(prompt, label):
        fallback_tpl = _SCENE_PROMPT_FALLBACK_TEMPLATE_A if label == "A" else _SCENE_PROMPT_FALLBACK_TEMPLATE_B
        fallback_prompt = fallback_tpl.format(
            bg=bg, accent=accent, character_side=character_side, text_side=text_side,
        )
        for attempt, p in enumerate([prompt, fallback_prompt]):
            try:
                return _call_nano_banana(p)
            except Exception as e:
                tag = "primary" if attempt == 0 else "fallback"
                print(f"    Scene {label} {tag} failed ({type(e).__name__}: {e}) — {'trying fallback' if attempt == 0 else 'giving up'}")
        raise RuntimeError(f"Scene {label}: both primary and fallback prompts failed")

    scene_bytes_a = _scene_with_fallback(prompts["a"], "A")
    scene_bytes_b = _scene_with_fallback(prompts["b"], "B")

    path_a = out_dir / "thumb_A.jpg"
    path_b = out_dir / "thumb_B.jpg"
    _compose_thumbnail(scene_bytes_a, hooks["a"], text_side, path_a, bg_rgb=bg_rgb)
    _compose_thumbnail(scene_bytes_b, hooks["b"], text_side, path_b, bg_rgb=bg_rgb)
    return path_a, path_b
