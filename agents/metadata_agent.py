import agents.llm as _llm

CHANNEL_URL = "https://www.youtube.com/@heyapophenia"


def _timestamp_marks(duration_min):
    """Returns just the time codes for the script's fixed 5-babak structure (see
    script_agent._DRAFT) — proportional to each babak's typical share of runtime
    (wound and strategy/close run longest per that prompt's own pacing note).

    Labels are NOT generated here anymore. Generic template labels ("The Hook",
    "The Wound", "The Science"...) were identical across every video regardless
    of actual content — the LLM now writes a contextual title per mark based on
    that specific script (see _PROMPT's TIMESTAMPS section)."""
    weights = [0.08, 0.32, 0.18, 0.14, 0.28]
    marks, acc = [], 0.0
    for w in weights:
        marks.append(acc)
        acc += w

    def fmt(total_min):
        total_sec = int(total_min * 60)
        h, rem = divmod(total_sec, 3600)
        mn, sec = divmod(rem, 60)
        return f"{h}:{mn:02d}:{sec:02d}" if h else f"{mn:02d}:{sec:02d}"

    return [fmt(duration_min * m) for m in marks]

_PROMPT = """You are an expert YouTube SEO strategist for a psychology insight essay channel \
called "Apophenia" (style reference: the channel "Kee" — generic psychological archetypes, \
real psychological terms explained conversationally, never academic, never naming real people).

Your job: generate a highly optimized title, description, and tags for this video.

Archetype: {topic}
Title angle: {angle}
Internal category tag (psych mechanism, do not say verbatim unless natural): {category}
Duration: ~{duration_min} minutes

SEO RULES — follow strictly:

TITLE rules (updated 2026-07-13 — dropped the philosopher-attribution formula: it read as a fake
quote falsely attributed to a real historical figure, not just a stylistic flourish; removed rather
than softened):
- 60-100 characters
- Use ONE of these two proven Psyphoria title formulas:
    B) "How to [specific action] [meaningful result]"
       e.g. "How to Stop Performing Confidence and Actually Feel It"
       e.g. "How to Trick Your Brain Into Enjoying Hard Things"
    C) "When You [action], [positive result]"
       e.g. "When You Stop Apologizing, Everything Changes"
       e.g. "When You Stop Caring So Much, Everything Falls Into Place"
- NEVER attribute any part of the title to a real person's name (no philosopher, thinker, or public
  figure name-drop, "– [Name]" suffix, or implied quote) — the archetype and angle must carry the
  title on their own
- TITLE_A and TITLE_B must use DIFFERENT formulas (one uses B, the other uses C)
- Sharp, personal, specific — pull from the angle field's most recognizable behavioral detail
- NO "The Psychology of" prefix (retired) — NO ALL CAPS — NO emojis — NO real person's name in the
  topic itself — NO false/misleading claims
- Naturally work in the core searchable keyword from the category (e.g. "people pleasing", "fawn
  response", "ego") inside the title for SEO — don't sacrifice searchability for cleverness

DESCRIPTION rules:
- First 125 characters are critical — shown in YouTube search snippets, must be a calm, specific
  hook that matches "why am I like this" search intent
- Naturally weave in: "psychology of {topic_lower}", "{category_words} psychology", "self awareness"
- Write like a human essayist, not a bot — specific and emotionally precise, not generic
  self-help language
- Structure: hook line → what pattern this video explores (2-3 sentences) → the real
  psychological mechanism named in plain language → who it's for → subscribe CTA → disclosure
- Mention the channel handles archetypes, never real people or diagnoses

TIMESTAMPS rules (changed 2026-07-02 — labels were a fixed generic template
"The Hook / The Wound / The Science / The Turn / The Strategy" reused verbatim
on every single video regardless of content, telling the viewer nothing about
THIS video specifically):
- You are given the exact time codes below and the full script text
- Write ONE short chapter title (2-5 words, Title Case, no colon) per time code —
  each title must describe what is ACTUALLY said in that part of THIS script,
  not a generic structural label
- Example (for a script about the fake-confidence adult): "0:00 – Why You
  Sound Confident but Say Sorry" / "0:35 – Where the Mask Started" / "3:10 –
  The Real Mechanism: Learned Performance" / "6:40 – What the Mask Protects" /
  "8:20 – Dropping the Performance" — specific to that video, never reused
  wholesale on a different topic
- Time codes to use (do not change these):
{timestamp_marks}

Script for this video (write timestamp titles based on what actually happens
in each section, in this reading order):
\"\"\"
{script}
\"\"\"

TAGS rules:
- 15 tags total
- Mix: 3 ultra-broad ("psychology", "self awareness", "mental health"), 6 mid-tail ("attachment
  theory", "emotional healing", "trauma patterns", "{category_words} psychology", "psychology of
  relationships", "why am i like this"), 6 long-tail specific to this exact archetype and angle
- Order: most important first

Output EXACTLY this format, nothing else:

TITLE_A: [your primary optimized title]
TITLE_B: [alternate title — the OTHER formula from A (if A used formula B, use C, and vice versa); \
still 60-100 characters, different curiosity angle from A, equally clickable]

DESCRIPTION:
[First line: 125-char hook matching search intent]

[2-3 sentences: what pattern this video explores, in plain conversational language]

[1-2 sentences: the real psychological mechanism, named and explained simply]

[1 sentence: who this is for]

[Timestamp block: one "TIME – Contextual Title" line per time code given above, in order]

More psychology breakdowns:
▶ Full Channel → {channel_url}

New video twice a week. Subscribe so you never miss one.

This video explores a generic psychological archetype, not any real individual — any
resemblance to a specific person is coincidental. Not a substitute for therapy or professional
mental health care. Narration and illustrations are AI-assisted; psychological research, script
structure, and final editing are human-curated.

#Psychology #SelfAwareness #AttachmentTheory #MentalHealth #EmotionalHealing #{category_tag}Psychology #PsychologyFacts #WhyAmILikeThis #TraumaHealing #PersonalGrowth

TAGS: [15 tags comma-separated, most important first]"""


def generate_metadata(topic_data, script, duration_min=15, retries=3):
    category = topic_data["category"]
    category_words = category.replace("-", " ")
    category_tag = "".join(w.capitalize() for w in category.split("-"))
    marks_str = "\n".join(_timestamp_marks(duration_min))
    for attempt in range(retries):
        result = _generate_metadata_once(
            topic_data, script, duration_min, category, category_words, category_tag, marks_str,
        )
        if result["title_a"]:
            return result
        print(f"    Metadata came back with an empty title (attempt {attempt+1}/{retries}) — retrying...")
    # Confirmed 2026-07-10: an empty LLM response (no TITLE_A: line at all)
    # parsed silently into blank title/description/tags with no exception —
    # the pipeline then uploaded to YouTube with a blank title before this
    # was caught. Failing loud here lets scheduler.py's normal retry-next-run
    # behavior kick in instead of publishing a titleless video.
    raise RuntimeError(f"generate_metadata: title_a still empty after {retries} attempts")


def _generate_metadata_once(topic_data, script, duration_min, category, category_words, category_tag, marks_str):
    text = _llm.call(_PROMPT.format(
        topic=topic_data["topic"],
        topic_lower=topic_data["topic"].lower(),
        angle=topic_data["angle"],
        category=category,
        category_words=category_words,
        category_tag=category_tag,
        channel_url=CHANNEL_URL,
        duration_min=duration_min,
        timestamp_marks=marks_str,
        script=script,
    ), max_tokens=2400)
    title_a, title_b, description, tags = "", "", "", []
    lines = text.strip().split("\n")
    mode = None
    desc_lines = []

    for line in lines:
        if line.startswith("TITLE_A:"):
            title_a = line.replace("TITLE_A:", "").strip()[:100]
            mode = "title"
        elif line.startswith("TITLE_B:"):
            title_b = line.replace("TITLE_B:", "").strip()[:100]
            mode = "title"
        elif line.startswith("TITLE:"):
            # backward compat if model ignores new format
            title_a = line.replace("TITLE:", "").strip()[:100]
            mode = "title"
        elif line.startswith("DESCRIPTION:"):
            mode = "description"
        elif line.startswith("TAGS:"):
            description = "\n".join(desc_lines).strip()
            raw_tags = line.replace("TAGS:", "").strip()
            tags = [t.strip() for t in raw_tags.split(",")][:15]
            mode = "tags"
        elif mode == "description":
            desc_lines.append(line)

    if not description:
        description = "\n".join(desc_lines).strip()
    if not title_b:
        title_b = title_a  # fallback: same title if model didn't produce B

    return {"title": title_a, "title_a": title_a, "title_b": title_b, "description": description, "tags": tags}


_ENGAGEMENT_QUESTION_PROMPT = """You write a single short comment for a psychology-essay YouTube video, \
posted by the channel itself right after upload to invite replies. Added 2026-06-24 after performance \
feedback: high-retention videos on sensitive topics (family estrangement, etc.) were getting zero \
comments — likely because viewers feel vulnerable sharing a personal story unprompted.

The comment must be LOW-STAKES: not "share your story" (too vulnerable, too open-ended), but a quick, \
non-identifying multiple-choice-style question that's easy to answer in three words, e.g. "Which of \
these resonated most — enmeshment or hypervigilance?" Pull the two concrete concepts/terms from the \
angle given (the actual psychological mechanisms this specific video names), not generic ones.

Given an archetype and its angle, return ONLY the comment text itself (one sentence, no quotes, no \
preamble, under 20 words)."""


def generate_engagement_question(topic_data):
    text = _llm.call(
        f"Archetype: {topic_data['topic']}\nAngle: {topic_data['angle']}",
        system=_ENGAGEMENT_QUESTION_PROMPT,
        max_tokens=100,
    )
    return text.strip('"')
