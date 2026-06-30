"""Cuts a YouTube Short out of the already-assembled main video, treated
DIFFERENTLY from the main upload on purpose — its only job is to hook someone
scrolling Shorts and push them to the full video, not to tell the whole story.

Reuses the main video.mp4 directly (already has narration + music mixed in,
and burned captions if BURN_IN_SUBTITLES is on) rather than re-rendering Ken
Burns clips from scratch — far cheaper and the hook section already looks
right. Steps:
1. Cut the first ~SHORT_TARGET_SEC of narration, snapped to the nearest
   sentence boundary (word_timings) so it doesn't cut off mid-word.
2. Crop 16:9 -> 9:16 (scale up to height 1920, crop center 1080 width).
3. Burn in word-by-word karaoke captions for the WHOLE clip (one word on
   screen at a time, synced to word_timings) — Shorts are watched muted by
   default far more than long-form, so captions need to run the entire time
   the narrator is talking, not just appear at the end. Word captions stop
   right as the CTA window starts so the two never overlap.
4. Burn in a centered CTA caption for the final CTA_WINDOW_SEC seconds,
   pointing at the full video — this is the entire point of the Short.

Both are rendered via one .ass subtitle burn (libass), not ffmpeg drawtext —
drawtext's filter-string escaping mangled a literal arrow character into the
text "u2192" on screen (confirmed 2026-06-22, Topic #4's first short). ASS
also matches the channel's vintage-comic identity (Bebas Neue, same font as
the thumbnails) and gives a proper black outline for guaranteed readability
over any background. Word captions sit center-low; the CTA sits center —
placed mid-frame rather than near the bottom third, which Shorts' own UI
(caption/like/share column) covers anyway.
"""
import subprocess
from pathlib import Path
from config import OUTPUT_DIR, FFMPEG_BIN

SHORT_TARGET_SEC = 50  # aim for ~50s hook, snapped to the nearest sentence end
SHORT_MAX_SEC = 58     # hard ceiling — YouTube Shorts must be <=60s to be treated as a Short
CTA_WINDOW_SEC = 4.5   # how long the "watch full video" card overlays the end of the clip

ASSETS_DIR = Path(__file__).parent.parent / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
CTA_LINE_1 = "WANT THE FULL BREAKDOWN?"
CTA_LINE_2 = "FULL VIDEO → LINK IN DESCRIPTION"
# Added 2026-06-23, content #5 onward — Topic #4 was already rendered and
# left as-is to avoid a costly re-render just for this. A third, smaller
# line under the existing CTA asking for the standard engagement actions;
# same window/timing as CTA_LINE_1/2, no separate fade needed.
CTA_LINE_3 = "LIKE • COMMENT • SHARE • SUBSCRIBE"
WORD_HIGHLIGHT_COLOR_ASS = "&H1CB8FF&"  # same yellow/orange as the main video's captions


def _nearest_sentence_end(word_timings, target_sec):
    """Snaps target_sec to the end of whichever sentence is closest to it,
    so the Short doesn't cut off mid-sentence. Falls back to target_sec
    itself if word_timings is empty."""
    if not word_timings:
        return target_sec
    import re
    ends = [w["end"] for w in word_timings if re.search(r'[.!?]["\')]?$', w["text"])]
    if not ends:
        return min(target_sec, word_timings[-1]["end"])
    candidates = [e for e in ends if e <= SHORT_MAX_SEC] or ends
    return min(candidates, key=lambda e: abs(e - target_sec))


def _fmt_ts(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:01d}:{m:02d}:{s:05.2f}"


def _build_short_ass(word_timings, clip_end, cta_start, cta_end, out_path):
    """One .ass file, two layers of burned text:
    - Word-by-word karaoke captions (one word on screen at a time, same
      mechanic as agents/caption_agent.build_ass for the main video) for
      every word that starts before cta_start — so they run continuously
      while the narrator talks and stop cleanly right as the CTA appears,
      never overlapping it.
    - The 2-line CTA card for [cta_start, cta_end].
    Vertical canvas (1080x1920). Words sit lower-middle (MarginV=420) —
    low enough to read naturally, high enough to clear Shorts' own bottom
    UI strip. CTA sits mid-frame via \\pos, same as before."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Bebas Neue,70,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,5,0,2,60,60,420,1
Style: WordHi,Bebas Neue,70,&H001CB8FF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,5,0,2,60,60,420,1
Style: CTA1,Bebas Neue,84,&H00F2EEDE,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,7,0,5,60,60,40,1
Style: CTA2,Bebas Neue,62,&H00F2EEDE,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,6,0,5,60,60,40,1
Style: CTA3,Bebas Neue,46,&H001CB8FF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,5,0,5,60,60,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for w in word_timings:
        if w["start"] >= cta_start:
            break
        end = min(w["end"], cta_start)
        if end <= w["start"]:
            continue
        style = "WordHi" if w.get("highlight") else "Word"
        text = w["text"].replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{_fmt_ts(w['start'])},{_fmt_ts(end)},{style},,0,0,0,,{text}")

    s, e = _fmt_ts(cta_start), _fmt_ts(cta_end)
    lines.append(f"Dialogue: 1,{s},{e},CTA1,,0,0,0,,{{\\pos(540,860)}}{CTA_LINE_1}")
    lines.append(f"Dialogue: 1,{s},{e},CTA2,,0,0,0,,{{\\pos(540,990)}}{CTA_LINE_2}")
    lines.append(f"Dialogue: 1,{s},{e},CTA3,,0,0,0,,{{\\pos(540,1100)}}{CTA_LINE_3}")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def generate_short(video_path, word_timings, topic_id, clip_start_sec=0.0, variant="hook"):
    """Cuts output/shorts/{topic_id}.mp4 (or {topic_id}_{variant}.mp4 for any
    non-"hook" variant) from `video_path` (the finished main video). Returns
    the output path.

    clip_start_sec added 2026-06-24 so a SECOND short can be cut from a
    video's conceptual "aha" turning point instead of always the opening
    hook — per Gemini performance feedback flagging the turning-point
    explanation as the strongest (underused) growth lever on a high-
    retention video. word_timings are shifted to be local to clip_start_sec
    before any of the existing hook/CTA timing math runs, so everything
    downstream (sentence-boundary snapping, caption sync, CTA window) works
    identically regardless of where in the main video the clip starts."""
    local_words = [
        {**w, "start": w["start"] - clip_start_sec, "end": w["end"] - clip_start_sec}
        for w in word_timings if w["start"] >= clip_start_sec
    ]

    clip_dur = _nearest_sentence_end(local_words, SHORT_TARGET_SEC)
    clip_dur = min(clip_dur, SHORT_MAX_SEC)

    suffix = "" if variant == "hook" else f"_{variant}"
    out_path = OUTPUT_DIR / "shorts" / f"{topic_id}{suffix}.mp4"
    cta_start = max(0.0, clip_dur - CTA_WINDOW_SEC)

    ass_path = OUTPUT_DIR / "shorts" / f"{topic_id}{suffix}_cta.ass"
    _build_short_ass(local_words, clip_dur, cta_start, clip_dur, ass_path)
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    fonts_escaped = str(FONTS_DIR).replace("\\", "/").replace(":", "\\:")

    vf = (
        f"scale=-2:1920,crop=1080:1920,"
        f"subtitles={ass_escaped}:fontsdir={fonts_escaped}"
    )

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", str(video_path),
        "-ss", f"{clip_start_sec:.2f}",
        "-t", f"{clip_dur:.2f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    ass_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"Shorts cut failed: {result.stderr[-600:]}")

    print(f"    Short ({variant}): {clip_dur:.0f}s from t={clip_start_sec:.0f}s, CTA from {cta_start:.0f}s -> {out_path.name}")
    return out_path
