import base64
import hashlib
import json
import re
import struct
import subprocess
import shutil
import time
import requests
from config import (
    OUTPUT_DIR, ELEVENLABS_API_KEY, ELEVENLABS_MODEL, ELEVENLABS_OUTPUT_FORMAT,
    ELEVENLABS_VOICE_ID, ELEVENLABS_VOICE_SETTINGS, FFMPEG_BIN, FFPROBE_BIN,
)

SILENCE_MS = 450          # gap between ordinary script chunks
MAX_VOLUME_RANGE_DB = 9  # if segments within a chunk differ more than this, re-roll the chunk
QUALITY_RETRIES = 2

# ElevenLabs' own docs: keep generations under 800-900 chars for expressive,
# dynamic delivery — longer single generations flatten toward monotone. Same
# value Narava settled on (see project memory project_narava_pipeline.md) —
# chunking down further to ~1-2 sentences is safe and gives a silence gap
# between most sentences instead of only every 4-6. Billing is per character
# regardless of chunk count.
MAX_CHUNK_CHARS = 220

_IS_PCM = ELEVENLABS_OUTPUT_FORMAT.startswith("pcm")
_AUDIO_EXT = "wav" if _IS_PCM else "mp3"
_AUDIO_CODEC = "pcm_s16le" if _IS_PCM else "libmp3lame"
_CODEC_ARGS = ["-c:a", _AUDIO_CODEC] if _IS_PCM else ["-c:a", _AUDIO_CODEC, "-b:a", "192k"]


def _chunk_text(text, max_chars=MAX_CHUNK_CHARS):
    sentences = text.replace("\n\n", " ").replace("\n", " ").split(". ")
    chunks, current = [], ""
    for s in sentences:
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


def _pcm_to_wav(pcm_bytes, sample_rate=44100, channels=1, bits_per_sample=16):
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_bytes)
    header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
    header += b"data" + struct.pack("<I", data_size)
    return header + pcm_bytes


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


def _call_tts(text, voice_id, voice_settings, with_timestamps=False):
    """Single API call. Returns (audio_bytes, alignment_or_None). audio_bytes
    is a complete, playable file's bytes (WAV if ELEVENLABS_OUTPUT_FORMAT is
    PCM, raw MP3 bytes otherwise)."""
    path_suffix = "/with-timestamps" if with_timestamps else ""
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}{path_suffix}",
        params={"output_format": ELEVENLABS_OUTPUT_FORMAT},
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": ELEVENLABS_MODEL,
            "voice_settings": voice_settings,
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:300]}")

    if with_timestamps:
        data = resp.json()
        audio_bytes = base64.b64decode(data["audio_base64"])
        alignment = data.get("alignment")
    else:
        audio_bytes = resp.content
        alignment = None

    return (_pcm_to_wav(audio_bytes) if _IS_PCM else audio_bytes), alignment


def _synthesize_chunk(text, out_path, voice_id, voice_settings, capture_timestamps=False,
                       max_api_retries=5, max_quality_retries=QUALITY_RETRIES):
    """Save the RAW chunk as-is — no per-chunk loudnorm. Loudness normalization
    runs once on the full merged file instead; per-chunk normalization on
    clips this short (under ITU-R BS.1770's reliable measurement window)
    produced audible level jumps between chunks once stitched together."""
    delays = [10, 30, 60, 120, 180]
    api_attempt = 0
    quality_attempt = 0
    alignment = None

    while True:
        try:
            audio_bytes, alignment = _call_tts(text, voice_id, voice_settings, with_timestamps=capture_timestamps)
        except Exception as api_err:
            err_text = str(api_err).lower()
            if any(kw in err_text for kw in (
                "quota", "credit", "insufficient", "payment",
                "subscription_required", "not_allowed", "tier",
            )):
                raise RuntimeError(f"TTS quota/credit/plan error (not retrying): {api_err}")
            wait = delays[min(api_attempt, len(delays) - 1)]
            print(f"    API error attempt {api_attempt+1}/{max_api_retries}: {api_err} — wait {wait}s")
            api_attempt += 1
            if api_attempt >= max_api_retries:
                raise RuntimeError(f"TTS API failed after {max_api_retries} attempts: {api_err}")
            time.sleep(wait)
            continue

        out_path.write_bytes(audio_bytes)
        if alignment is not None:
            out_path.with_suffix(".json").write_text(json.dumps(alignment))

        ok, spread = _is_consistent(out_path)
        if ok:
            return

        quality_attempt += 1
        if quality_attempt >= max_quality_retries:
            print(f"    Volume drift {spread:.1f}dB persists after {max_quality_retries} re-rolls — accepting best take")
            return
        print(f"    Volume drift {spread:.1f}dB detected — re-rolling chunk ({quality_attempt}/{max_quality_retries})...")


def _merge_with_ffmpeg(chunk_dir, out_path):
    chunks = sorted(chunk_dir.glob(f"[0-9][0-9][0-9][0-9].{_AUDIO_EXT}"))

    def _make_silence(ms, name):
        path = chunk_dir / name
        subprocess.run([
            FFMPEG_BIN, "-y", "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=mono",
            "-t", f"{ms / 1000:.3f}",
            *_CODEC_ARGS, str(path)
        ], capture_output=True, check=True)
        return path

    silence_path = _make_silence(SILENCE_MS, f"silence.{_AUDIO_EXT}")

    concat_file = chunk_dir / "concat.txt"
    lines = []
    for chunk in chunks:
        lines.append(f"file '{chunk}'")
        lines.append(f"file '{silence_path}'")
    concat_file.write_text("\n".join(lines))

    raw_merged = chunk_dir / f"merged_raw.{_AUDIO_EXT}"
    subprocess.run([
        FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        *_CODEC_ARGS,
        str(raw_merged)
    ], capture_output=True, check=True)

    # Single loudness normalization pass over the whole program — same reasoning
    # as Narava: far more reliable than many independent per-chunk passes.
    subprocess.run([
        FFMPEG_BIN, "-y", "-i", str(raw_merged),
        "-af", "loudnorm=I=-16:TP=-3.0:LRA=20",
        *_CODEC_ARGS, str(out_path)
    ], capture_output=True, check=True)


def _words_from_alignment(text, alignment):
    """Splits one chunk's character-level alignment into word-level (text,
    start, end), local to that chunk's own audio (0-based)."""
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    words = []
    cur_text, cur_start, cur_end = "", None, None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur_text:
                words.append({"text": cur_text, "start": cur_start, "end": cur_end})
                cur_text, cur_start, cur_end = "", None, None
            continue
        if cur_start is None:
            cur_start = s
        cur_text += ch
        cur_end = e
    if cur_text:
        words.append({"text": cur_text, "start": cur_start, "end": cur_end})
    return words


def _compute_word_timings(chunk_dir, chunks, highlight_words):
    """Combines each chunk's local alignment + cumulative chunk/silence
    offsets into one global word-timing list, then zips in the
    highlight/blackscreen flags from caption_agent.annotate_script (same word
    order — both derive from the exact same clean text)."""
    offset = 0.0
    silence_dur = SILENCE_MS / 1000
    all_words = []
    for i, chunk_text in enumerate(chunks):
        audio_path = chunk_dir / f"{i:04d}.{_AUDIO_EXT}"
        align_path = audio_path.with_suffix(".json")
        if align_path.exists():
            alignment = json.loads(align_path.read_text())
            for w in _words_from_alignment(chunk_text, alignment):
                all_words.append({"text": w["text"], "start": w["start"] + offset, "end": w["end"] + offset})
        offset += _audio_duration(audio_path) + silence_dur

    n = min(len(all_words), len(highlight_words))
    if len(all_words) != len(highlight_words):
        print(f"    Warning: word count mismatch (audio={len(all_words)}, script={len(highlight_words)}) — captions truncated to {n}")
    timings = []
    for i in range(n):
        timings.append({
            "text": all_words[i]["text"],
            "start": all_words[i]["start"],
            "end": all_words[i]["end"],
            "highlight": highlight_words[i]["highlight"],
            "blackscreen": highlight_words[i]["blackscreen"],
        })
    return timings


def generate_audio(script_text, topic_id, category=None, with_captions=False, highlight_words=None):
    """If with_captions=True, returns (audio_path, word_timings) — requires
    highlight_words from agents.caption_agent.annotate_script(), in the exact
    same word order as script_text. Otherwise returns just audio_path."""
    voice_id, voice_settings = ELEVENLABS_VOICE_ID, ELEVENLABS_VOICE_SETTINGS

    chunks = _chunk_text(script_text)
    total_chars = sum(len(c) for c in chunks)
    quality_label = "studio-quality PCM, no compression" if _IS_PCM else "mp3 192kbps (PCM needs ElevenLabs Pro tier)"
    print(f"    TTS: {len(chunks)} chunks, ~{total_chars:,} chars")
    print(f"    Voice settings: {ELEVENLABS_MODEL}, {quality_label}")

    chunk_dir = OUTPUT_DIR / "audio" / f"chunks_{topic_id}"
    script_hash = hashlib.md5(script_text.encode()).hexdigest()[:8]
    hash_file = chunk_dir / ".script_hash"

    if chunk_dir.exists() and hash_file.exists():
        if hash_file.read_text().strip() != script_hash:
            print(f"    Script changed — clearing cached chunks")
            shutil.rmtree(chunk_dir)

    chunk_dir.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(script_hash)

    for i, chunk in enumerate(chunks):
        out = chunk_dir / f"{i:04d}.{_AUDIO_EXT}"
        if out.exists() and out.stat().st_size > 0 and (not with_captions or out.with_suffix(".json").exists()):
            print(f"    TTS: {i+1}/{len(chunks)} (cached)")
            continue
        _synthesize_chunk(chunk, out, voice_id, voice_settings, capture_timestamps=with_captions)
        print(f"    TTS: {i+1}/{len(chunks)} done")
        time.sleep(1)

    word_timings = None
    if with_captions:
        word_timings = _compute_word_timings(chunk_dir, chunks, highlight_words)

    print("    Merging audio...")
    out_path = OUTPUT_DIR / "audio" / f"{topic_id}.{_AUDIO_EXT}"
    _merge_with_ffmpeg(chunk_dir, out_path)
    shutil.rmtree(chunk_dir)

    mins = _audio_duration(out_path) / 60
    print(f"    Audio: {mins:.0f} min → {out_path.name}")
    return (out_path, word_timings) if with_captions else out_path
