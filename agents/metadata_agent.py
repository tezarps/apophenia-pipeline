import anthropic
from config import ANTHROPIC_API_KEY, HAIKU_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

CHANNEL_URL = "https://www.youtube.com/@heyapophenia"

_PROMPT = """You are an expert YouTube SEO strategist for a psychology insight essay channel \
called "Apophenia" (style reference: the channel "Kee" — generic psychological archetypes, \
real psychological terms explained conversationally, never academic, never naming real people).

Your job: generate a highly optimized title, description, and tags for this video.

Archetype: {topic}
Title angle: {angle}
Internal category tag (psych mechanism, do not say verbatim unless natural): {category}
Duration: ~{duration_min} minutes

SEO RULES — follow strictly:

TITLE rules:
- 60-100 characters
- Lead with "The Psychology of" or "The Psychology Of" followed by the archetype description —
  this exact phrase is the channel's proven high-performing format (reference titles: "The
  Psychology of the Emotionally Exhausted Adult", "The Psychology Of People Who Cut Off Their
  Family", "The Psychology of People Who Apologize Too Much")
- The archetype description after "The Psychology of" should read naturally, drawing on the
  archetype field and angle field given
- NO clickbait, NO ALL CAPS, NO emojis, no real person's name ever

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
