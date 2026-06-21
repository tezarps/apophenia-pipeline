import math
import shutil
import subprocess
from pathlib import Path
from config import IMAGES_DIR, OUTPUT_DIR, FFMPEG_BIN, FFPROBE_BIN

FPS = 25
SLIDE_DURATION = 10  # seconds per image (Ken Burns)

ASSETS_DIR = Path(__file__).parent.parent / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
FONTS_DIR = ASSETS_DIR / "fonts"

# Thumbnails are NOT handled here — see agents/thumbnail_agent.py for the
# Kee-style character+hook-text composition. This module is the video
# assembly mechanics: Ken Burns slideshow, audio mix, reflective black-screen
# beats (agents/caption_agent.blackscreen_spans), and caption burn-in
# (agents/caption_agent.build_ass).


def _get_music(category):
    """Find music file for category — supports .wav and .mp3."""
    for ext in (".wav", ".mp3"):
        path = MUSIC_DIR / f"{category.lower()}{ext}"
        if path.exists():
            return path
    return None


def _get_images(category, topic_slug=None, count=10):
    candidates = []
    if topic_slug:
        topic_dir = IMAGES_DIR / category.lower() / topic_slug.lower()
        if topic_dir.exists():
            candidates = sorted(topic_dir.glob("*.jpg")) + sorted(topic_dir.glob("*.png")) + sorted(topic_dir.glob("*.jpeg"))
    if not candidates:
        cat_dir = IMAGES_DIR / category.lower()
        if cat_dir.exists():
            candidates = sorted(cat_dir.glob("*.jpg")) + sorted(cat_dir.glob("*.png")) + sorted(cat_dir.glob("*.jpeg"))
    if not candidates:
        candidates = sorted(IMAGES_DIR.glob("*.jpg")) + sorted(IMAGES_DIR.glob("*.png"))
    if not candidates:
        raise FileNotFoundError(f"No images found in images/{category.lower()}/{topic_slug or ''}/")
    return [candidates[i % len(candidates)] for i in range(count)]


def _make_ken_burns_clip(img_path, out_path):
    """Generate a slide clip with a subtle, smooth zoom-in (Ken Burns).

    zoompan rounds the crop window to whole pixels every frame; with too
    little headroom between the pre-scaled source and the 1920x1080 output,
    that window barely moves frame-to-frame and the rounding shows up as
    visible stair-step shake. Pre-scaling the source far beyond what the
    zoom range needs gives the rounding enough room to land on a different
    pixel every frame, which is what actually reads as smooth.
    """
    d = SLIDE_DURATION * FPS
    t = f"min(on,{d-1})/{d-1}"
    ease = f"(3*pow({t},2)-2*pow({t},3))"

    z_max = 1990 / 1920
    z = f"1+{z_max - 1:.7f}*{ease}"

    pre_w, pre_h = 7680, 4320

    vf_parts = [
        f"scale={pre_w}:{pre_h}:force_original_aspect_ratio=increase",
        f"crop={pre_w}:{pre_h}",
        f"zoompan=z='{z}':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps={FPS}",
        "setsar=1",
    ]
    cmd = [
        FFMPEG_BIN, "-y",
        "-loop", "1", "-i", str(img_path),
        "-vf", ",".join(vf_parts),
        "-t", str(SLIDE_DURATION),
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Ken Burns failed for {img_path.name}: {result.stderr[-300:]}")


def _make_black_clip(out_path):
    cmd = [
        FFMPEG_BIN, "-y", "-f", "lavfi",
        "-i", f"color=c=black:s=1920x1080:r={FPS}",
        "-t", str(SLIDE_DURATION),
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Black clip failed: {result.stderr[-300:]}")


def _audio_duration(audio_path):
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def _slot_is_blackscreen(slot_start, slot_end, blackscreen_spans):
    if not blackscreen_spans:
        return False
    slot_dur = slot_end - slot_start
    overlap = 0.0
    for s, e in blackscreen_spans:
        overlap += max(0.0, min(slot_end, e) - max(slot_start, s))
    return overlap >= slot_dur * 0.5


def create_video(audio_path, category, topic_id, topic_slug=None, blackscreen_spans=None, subtitles_path=None):
    images = _get_images(category, topic_slug, count=10)
    out_path = OUTPUT_DIR / "video" / f"{topic_id}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    duration = _audio_duration(audio_path)

    clips_dir = out_path.parent / f"{topic_id}_clips"
    clips_dir.mkdir(exist_ok=True)

    def _clip_is_valid(path):
        if not path.exists() or path.stat().st_size == 0:
            return False
        try:
            return _audio_duration(path) >= SLIDE_DURATION - 1
        except subprocess.CalledProcessError:
            return False

    clip_paths = []
    for i, img in enumerate(images):
        clip_out = clips_dir / f"clip_{i:02d}.mp4"
        if not _clip_is_valid(clip_out):
            print(f"    Ken Burns clip {i+1}/{len(images)}...")
            _make_ken_burns_clip(img, clip_out)
        clip_paths.append(clip_out)

    black_clip = clips_dir / "black.mp4"
    if blackscreen_spans and not _clip_is_valid(black_clip):
        _make_black_clip(black_clip)

    # Time-driven slot schedule (not a pure repeating cycle) so reflective
    # beats (agents/caption_agent.blackscreen_spans) land on the right slide
    # instead of whichever image the cycle happens to be on.
    num_slots = math.ceil(duration / SLIDE_DURATION)
    concat_lines = []
    for s in range(num_slots):
        slot_start, slot_end = s * SLIDE_DURATION, (s + 1) * SLIDE_DURATION
        if _slot_is_blackscreen(slot_start, slot_end, blackscreen_spans):
            concat_lines.append(f"file '{black_clip.resolve()}'")
        else:
            concat_lines.append(f"file '{clip_paths[s % len(clip_paths)].resolve()}'")
    concat_file = out_path.parent / f"{topic_id}_concat.txt"
    concat_file.write_text("\n".join(concat_lines))

    music_path = _get_music(category)
    pre_subtitle_path = out_path if not subtitles_path else out_path.parent / f"{topic_id}_nosubs.mp4"

    if music_path:
        af = (
            "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=1.0[narration];"
            f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=0.30,aloop=loop=-1:size=2e+09[music];"
            "[narration][music]amix=inputs=2:duration=first:dropout_transition=3[aout]"
        )
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-i", str(audio_path),
            "-i", str(music_path),
            "-c:v", "copy",
            "-filter_complex", af,
            "-map", "0:v", "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(pre_subtitle_path),
        ]
        print(f"    Assembling video + music ({category})...")
    else:
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(pre_subtitle_path),
        ]
        print(f"    Assembling video (no music for {category})...")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {result.stderr[-600:]}")

    if subtitles_path:
        print(f"    Burning captions...")
        ass_escaped = str(subtitles_path).replace("\\", "/").replace(":", "\\:")
        fonts_escaped = str(FONTS_DIR).replace("\\", "/").replace(":", "\\:")
        cmd_sub = [
            FFMPEG_BIN, "-y", "-i", str(pre_subtitle_path),
            "-vf", f"subtitles={ass_escaped}:fontsdir={fonts_escaped}",
            "-c:a", "copy",
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        result_sub = subprocess.run(cmd_sub, capture_output=True, text=True)
        if result_sub.returncode != 0:
            raise RuntimeError(f"Caption burn-in failed: {result_sub.stderr[-600:]}")
        pre_subtitle_path.unlink(missing_ok=True)

    concat_file.unlink(missing_ok=True)
    shutil.rmtree(clips_dir)

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"    Video: {size_mb:.0f}MB, {duration/60:.0f} min → {out_path.name}")
    return out_path, duration
