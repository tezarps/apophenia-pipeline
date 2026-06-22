"""Generates one continuous ambient backsound track per VIDEO (not per
category) via ElevenLabs' Sound Generation API (same ELEVENLABS_API_KEY
already used for narration TTS — no new credential needed).

The bed is built from several ElevenLabs sound-generation calls chained with
acrossfade, sized to the video's actual total length (narration + outro
tail), instead of generating one short clip and looping it with `aloop` —
flagged by user feedback 2026-06-22: looping a ~20s clip under an 8-12min
video produced an audible seam every loop, reading as choppy rather than
smooth. The FINAL segment uses a distinct "resolving/settling" prompt instead
of the body texture, so the bed itself winds down under the CTA/outro instead
of just being cut off by assembly_agent's fade-out.

Generating per-topic instead of per-category was a deliberate change
2026-06-22 — reusing one fixed file per category made every video in that
category sound identical, which gets noticeable fast across a twice-weekly
channel. Each topic still gets a category-anchored mood (so the channel stays
sonically coherent) but a distinct texture variation on top, so no two videos
share the exact same bed.
"""
import math
import shutil
import subprocess
import requests
from config import ELEVENLABS_API_KEY, BASE_DIR, FFMPEG_BIN

MUSIC_DIR = BASE_DIR / "assets" / "music"

# Calm, melancholic-but-warm instrumental bed — matches the channel's
# emotionally restrained essay tone, never dramatic or melodic enough to
# compete with narration. One mood anchor per category, same instrumentation
# family so the channel still feels sonically consistent across topics.
_CATEGORY_PROMPTS = {
    "avoidant-attachment": "slow ambient piano and soft pad drone, distant and a little cold, "
        "introspective, no percussion, no melody hook, gentle sustained tones, calm and spacious",
    "enmeshment": "warm slow ambient strings and soft piano, intimate but slightly claustrophobic "
        "undertone, no percussion, no melody hook, gentle sustained tones, calm and spacious",
    "fawn-response": "soft ambient piano with gentle tension underneath, warm but uneasy, "
        "no percussion, no melody hook, slow sustained tones, calm and spacious",
}
_DEFAULT_PROMPT = ("slow ambient piano and soft pad drone, warm and reflective, no percussion, "
    "no melody hook, gentle sustained tones, calm and spacious, suitable as quiet background "
    "music under spoken narration")

# Closing mood for the LAST segment only — same instrumentation family as the
# body, but explicitly resolving/settling so the bed itself reads as an
# ending under the CTA, not just abruptly faded out by assembly_agent.
_ENDING_PROMPTS = {
    "avoidant-attachment": "slow ambient piano and soft pad drone, resolving and settling, "
        "gently descending toward stillness, no percussion, no melody hook, warm closing tone",
    "enmeshment": "warm slow ambient strings and soft piano, resolving and settling, gently "
        "descending toward stillness, no percussion, no melody hook, warm closing tone",
    "fawn-response": "soft ambient piano, tension releasing, resolving and settling, gently "
        "descending toward stillness, no percussion, no melody hook, warm closing tone",
}
_DEFAULT_ENDING_PROMPT = ("slow ambient piano and soft pad drone, resolving and settling, gently "
    "descending toward stillness, warm closing tone, no percussion, no melody hook")

# Rotated by topic_id so back-to-back videos in the same category still sound
# distinct from each other, not just distinct from other categories.
_TEXTURE_VARIATIONS = [
    "with a faint music box undertone",
    "with subtle tape-hiss warmth",
    "with a low cello drone beneath",
    "with delicate glass-harmonica shimmer",
    "with soft rain-on-window texture underneath",
    "with a distant muted vibraphone shimmer",
    "with faint vinyl crackle texture",
]

SEGMENT_SEC = 30      # ElevenLabs sound-generation's max duration per call
CROSSFADE_SEC = 5     # overlap blended between segments — no hard loop seam
FALLBACK_DURATION_SEC = 22  # used only if no target duration is given


def _generate_segment(prompt, out_path):
    resp = requests.post(
        "https://api.elevenlabs.io/v1/sound-generation",
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={"text": prompt, "duration_seconds": SEGMENT_SEC, "prompt_influence": 0.4},
        timeout=120,
    )
    resp.raise_for_status()
    out_path.write_bytes(resp.content)


def _crossfade_concat(segment_paths, out_path):
    """Chains segments end-to-end with acrossfade so the bed has no audible
    loop seam, instead of assembly_agent looping one short clip under the
    whole video."""
    if len(segment_paths) == 1:
        shutil.copy(segment_paths[0], out_path)
        return

    cmd = [FFMPEG_BIN, "-y"]
    for p in segment_paths:
        cmd += ["-i", str(p)]

    filt_parts = []
    prev_label = "0:a"
    for i in range(1, len(segment_paths)):
        cur_label = f"f{i}"
        filt_parts.append(f"[{prev_label}][{i}:a]acrossfade=d={CROSSFADE_SEC}:c1=tri:c2=tri[{cur_label}]")
        prev_label = cur_label
    cmd += ["-filter_complex", ";".join(filt_parts), "-map", f"[{prev_label}]", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Music crossfade-concat failed: {result.stderr[-400:]}")


def generate_topic_music(topic_id, category, target_duration_sec=None):
    """Generates (if missing) and returns the path to assets/music/{topic_id}.mp3
    — a continuous bed covering target_duration_sec (pass the video's total
    length, e.g. narration duration + assembly_agent.OUTRO_TAIL_SEC). If no
    target is given, falls back to a single short clip (old behavior).

    Cached per topic_id only — so a retry on the SAME topic doesn't repay for
    generation, but every new topic gets a genuinely new track."""
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MUSIC_DIR / f"{topic_id}.mp3"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    body_mood = _CATEGORY_PROMPTS.get(category.lower(), _DEFAULT_PROMPT)
    texture = _TEXTURE_VARIATIONS[int(topic_id) % len(_TEXTURE_VARIATIONS)]
    body_prompt = f"{body_mood}, {texture}"
    ending_prompt = _ENDING_PROMPTS.get(category.lower(), _DEFAULT_ENDING_PROMPT)

    duration = target_duration_sec or FALLBACK_DURATION_SEC
    unique_per_seg = SEGMENT_SEC - CROSSFADE_SEC
    n_segments = 1 if duration <= SEGMENT_SEC else math.ceil((duration - SEGMENT_SEC) / unique_per_seg) + 1

    tmp_dir = MUSIC_DIR / f"_tmp_{topic_id}"
    tmp_dir.mkdir(exist_ok=True)
    try:
        seg_paths = []
        for i in range(n_segments):
            seg_path = tmp_dir / f"seg_{i:02d}.mp3"
            prompt = ending_prompt if i == n_segments - 1 else body_prompt
            print(f"    Music segment {i+1}/{n_segments}{' (ending)' if i == n_segments - 1 else ''}...")
            _generate_segment(prompt, seg_path)
            seg_paths.append(seg_path)

        _crossfade_concat(seg_paths, out_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return out_path
