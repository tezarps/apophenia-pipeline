import anthropic
from config import ANTHROPIC_API_KEY, HAIKU_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

CHANNEL_URL = "https://www.youtube.com/@heyapophenia"


def _timestamp_block(duration_min):
    """Maps the script's fixed 5-babak structure (see script_agent._DRAFT) to
    rough proportional timestamps — wound (babak 2) and strategy/close (babak 5)
    run longest per that prompt's own pacing note, so they get the biggest share."""
    labels = ["The Hook", "The Wound", "The Science", "The Turn", "The Strategy"]
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

    return "\n".join(f"{fmt(duration_min * m)} – {l}" for m, l in zip(marks, labels))

_PROMPT = """You are an expert YouTube SEO strategist for a psychology insight essay channel \
called "Apophenia" (style reference: the channel "Kee" — generic psychological archetypes, \
real psychological terms explained conversationally, never academic, never naming real people).

Your job: generate a highly optimized title, description, and tags for this video.

Archetype: {topic}
Title angle: {angle}
Internal category tag (psych mechanism, do not say verbatim unless natural): {category}
Duration: ~{duration_min} minutes

SEO RULES — follow strictly:

TITLE rules (changed 2026-06-22 — titles were testing too generic/flat, need real click-pull):
- 60-100 characters
- Lead with "The Psychology of" or "The Psychology Of" followed by the archetype description —
  this exact phrase is the channel's proven high-performing format, but the archetype description
  itself must now be SPECIFIC and PUNCHY, not a flat clinical restatement — pull from the angle
  field's sharpest, most personally-recognizable detail (reference good titles: "The Psychology of
  People Who Go Quiet the Second a Room Fills Up", "The Psychology of People Who Apologize Before
  Anyone Else Even Speaks" — specific, second-person-recognizable, curiosity-inducing; bad/too
  generic: "The Psychology of the Emotionally Exhausted Adult")
- Allowed and encouraged: a sharp behavioral hook, a "why you..." or "the real reason..." framing
  worked into the archetype description itself, mild tension/curiosity gap — this IS the clickbait
  lever, just without leaving the "The Psychology of" SEO anchor phrase
- Still NO ALL CAPS, NO emojis, no real person's name, no false/misleading claims — the hook must
  be something the video genuinely delivers on, not bait-and-switch
- Naturally include the core searchable keyword from the category (e.g. "invisible", "people
  pleasing", "avoidant") inside the title itself — SEO and clickbait both matter, don't sacrifice
  one for the other

DESCRIPTION rules:
- First 125 characters are critical — shown in YouTube search snippets, must be a calm, specific
  hook that matches "why am I like this" search intent
- Naturally weave in: "psychology of {topic_lower}", "{category_words} psychology", "self awareness"
- Write like a human essayist, not a bot — specific and emotionally precise, not generic
  self-help language
- Structure: hook line → what pattern this video explores (2-3 sentences) → the real
  psychological mechanism named in plain language → who it's for → subscribe CTA → disclosure
- Mention the channel handles archetypes, never real people or diagnoses

TAGS rules:
- 15 tags total
- Mix: 3 ultra-broad ("psychology", "self awareness", "mental health"), 6 mid-tail ("attachment
  theory", "emotional healing", "trauma patterns", "{category_words} psychology", "psychology of
  relationships", "why am i like this"), 6 long-tail specific to this exact archetype and angle
- Order: most important first

Output EXACTLY this format, nothing else:

TITLE: [your optimized title]

DESCRIPTION:
[First line: 125-char hook matching search intent]

[2-3 sentences: what pattern this video explores, in plain conversational language]

[1-2 sentences: the real psychological mechanism, named and explained simply]

[1 sentence: who this is for]

{timestamp_block}

More psychology breakdowns:
▶ Full Channel → {channel_url}

New video twice a week. Subscribe so you never miss one.

This video explores a generic psychological archetype, not any real individual — any
resemblance to a specific person is coincidental. Not a substitute for therapy or professional
mental health care. Narration and illustrations are AI-assisted; psychological research, script
structure, and final editing are human-curated.

#Psychology #SelfAwareness #AttachmentTheory #MentalHealth #EmotionalHealing #{category_tag}Psychology #PsychologyFacts #WhyAmILikeThis #TraumaHealing #PersonalGrowth

TAGS: [15 tags comma-separated, most important first]"""


def generate_metadata(topic_data, duration_min=15):
    category = topic_data["category"]
    category_words = category.replace("-", " ")
    category_tag = "".join(w.capitalize() for w in category.split("-"))
    r = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": _PROMPT.format(
            topic=topic_data["topic"],
            topic_lower=topic_data["topic"].lower(),
            angle=topic_data["angle"],
            category=category,
            category_words=category_words,
            category_tag=category_tag,
            channel_url=CHANNEL_URL,
            duration_min=duration_min,
            timestamp_block=_timestamp_block(duration_min),
        )}],
    )

    text = r.content[0].text
    title, description, tags = "", "", []
    lines = text.strip().split("\n")
    mode = None
    desc_lines = []

    for line in lines:
        if line.startswith("TITLE:"):
            title = line.replace("TITLE:", "").strip()[:100]
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

    return {"title": title, "description": description, "tags": tags}
