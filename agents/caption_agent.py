"""Word-by-word animated captions with keyword highlighting + reflective
black-screen beats.

Pipeline:
1. annotate_script() — one Haiku pass over the polished script marks ~10-15%
   of words as highlight keywords (**word**) and wraps 2-4 short reflective
   sentences (babak 2/4 material) in [[BLACK]]...[[/BLACK]]. Markers are
   stripped before the clean text goes to TTS.
2. tts_agent.generate_audio(..., with_timestamps=True) returns per-word
   start/end times (see tts_agent.py) aligned to the SAME clean-text word
   sequence produced here.
3. build_ass() zips word timings + highlight/blackscreen flags into a karaoke-
   style .ass file (one short dialogue event per word) for assembly_agent to
   burn into the final video.
4. blackscreen_spans() merges contiguous flagged words into (start, end)
   ranges so assembly_agent can swap the slideshow to black during those beats.
"""
import json
import re

import anthropic
from config import ANTHROPIC_API_KEY, HAIKU_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

HIGHLIGHT_COLOR_ASS = "&H1CB8FF&"  # BGR hex for the same yellow/orange used in thumbnails

_ANNOTATE_SYSTEM = """You annotate a psychology-essay video script for caption rendering. You do \
NOT change any wording — only add two kinds of markers around EXISTING text, verbatim.

1. Wrap roughly 10-15% of words — the emotionally/conceptually load-bearing ones (not function \
words like "the/a/and") — in double asterisks, e.g. **exhausted**. Spread these across the whole \
script, a few per sentence at most, never two in a row.

2. Find 2-4 short, already-existing sentences that work well as a quiet "let this sink in" \
reflective beat (these should come from the wound section or the turning-point section of the \
script, never the hook or the closing strategy), and wrap each ENTIRE sentence in [[BLACK]] and \
[[/BLACK]], e.g. [[BLACK]]You learned to disappear before anyone could leave first.[[/BLACK]]. A \
sentence can have both markers (highlighted words inside a [[BLACK]] sentence) if it naturally does.

Do not add, remove, or reorder any words. Output the full script text with only these markers added."""


def annotate_script(script_text):
    """Returns (clean_text, words) where `words` is a list of dicts:
    {"text": str, "highlight": bool, "blackscreen": bool}, in the same order
    the words will be spoken — this order MUST match what's sent to TTS."""
    msg = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=8000,
        system=_ANNOTATE_SYSTEM,
        messages=[{"role": "user", "content": script_text}],
    )
    annotated = msg.content[0].text.strip()

    words = []
    clean_parts = []
    in_black = False
    # Split on markers while keeping them, so we can track [[BLACK]] state
    # across word boundaries that fall inside a wrapped sentence.
    tokens = re.split(r"(\[\[BLACK\]\]|\[\[/BLACK\]\]|\*\*[^*]+\*\*)", annotated)
    for tok in tokens:
        if tok == "[[BLACK]]":
            in_black = True
            continue
        if tok == "[[/BLACK]]":
            in_black = False
            continue
        m = re.match(r"\*\*([^*]+)\*\*$", tok)
        if m:
            for w in m.group(1).split():
                words.append({"text": w, "highlight": True, "blackscreen": in_black})
            clean_parts.append(m.group(1))
            continue
        for w in tok.split():
            words.append({"text": w, "highlight": False, "blackscreen": in_black})
        clean_parts.append(tok)

    clean_text = "".join(clean_parts)
    # Normalize whitespace the same way TTS chunking does, so chunk text and
    # this word list stay in lockstep.
    clean_text = re.sub(r"[ \t]+", " ", clean_text).strip()
    return clean_text, words


def blackscreen_spans(word_timings, merge_gap=0.5):
    """word_timings: list of {"text", "start", "end", "highlight", "blackscreen"}.
    Returns merged [(start, end), ...] for contiguous/near-contiguous
    blackscreen-flagged words."""
    spans = []
    current = None
    for w in word_timings:
        if w["blackscreen"]:
            if current and w["start"] - current[1] <= merge_gap:
                current = (current[0], w["end"])
            else:
                if current:
                    spans.append(current)
                current = (w["start"], w["end"])
        else:
            if current:
                spans.append(current)
                current = None
    if current:
        spans.append(current)
    return spans


def _ass_header():
    # Fontname below ("Lato") must match the TTF's internal family name —
    # libass matches by family, not filename. Confirm with:
    #   fc-scan --format '%{family}\n' assets/fonts/Lato-Regular.ttf
    # before the first real render.
    return """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Lato,84,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,2,80,80,80,1
Style: WordHi,Lato,84,""" + HIGHLIGHT_COLOR_ASS + """,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,2,80,80,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _fmt_ts(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:01d}:{m:02d}:{s:05.2f}"


def build_ass(word_timings, out_path, words_on_screen=4):
    """Writes an .ass file: a short rolling window of `words_on_screen` words
    is shown at a time (Kee-style word-by-word reveal), current/recent
    highlighted words rendered in the WordHi style."""
    lines = [_ass_header()]
    n = len(word_timings)
    for i, w in enumerate(word_timings):
        window = word_timings[max(0, i - words_on_screen + 1): i + 1]
        text = " ".join(
            (f"{{\\c{HIGHLIGHT_COLOR_ASS}}}" + wd["text"] + "{\\c&H00FFFFFF&}") if wd["highlight"] else wd["text"]
            for wd in window
        )
        style = "Word"
        lines.append(
            f"Dialogue: 0,{_fmt_ts(w['start'])},{_fmt_ts(w['end'])},{style},,0,0,0,,{text}"
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
