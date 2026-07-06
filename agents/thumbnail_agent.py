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

import urllib.request as _urllib_req

from PIL import Image, ImageDraw, ImageFont

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, BASE_DIR

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
    body = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }).encode()
    req = _urllib_req.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
    )
    with _urllib_req.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


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


def _call_nano_banana(prompt, retries=3):
    """Generate a thumbnail scene via pollinations.ai (Flux, free, no API
    key). Kept the original name so all call-sites are unchanged. Switched
    from OpenRouter Nano Banana 2026-07-06 after OpenRouter credits ran out
    — same swap as agents/image_agent.py's content-image generation."""
    import time
    import urllib.parse
    import urllib.request

    encoded = urllib.parse.quote(prompt)
    seed = abs(hash(prompt)) % 2_000_000
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1280&height=720&nologo=true&model=flux&seed={seed}"
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
    font_size = 130
    margin = 56
    max_width = half - margin * 2

    def _wrap(text, fnt, mw):
        _m = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        # split on whitespace; also break hyphenated words at hyphens
        words = text.upper().split()
        tokens = []
        for w in words:
            if '-' in w:
                parts = w.split('-')
                for i, p in enumerate(parts):
                    tokens.append(p + '-' if i < len(parts) - 1 else p)
            else:
                tokens.append(w)
        tokens = [t for t in tokens if t]
        ls, cur = [], ""
        for t in tokens:
            trial = (cur + " " + t).strip() if cur else t
            if _m.textlength(trial, font=fnt) <= mw:
                cur = trial
            else:
                if cur:
                    ls.append(cur)
                cur = t
        if cur:
            ls.append(cur)
        return ls

    font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), font_size)
    lines = _wrap(hook_text, font, max_width)

    # If any single line still overflows (very long word), shrink font globally
    _m2 = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    widest = max((_m2.textlength(l, font=font) for l in lines), default=1)
    if widest > max_width:
        font_size = int(font_size * max_width / widest)
        font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), font_size)
        lines = _wrap(hook_text, font, max_width)

    line_height = int(font_size * 1.1)
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


# ── Psyphoria template ───────────────────────────────────────────────────────

_HOOK_SHORT_SYSTEM = """\
You are a YouTube thumbnail copywriter for a psychology channel (English).
Generate TWO ultra-short hook variants — 2-3 words MAXIMUM each.
Good examples: "Stop Explaining", "You're Not Crazy", "They Need You Weak", "The Silent Exit", "You Already Know".
Rules: punchy, personal, instant recognition, no punctuation, Title Case.
Return ONLY JSON: {"a": "...", "b": "..."}"""

_SCENE_PSY_SYSTEM = """\
You are a visual prompt writer for an AI image generator.
Given a psychology topic, write ONE image generation prompt (3-4 sentences).

Style rules:
- Bold expressionist / fauvist oil painting. Think Kirchner, Matisse, Munch.
  Thick visible brushstrokes, vivid saturated color fields, slightly distorted forms.
- Composition: close-up portrait or half-body. Figure fills most of the frame.
  Strong emotional expression readable on the face.
- 2-3 dominant vivid contrasting colors.
- Full bleed edge-to-edge. NO text, NO borders, NO frames, NO vignette.
Return ONLY the prompt text, no labels, no preamble."""

_SCENE_PSY_PAIR_SYSTEM = """\
You are a visual prompt writer for an AI image generator.
Given a psychology topic and TWO hook texts, write TWO scene prompts — A in watercolor style, B in comic vintage style.

PROMPT A (watercolor/gouache):
Bold gouache and watercolor illustration, visible wet brushstrokes, rich saturated color, painterly texture, slightly dreamlike.
Warm light against cool shadow. Objects and figures fully visible and clearly rendered — NOT ghostly, NOT transparent washes.

PROMPT B (vintage comic halftone):
Vintage-comic illustration with visible halftone-dot texture, gouache and ink, bold vivid saturated palette, sharp contrast.
Slightly surreal and graphic. Background color fills edge-to-edge.

Both prompts must follow these rules:
- Close-up portrait or half-body. Figure fills most of the frame. Strong emotional expression readable on the face.
- 2-3 dominant vivid contrasting colors. Full bleed edge-to-edge — ZERO white border, ZERO frame, ZERO vignette.
- Scene visually reinforces its hook text — the image is a metaphor FOR the text, not a generic archetype illustration.
- Two DIFFERENT scene concepts (different setting, symbol, or situation) — not the same scene restated in two styles.
- No text in the image, no logos, no real/identifiable person, no grimace or contorted face.

Return ONLY JSON: {"a": "...", "b": "..."}
Nothing else."""


def _compose_psyphoria(scene_bytes, hook_text, out_path):
    """Psyphoria style: solid black band at top, full scene image directly below — no overlap, no fade."""
    text = hook_text.upper()
    font_size = 165
    font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), font_size)

    bbox = font.getbbox(text)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    max_w = int(THUMB_SIZE[0] * 0.92)
    if text_w > max_w:
        font_size = int(font_size * max_w / text_w)
        font = ImageFont.truetype(str(THUMBNAIL_FONT_PATH), font_size)
        bbox = font.getbbox(text)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

    pad_v = 28
    band_h = pad_v + text_h + pad_v       # solid black band
    img_h  = THUMB_SIZE[1] - band_h       # image area below band

    # Scale scene to fill 1280 × img_h (cover)
    scene = Image.open(io.BytesIO(scene_bytes)).convert("RGB")
    scale = max(THUMB_SIZE[0] / scene.width, img_h / scene.height)
    new_w = int(scene.width  * scale) + 1
    new_h = int(scene.height * scale) + 1
    scene = scene.resize((new_w, new_h), Image.LANCZOS)
    x0 = (new_w - THUMB_SIZE[0]) // 2
    y0 = (new_h - img_h) // 2
    scene = scene.crop((x0, y0, x0 + THUMB_SIZE[0], y0 + img_h))

    # Solid black canvas, paste image below band
    canvas = Image.new("RGB", THUMB_SIZE, (0, 0, 0))
    canvas.paste(scene, (0, band_h))

    # White text in band
    draw = ImageDraw.Draw(canvas)
    x = (THUMB_SIZE[0] - text_w) // 2
    y = pad_v - bbox[1]
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    canvas.save(out_path, "JPEG", quality=95)


def generate_thumbnails_psyphoria(topic_data):
    """Psyphoria layout (solid black top band + full image below) with two art styles:
    A = watercolor/gouache, B = vintage comic halftone. Each variant gets its own
    scene image generated to match its hook text."""
    topic    = topic_data["topic"]
    angle    = topic_data["angle"]
    category = topic_data["category"]
    slug     = topic.lower().replace(" ", "_")
    out_dir  = THUMBNAILS_DIR / category / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    print("  [psyphoria] Generating hooks...")
    hooks = json.loads(_call_claude(
        _HOOK_SHORT_SYSTEM,
        f"Archetype: {topic}\nAngle: {angle}",
        max_tokens=80,
    ))
    print(f"  Hook A: {hooks['a']} | Hook B: {hooks['b']}")

    print("  [psyphoria] Generating scene prompts (watercolor A + comic B)...")
    raw = _call_claude(
        _SCENE_PSY_PAIR_SYSTEM,
        f"Topic: {topic}\nAngle: {angle}\nCategory: {category}\nHook A: {hooks['a']}\nHook B: {hooks['b']}",
        max_tokens=600,
    )
    clean = raw.replace("```json", "").replace("```", "").strip()
    s, e = clean.find("{"), clean.rfind("}")
    scenes = json.loads(clean[s:e + 1])
    print(f"  Scene A (watercolor): {scenes['a'][:80]}...")
    print(f"  Scene B (comic):      {scenes['b'][:80]}...")

    print("  [psyphoria] Generating scene A (watercolor)...")
    scene_a_bytes = _call_nano_banana(scenes["a"])
    print("  [psyphoria] Generating scene B (comic vintage)...")
    scene_b_bytes = _call_nano_banana(scenes["b"])

    path_a = out_dir / "thumb_A.jpg"
    path_b = out_dir / "thumb_B.jpg"
    _compose_psyphoria(scene_a_bytes, hooks["a"], path_a)
    _compose_psyphoria(scene_b_bytes, hooks["b"], path_b)
    print(f"  A (watercolor): {path_a}")
    print(f"  B (comic):      {path_b}")
    return path_a, path_b
