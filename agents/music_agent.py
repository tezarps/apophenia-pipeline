"""Provides the background ambient bed for every video by trimming/looping
ONE shared reference track (assets/shared_ambient.mp3) instead of generating
a fresh one per topic via ElevenLabs' Sound Generation API.

Switched 2026-07-11 — ElevenLabs credits are at 0 (confirmed via the API's
own quota_exceeded response, same underlying constraint that already moved
narration to Kokoro and images to pollinations.ai/manual). Per-topic AI
generation is paused for now, not removed: the old ElevenLabs code path and
its category/texture-variation prompts are preserved below (unused) so this
can be flipped back once ElevenLabs credits are available again, without
having to redesign the music step from scratch.

The reference track was topic 11's already-generated bed (11.75 min, mood-
neutral "warm and reflective" — no category-specific prompt was defined for
its category, so it used _DEFAULT_PROMPT, making it a safe universal choice).
Every video reuses the exact same track, trimmed or looped to fit.
"""
import shutil
import subprocess
from config import BASE_DIR, FFMPEG_BIN, FFPROBE_BIN

MUSIC_DIR = BASE_DIR / "assets" / "music"
SHARED_TRACK = BASE_DIR / "assets" / "shared_ambient.mp3"
FADE_OUT_SEC = 3  # smooths the cut/loop-wrap instead of an abrupt stop


def _track_duration_sec():
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(SHARED_TRACK)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def generate_topic_music(topic_id, category, target_duration_sec=None):
    """Generates (if missing) and returns the path to assets/music/{topic_id}.mp3
    — the shared ambient track trimmed or looped (via ffmpeg -stream_loop) to
    cover target_duration_sec, with a short fade-out so the cut/loop-wrap
    isn't abrupt. Cached per topic_id, same as before, so a retry on the
    same topic doesn't redo the ffmpeg pass."""
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MUSIC_DIR / f"{topic_id}.mp3"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    if not SHARED_TRACK.exists():
        raise FileNotFoundError(
            f"Shared ambient track missing at {SHARED_TRACK} — "
            "restore assets/shared_ambient.mp3 or switch back to ElevenLabs generation."
        )

    duration = target_duration_sec or 22
    track_len = _track_duration_sec()
    fade_start = max(0, duration - FADE_OUT_SEC)

    if duration <= track_len:
        # Shorter than the reference track — trim, no looping needed.
        cmd = [
            FFMPEG_BIN, "-y", "-i", str(SHARED_TRACK), "-t", f"{duration:.3f}",
            "-af", f"afade=t=out:st={fade_start:.3f}:d={FADE_OUT_SEC}",
            str(out_path),
        ]
    else:
        # Longer than the reference track — loop indefinitely, cut to length.
        # -stream_loop -1 repeats the input; the fade-out still lands cleanly
        # on the final cut regardless of which loop iteration it falls in.
        cmd = [
            FFMPEG_BIN, "-y", "-stream_loop", "-1", "-i", str(SHARED_TRACK),
            "-t", f"{duration:.3f}",
            "-af", f"afade=t=out:st={fade_start:.3f}:d={FADE_OUT_SEC}",
            str(out_path),
        ]

    print(f"    Music: reusing shared ambient track ({duration:.0f}s{' looped' if duration > track_len else ' trimmed'})")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Music trim/loop failed: {result.stderr[-400:]}")
    return out_path


# ── ElevenLabs per-topic generation (paused, not deleted — see module docstring) ──
#
# import math
# import requests
# from config import ELEVENLABS_API_KEY
#
# _CATEGORY_PROMPTS = {
#   "avoidant-attachment": "slow ambient piano and soft pad drone, distant and a little cold, "
#     "introspective, no percussion, no melody hook, gentle sustained tones, calm and spacious",
#   "enmeshment": "warm slow ambient strings and soft piano, intimate but slightly claustrophobic "
#     "undertone, no percussion, no melody hook, gentle sustained tones, calm and spacious",
#   "fawn-response": "soft ambient piano with gentle tension underneath, warm but uneasy, "
#     "no percussion, no melody hook, slow sustained tones, calm and spacious",
# }
# _DEFAULT_PROMPT = ("slow ambient piano and soft pad drone, warm and reflective, no percussion, "
#   "no melody hook, gentle sustained tones, calm and spacious, suitable as quiet background "
#   "music under spoken narration")
# _ENDING_PROMPTS = {
#   "avoidant-attachment": "slow ambient piano and soft pad drone, resolving and settling, "
#     "gently descending toward stillness, no percussion, no melody hook, warm closing tone",
#   "enmeshment": "warm slow ambient strings and soft piano, resolving and settling, gently "
#     "descending toward stillness, no percussion, no melody hook, warm closing tone",
#   "fawn-response": "soft ambient piano, tension releasing, resolving and settling, gently "
#     "descending toward stillness, no percussion, no melody hook, warm closing tone",
# }
# _DEFAULT_ENDING_PROMPT = ("slow ambient piano and soft pad drone, resolving and settling, gently "
#   "descending toward stillness, warm closing tone, no percussion, no melody hook")
# _TEXTURE_VARIATIONS = [
#   "with a faint music box undertone", "with subtle tape-hiss warmth",
#   "with a low cello drone beneath", "with delicate glass-harmonica shimmer",
#   "with soft rain-on-window texture underneath", "with a distant muted vibraphone shimmer",
#   "with faint vinyl crackle texture",
# ]
# SEGMENT_SEC = 30
# CROSSFADE_SEC = 5
#
# def _generate_segment(prompt, out_path):
#   resp = requests.post(
#     "https://api.elevenlabs.io/v1/sound-generation",
#     headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
#     json={"text": prompt, "duration_seconds": SEGMENT_SEC, "prompt_influence": 0.4},
#     timeout=120,
#   )
#   resp.raise_for_status()
#   out_path.write_bytes(resp.content)
#
# def _crossfade_concat(segment_paths, out_path):
#   if len(segment_paths) == 1:
#     shutil.copy(segment_paths[0], out_path)
#     return
#   cmd = [FFMPEG_BIN, "-y"]
#   for p in segment_paths:
#     cmd += ["-i", str(p)]
#   filt_parts = []
#   prev_label = "0:a"
#   for i in range(1, len(segment_paths)):
#     cur_label = f"f{i}"
#     filt_parts.append(f"[{prev_label}][{i}:a]acrossfade=d={CROSSFADE_SEC}:c1=tri:c2=tri[{cur_label}]")
#     prev_label = cur_label
#   cmd += ["-filter_complex", ";".join(filt_parts), "-map", f"[{prev_label}]", str(out_path)]
#   result = subprocess.run(cmd, capture_output=True, text=True)
#   if result.returncode != 0:
#     raise RuntimeError(f"Music crossfade-concat failed: {result.stderr[-400:]}")
#
# def generate_topic_music_elevenlabs(topic_id, category, target_duration_sec=None):
#   MUSIC_DIR.mkdir(parents=True, exist_ok=True)
#   out_path = MUSIC_DIR / f"{topic_id}.mp3"
#   if out_path.exists() and out_path.stat().st_size > 0:
#     return out_path
#   body_mood = _CATEGORY_PROMPTS.get(category.lower(), _DEFAULT_PROMPT)
#   texture = _TEXTURE_VARIATIONS[int(topic_id) % len(_TEXTURE_VARIATIONS)]
#   body_prompt = f"{body_mood}, {texture}"
#   ending_prompt = _ENDING_PROMPTS.get(category.lower(), _DEFAULT_ENDING_PROMPT)
#   duration = target_duration_sec or 22
#   unique_per_seg = SEGMENT_SEC - CROSSFADE_SEC
#   n_segments = 1 if duration <= SEGMENT_SEC else math.ceil((duration - SEGMENT_SEC) / unique_per_seg) + 1
#   tmp_dir = MUSIC_DIR / f"_tmp_{topic_id}"
#   tmp_dir.mkdir(exist_ok=True)
#   try:
#     seg_paths = []
#     for i in range(n_segments):
#       seg_path = tmp_dir / f"seg_{i:02d}.mp3"
#       prompt = ending_prompt if i == n_segments - 1 else body_prompt
#       _generate_segment(prompt, seg_path)
#       seg_paths.append(seg_path)
#     _crossfade_concat(seg_paths, out_path)
#   finally:
#     shutil.rmtree(tmp_dir, ignore_errors=True)
#   return out_path
