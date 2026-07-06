import hashlib
import json
import re
import subprocess
import shutil

import numpy as np
import soundfile as sf

from config import (
    OUTPUT_DIR, FFMPEG_BIN, FFPROBE_BIN,
    KOKORO_VOICE, KOKORO_SPEED, KOKORO_MODEL_PATH, KOKORO_VOICES_PATH,
)

SILENCE_MS = 450          # gap between ordinary script chunks

# Longer pause right before the CTA sign-off line so it reads as a deliberate
# close, not more content (user feedback 2026-06-22).
CTA_GAP_MS = 1600
MAX_VOLUME_RANGE_DB = 9   # re-roll chunk if level drift exceeds this
QUALITY_RETRIES = 2

# Sentence-level chunking — same value as before the Kokoro switch.
# Per-chunk caching and the CTA-gap logic both depend on sentence boundaries,
# independent of which TTS backend renders the audio.
MAX_CHUNK_CHARS = 220

_CHUNK_EXT = "wav"   # Kokoro's native output; chunks stored as WAV internally
_OUT_EXT = "mp3"     # final merged file always mp3 for pipeline/scheduler compat

_kokoro = None


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
    return _kokoro


def _chunk_text(text, max_chars=MAX_CHUNK_CHARS):
    sentences = text.replace("\n\n", " ").replace("\n", " ").split(". ")
    chunks, current = [], ""
    for s in sentences:
        # Force the sign-off sentence onto its own chunk so _gap_durations_ms
        # can insert the longer CTA pause before it.
        if "apophenia" in s.lower() and current:
            chunks.append(current.strip())
            current = ""
        candidate = current + s + ". "
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = s + ". "
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _audio_duration(path):
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())


def _segment_mean_volumes(audio_path, n_segments=4):
    duration = _audio_duration(audio_path)
    if duration < 2:
        return []
    seg_len = duration / n_segments
    volumes = []
    for i in range(n_segments):
        result = subprocess.run(
            [FFMPEG_BIN, "-ss", str(i * seg_len), "-t", str(seg_len), "-i", str(audio_path),
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True
        )
        match = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", result.stderr)
        if match:
            volumes.append(float(match.group(1)))
    return volumes


def _is_consistent(audio_path):
    volumes = _segment_mean_volumes(audio_path)
    if len(volumes) < 2:
        return True, 0
    spread = max(volumes) - min(volumes)
    return spread <= MAX_VOLUME_RANGE_DB, spread


def _synthesize_chunk(text, out_path, max_quality_retries=QUALITY_RETRIES):
    """Generate one chunk via Kokoro (local, no network). Volume-drift
    re-roll kept as a safety net even though Kokoro is near-deterministic."""
    kokoro = _get_kokoro()

    for attempt in range(max_quality_retries + 1):
        samples, sample_rate = kokoro.create(
            text, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang="en-us"
        )
        sf.write(str(out_path), samples, sample_rate)

        ok, spread = _is_consistent(out_path)
        if ok:
            return
        if attempt >= max_quality_retries:
            print(f"    Volume drift {spread:.1f}dB persists — accepting best take")
            return
        print(f"    Volume drift {spread:.1f}dB — re-rolling ({attempt+1}/{max_quality_retries})...")


def _gap_durations_ms(chunk_texts):
    gaps = [SILENCE_MS] * len(chunk_texts)
    for i in range(1, len(chunk_texts)):
        if "apophenia" in chunk_texts[i].lower():
            gaps[i - 1] = CTA_GAP_MS
    return gaps


def _merge_with_ffmpeg(chunk_dir, out_path, chunk_texts=None):
    """Concat WAV chunks + silence gaps, then loudnorm → mp3 final output."""
    chunks = sorted(chunk_dir.glob(f"[0-9][0-9][0-9][0-9].{_CHUNK_EXT}"))
    gaps_ms = _gap_durations_ms(chunk_texts) if chunk_texts else [SILENCE_MS] * len(chunks)

    # Silence generated at Kokoro's native 24kHz to match chunk sample rate.
    kokoro_sr = 24000

    def _make_silence(ms, name):
        path = chunk_dir / name
        subprocess.run([
            FFMPEG_BIN, "-y", "-f", "lavfi",
            "-i", f"anullsrc=r={kokoro_sr}:cl=mono",
            "-t", f"{ms / 1000:.3f}",
            "-c:a", "pcm_s16le", str(path)
        ], capture_output=True, check=True)
        return path

    silence_paths = {}
    for ms in set(gaps_ms):
        silence_paths[ms] = _make_silence(ms, f"silence_{ms}.{_CHUNK_EXT}")

    concat_file = chunk_dir / "concat.txt"
    lines = []
    for chunk, gap_ms in zip(chunks, gaps_ms):
        lines.append(f"file '{chunk}'")
        lines.append(f"file '{silence_paths[gap_ms]}'")
    concat_file.write_text("\n".join(lines))

    raw_merged = chunk_dir / f"merged_raw.{_CHUNK_EXT}"
    subprocess.run([
        FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:a", "pcm_s16le",
        str(raw_merged)
    ], capture_output=True, check=True)

    # Single loudnorm pass on the full program — same reasoning as Narava.
    # -14 LUFS matches YouTube's target so playback isn't attenuated vs other videos.
    subprocess.run([
        FFMPEG_BIN, "-y", "-i", str(raw_merged),
        "-af", "loudnorm=I=-14:TP=-2.0:LRA=20",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(out_path)
    ], capture_output=True, check=True)


def generate_audio(script_text, topic_id, category=None, with_captions=False, highlight_words=None):
    """Returns audio_path if with_captions=False, or (audio_path, word_timings)
    if with_captions=True. Kokoro doesn't produce word-level alignment, so
    word_timings is always [] — the video assembly handles this gracefully
    (even image slots, no reflective-beat blackscreens). Cache prevents
    re-rendering on pipeline retries after later-stage failures."""
    out_path = OUTPUT_DIR / "audio" / f"{topic_id}.{_OUT_EXT}"
    timings_path = OUTPUT_DIR / "audio" / f"{topic_id}_timings.json"
    if out_path.exists() and out_path.stat().st_size > 0:
        if with_captions and timings_path.exists():
            print(f"    TTS: using cached audio + timings (no re-render) → {out_path.name}")
            return out_path, json.loads(timings_path.read_text())
        if not with_captions:
            print(f"    TTS: using cached audio (no re-render) → {out_path.name}")
            return out_path

    chunks = _chunk_text(script_text)
    total_chars = sum(len(c) for c in chunks)
    print(f"    TTS: {len(chunks)} chunks, ~{total_chars:,} chars")
    print(f"    Voice: Kokoro {KOKORO_VOICE} (local, no API cost)")

    chunk_dir = OUTPUT_DIR / "audio" / f"chunks_{topic_id}"
    script_hash = hashlib.md5(script_text.encode()).hexdigest()[:8]
    hash_file = chunk_dir / ".script_hash"

    if chunk_dir.exists() and hash_file.exists():
        if hash_file.read_text().strip() != script_hash:
            print("    Script changed — clearing cached chunks")
            shutil.rmtree(chunk_dir)

    chunk_dir.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(script_hash)

    for i, chunk in enumerate(chunks):
        out = chunk_dir / f"{i:04d}.{_CHUNK_EXT}"
        if out.exists() and out.stat().st_size > 0:
            print(f"    TTS: {i+1}/{len(chunks)} (cached)")
            continue
        _synthesize_chunk(chunk, out)
        print(f"    TTS: {i+1}/{len(chunks)} done")

    print("    Merging audio...")
    _merge_with_ffmpeg(chunk_dir, out_path, chunk_texts=chunks)
    shutil.rmtree(chunk_dir)

    # Kokoro provides no word-level alignment — word_timings is empty.
    # The video assembly falls back to even image slots; blackscreen beats
    # are skipped. Can be revisited if a forced-aligner is added later.
    word_timings = []
    if with_captions:
        timings_path.write_text(json.dumps(word_timings))

    mins = _audio_duration(out_path) / 60
    print(f"    Audio: {mins:.1f} min → {out_path.name}")
    return (out_path, word_timings) if with_captions else out_path
