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

IMAGE_COUNT = 12

# Rendering TREATMENT borrowed from "Pendongeng Tidur" — sharp chiaroscuro, painterly/
# woodcut texture, one dramatic light source, a small figure dwarfed by its surroundings.
# The OBJECTS/SETTING are NOT borrowed — those come fresh from each archetype's emotional
# content below. See project memory project_apophenia.md.
_STYLE_SUFFIX = (
    ", dramatic chiaroscuro lighting, sharp contrast between deep dark areas and one "
    "saturated accent of light, painterly digital illustration with visible woodcut-like "
    "texture, single dramatic light source, small human figure dwarfed by its surroundings, "
    "cinematic wide shot, no text, no watermark, moody and atmospheric, not flat or evenly lit"
)

_PROMPTS_SYSTEM = """You write short visual scene descriptions for a psychology-essay YouTube \
channel (visual treatment: sharp dark/light contrast, painterly, a small figure dwarfed by its \
surroundings — think the mood of a moody illustrated parable, not a literal therapy-office scene).

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
        max_tokens=1500,
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
