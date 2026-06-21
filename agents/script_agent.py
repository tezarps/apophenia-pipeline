import anthropic
from config import ANTHROPIC_API_KEY, HAIKU_MODEL, SONNET_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Four hook devices, rotated by topic id, so every video doesn't open with the
# exact same rhetorical move (templated-content risk, same reasoning as Narava's
# frame variants — see project memory project_apophenia.md). The 5-babak
# skeleton itself stays fixed across all variants; that skeleton IS the brand.
_HOOK_VARIANTS = {
    "question": "Open with a short, direct second-person question the viewer might silently ask themselves about this exact pattern. 2-3 sentences max before moving into babak 2.",
    "scene": "Open with a tiny, concrete relatable scene (a specific moment, not abstract) where this pattern just played out — something mundane, instantly recognizable. 2-4 sentences before moving into babak 2.",
    "confession": "Open as if voicing the exact internal thought this archetype has but never says out loud. First-person internal monologue style, 1-3 sentences, then pivot to second person for the rest of the video.",
    "label": "Open by naming the pattern bluntly and a little unsettlingly, then immediately soften it with 'and if that's you, here's what's actually happening' before moving into babak 2.",
}
_VARIANT_ORDER = ["question", "scene", "confession", "label"]


def _hook_variant_for(topic_id):
    return _VARIANT_ORDER[int(topic_id) % len(_VARIANT_ORDER)]


_DRAFT = """You are writing a psychology insight essay script for YouTube (think the channel "Kee" — \
calm, intimate, second-person narration; real psychological substance delivered conversationally, never academic).

Archetype: {topic}
Title/angle: {angle}
Category (internal tag, do not say out loud): {category}

CRITICAL CONSTRAINT: this is a GENERIC psychological archetype. Do not name, describe, or allude to \
any real historical figure, celebrity, or identifiable individual. No likeness, no biography. The \
"you" in this script is a composite pattern, not a real person.

Write the full script in five babak (acts), in order, as one continuous flowing piece — no headers, \
no labels, no act numbers in the output:

BABAK 1 — HOOK. {hook_instruction}

BABAK 2 — THE WOUND. Explore where this pattern was likely formed — a believable, generic formative \
scenario (childhood home dynamic, an early relationship, a repeated small moment), written in second \
person ("you"). Not a single traumatic event necessarily — often it's something that happened quietly, \
repeatedly. Show how the pattern made sense as a survival response at the time.

BABAK 3 — THE SCIENCE. Name the real psychological mechanism or term that explains this pattern \
(e.g. fawn response, hypervigilance, parentification, learned helplessness — use whatever term \
genuinely fits this specific archetype). Explain it like you're explaining it to a smart friend over \
coffee, not reading a journal abstract — no citations, no "studies show," just clear plain-language \
explanation of the mechanism and why it makes psychological sense.

BABAK 4 — THE TURN. A reframe. Not "just stop doing this" — show what the pattern was actually \
protecting, and what becomes possible once that's seen clearly. This is the emotional pivot point of \
the video.

BABAK 5 — THE STRATEGY + CLOSE. One or two concrete, practical things the viewer can actually try \
(specific, not generic "practice self-compassion" platitudes). Then close by looping back to the \
exact hook/title from babak 1 — land on the same image or question you opened with, now re-seen \
through everything explained since.

Point of view: second person ("you"), present tense, conversational, throughout (except the brief \
first-person hook moment in the "confession" variant, if used). Tone: warm, a little intimate, calm \
delivery — emotionally restrained, never melodramatic, never therapy-speak cliché.

Length: let it breathe naturally with the content — aim for roughly 2,200-3,000 words total, distributed \
unevenly across babak as the content needs (babak 2 and 5 are usually longest).

Output only the script text. No headers, no babak labels, no markdown."""

_POLISH = """You are an editor for a psychology-essay YouTube script (channel tone: calm, intimate, \
conversational — think "Kee"). Your main job this pass is HUMANIZING — this draft was written by an \
LLM and reads like one. Hunt down and rewrite every generic AI-speak pattern, even if it means \
rephrasing a sentence completely.

BANNED PATTERNS — find every instance and rewrite it into something a specific, real human would \
actually say. Do not just swap a synonym, change the sentence's actual structure:
- "Not because X, but because Y" (and reverse: "It's not X, it's Y") — way overused, find what the \
sentence is actually trying to say and say it directly instead.
- "It's not just about X — it's about Y" / "It's not just X, it's Y"
- "Here's the thing" / "The truth is" / "At the end of the day" / "In other words" / "What if I told you"
- "And that's the beauty of it" / "And that's exactly the point" / abstract poetic closers like \
"it's a quiet rebellion" / "a quiet kind of [noun]"
- Rule-of-three lists ("you speak up, you set a boundary, you say no") — vary the count, don't default to 3
- Parallel hammering ("You don't just X. You Y. You Z.") used more than once in the whole script
- Rhetorical question chains (more than one rhetorical question back to back)
- Overusing em-dashes for dramatic pause — if more than ~3 in the whole script, cut most of them and \
restructure the sentence instead
- "Subtle, but powerful" / "small, but real" / any "[adjective], but [adjective]" hedge construction
- Therapy-speak buzzwords used like jargon-dropping rather than genuine explanation: "hold space", \
"sit with it", "show up for yourself", "do the work" — if used, ground it in a concrete action instead

REWRITE APPROACH for every flagged sentence: picture a specific, slightly imperfect human actually \
saying this out loud to a friend — contractions, occasional sentence fragments, varied sentence \
length (some short and blunt, some longer and winding), concrete sensory detail over abstraction. \
Punctuation should cue how it's actually spoken — a short blunt sentence for a hard truth, a longer \
trailing one (with "..." where a real pause would land) for something more uncertain or vulnerable, \
a question mark where the voice would genuinely lift. This script gets read aloud by TTS, so the \
punctuation IS the performance direction — use it deliberately, not decoratively.

Other rules:
1. Tighten pacing — cut any sentence that restates something already said.
2. Make sure the psychological term introduced in babak 3 has one clear, plain-language sentence \
defining it the first time it's used — a viewer with zero psych background should follow it.
3. Make sure the ending explicitly echoes the opening hook's exact image or question — this loop-back \
is mandatory, check it's actually there and sharpen it if it's vague.
4. Keep delivery conversational, not academic. No citations, no "research shows" hedging.
5. Do not introduce any named historical or real individual anywhere in the script.
6. Do NOT add chapter headers, babak labels, or section markers.

Draft:
{draft}

Output only the polished script."""

_AUDIT = """You are doing a final mechanical check on a YouTube script — NOT a rewrite. The script \
already went through a humanizing pass, but a couple of banned AI-speak patterns sometimes survive \
that pass anyway. Your only job: find every remaining instance of these patterns and rewrite ONLY \
that sentence (preserve everything else word-for-word, including all other phrasing/punctuation \
choices already made):

- "Not because X, but because Y" / "It's not X, it's Y" / "isn't just about X — it's about Y" / \
"isn't just X, it's Y" — say what the sentence means directly instead of through this negate-then-\
correct frame.
- "Here's the thing" / "The truth is" / "At the end of the day" / "In other words" / "What if I told you"
- "And that's the beauty of it" / "And that's exactly the point" / abstract closers like "a quiet \
rebellion" or "a quiet kind of [noun]"
- Rule-of-three lists, or "You don't just X. You Y. You Z." used more than once total in the script
- More than one rhetorical question in a row

If the script has NONE of these patterns, output it completely unchanged. Do not "improve" anything \
else — this is a targeted fix, not another editing pass.

Script:
{script}

Output the full script, unchanged except for any rewritten sentences."""


def _call(model, prompt, max_tokens=8000):
    r = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return r.content[0].text


def generate_script(topic_data):
    category = topic_data["category"]
    topic = topic_data["topic"]
    angle = topic_data["angle"]
    variant_key = _hook_variant_for(topic_data["id"])
    hook_instruction = _HOOK_VARIANTS[variant_key]

    print(f"    Hook variant: {variant_key}")
    print("    Drafting (Haiku)...")
    draft = _call(HAIKU_MODEL, _DRAFT.format(
        topic=topic, angle=angle, category=category, hook_instruction=hook_instruction,
    ))

    print("    Polishing (Sonnet)...")
    polished = _call(SONNET_MODEL, _POLISH.format(draft=draft))

    print("    Auditing for leftover AI-speak (Haiku)...")
    final = _call(HAIKU_MODEL, _AUDIT.format(script=polished), max_tokens=8000)

    word_count = len(final.split())
    print(f"    Script: {word_count:,} words (~{word_count // 140:.0f} min audio)")
    return final.strip()
