import re

import agents.llm as _llm

# Deterministic backstop — the _AUDIT LLM pass catches most banned AI-speak but
# isn't 100% reliable (confirmed empirically: "not because X, but because Y"
# slipped through the audit pass itself on 2026-06-21). Expanded 2026-06-21
# using the anti-ai-writing skill's fuller reframe/vocab taxonomy (~/.claude/skills/
# anti-ai-writing) — these regexes find any survivor so generate_script can do
# one more *sentence-scoped* rewrite instead of trusting a single LLM pass.
_BANNED_REGEX = [
    re.compile(r"\bnot\b[^.!?]{0,60}\bbecause\b[^.!?]*\bbut\b[^.!?]{0,15}\bbecause\b", re.I),
    re.compile(r"\bit'?s not\b[^.!?,]{0,40},?\s*it'?s\b", re.I),
    re.compile(r"\bisn'?t just\b[^.!?]{0,60}[—-]\s*it'?s\b", re.I),
    re.compile(r"\b(the (?:real|hidden|overlooked) question (?:isn'?t|is not))\b", re.I),
    re.compile(r"\bit was never about\b[^.!?]{0,60}\bit was always about\b", re.I),
    re.compile(r"\bless\b[^.!?]{0,30}\bmore\b[^.!?]{0,30}\b(?:than)?\b", re.I),
    re.compile(r"\bstop thinking\b[^.!?]{0,40}\bstart thinking\b", re.I),
    re.compile(r"\bhere'?s the thing\b", re.I),
    re.compile(r"\bthe truth is\b", re.I),
    re.compile(r"\bat the end of the day\b", re.I),
    re.compile(r"\bin other words\b", re.I),
    re.compile(r"\bwhat if I told you\b", re.I),
    re.compile(r"\band that'?s the beauty of it\b", re.I),
    re.compile(r"\bmost people (?:think|believe|assume)\b[^.!?]{0,80}\bbut\b", re.I),
    # anti-ai-writing vocab blocklist — words doing PR for an idea instead of describing it.
    re.compile(r"\b(delve|harness(?:ed|ing)?|unlock(?:s|ed|ing)?|tapestry|leverag(?:e|ed|ing)|synerg|seamless(?:ly)?|"
               r"elevate[sd]?|streamlin\w*|supercharge[sd]?|game-changer|cutting-edge|revolutioniz\w*|transformative|"
               r"intricate|crucial|pivotal|testament|foster(?:ed|ing)?|empower\w*|holistic|frictionless|unparalleled|"
               r"groundbreaking|data-driven|frictionless)\b", re.I),
    # Dead openings / transitions / engagement bait.
    re.compile(r"^(in today'?s|it'?s important to note|let'?s (?:dive in|explore|unpack)|nobody is talking about)", re.I),
    re.compile(r"\b(furthermore|moreover|additionally)\b,", re.I),
    re.compile(r"\b(let that sink in|read that again|this changes everything)\b", re.I),
    # Meta-announcement filler — kills hook momentum (observed in Topic #3 post-mortem)
    re.compile(r"\bthat'?s what I want to (?:talk about|explore|discuss)\b", re.I),
    re.compile(r"\blet me (?:start|begin) with\b", re.I),
    re.compile(r"\btoday (?:I want to|we'?re going to|we will)\b", re.I),
    re.compile(r"\bin this video\b", re.I),
]


def _find_banned_sentence(text):
    """Returns the first whole sentence containing a banned pattern, or None."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        if any(rx.search(s) for rx in _BANNED_REGEX):
            return s
    return None

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

BABAK 1 — HOOK. This is a YouTube video — the viewer decides whether to keep watching within the \
first 5-8 spoken seconds (roughly the first 1-2 sentences), before anything else in the script has a \
chance to land. Those opening words must make the specific right viewer feel "wait, that's me" \
immediately — concrete and instantly recognizable, not a warm-up or a general statement about \
psychology. Build the hook around real curiosity, not just a relatable moment: name what the viewer \
currently believes about themselves or this pattern (A), and set it against what this video is \
actually about to reveal (B) — B must be concrete (a mechanism, a specific reframe), not a vague tease. \
That A-vs-B gap is the open question that only resolves later in the video. {hook_instruction}

CRITICAL — NO TRANSITION ANNOUNCEMENT after the hook. The hook's last word flows directly into babak 2 \
with NO meta-commentary bridge sentence. The following phrases are absolutely banned anywhere in the \
script but especially between babak 1 and 2: "That's what I want to talk about today", \
"Let me start with", "Today I want to", "In this video", "What I'm going to explore", or any \
sentence that announces what the video is about to do instead of just doing it. \
The hook ends — babak 2 begins. No announcement needed.

RETENTION-CRITICAL WINDOW — 0:05 to 0:40 (confirmed 2026-07-02 via real YouTube Studio audience-retention \
data on "The Psychology of Adults Who Cut Off Their Family": AVD was above channel average, but a sharp \
viewer drop-off happened specifically between second 5 and second 40 — NOT just the opening 1-2 sentences. \
That drop lands right where the hook (babak 1) hands off into babak 2, meaning the actual failure point \
was momentum loss during the HANDOFF, not the opening line itself). To fix this: babak 2's first 1-2 \
sentences must open on the single sharpest, most concrete, most viscerally recognizable image or detail \
of the formative scenario — not a general setup, not scene-setting throat-clearing, not "let's go back \
to..." framing, not easing into the memory. Skip straight to the specific moment (a smell, a sentence \
someone said, a posture, an exact small action) that carries the emotional charge. The reader should feel \
the wound land within the first sentence of babak 2, with zero runway. Treat the whole 0:05-0:40 window as \
one continuous held breath — the hook's tension must not slacken even slightly while babak 2 gets going.

BABAK 2 — THE WOUND. Explore where this pattern was likely formed — a believable, generic formative \
scenario (childhood home dynamic, an early relationship, a repeated small moment), written in second \
person ("you"). Not a single traumatic event necessarily — often it's something that happened quietly, \
repeatedly. Show how the pattern made sense as a survival response at the time. Build this as a chain \
of BUT / THEREFORE beats, never "and then" — each beat should complicate or cause the next ("you needed \
X — but Y happened — therefore you learned Z"), not just list things that happened in sequence. Reach \
for lived, concrete detail (a specific moment, not a category) rather than a general description of \
the dynamic.

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
exact hook/title from babak 1 — land on the same image or A-vs-B contrast you opened with, now re-seen \
through everything explained since. Make this closing line a genuine "last dab": write it so that if a \
viewer heard only that one sentence, they'd still feel something and want to repeat it — not a generic \
wrap-up. After that lands, add ONE final short sentence that names the channel naturally as a soft \
invitation to keep watching more — something like "There's more of this every week here on Apophenia" \
or "If this is you, you'll find more of yourself here on Apophenia" — channel name "Apophenia" must \
appear, said like a person talking, never like a corporate subscribe-and-like ad read.

After the channel mention, add ONE more short sentence — the actual spoken engagement ask (subscribe, \
like, comment, share) — but said like a real person closing out a conversation, never like a read ad. \
Anchor it to the specific archetype just discussed instead of being generic: e.g. invite the viewer to \
say in the comments whether this pattern is them (or who it reminds them of), mention that liking helps \
this reach someone who needs to hear it, and a quick "subscribe if you want more of these." Vary the \
exact phrasing and which of the four asks (like/comment/subscribe/share) gets the most emphasis each \
time — don't reuse the same sentence shape across videos. This is the very last line of the script.

Point of view: second person ("you"), present tense, conversational, throughout (except the brief \
first-person hook moment in the "confession" variant, if used). Tone: warm, a little intimate, calm \
delivery — emotionally restrained, never melodramatic, never therapy-speak cliché.

Plain language: keep the IDEAS sophisticated but the WORDS simple — aim for roughly 8th-grade reading \
level in the body (a smart 13-year-old should follow every sentence on first listen, since this is \
heard once, not read). Common words over fancy ones (use, help, show, enough — not utilize, facilitate, \
demonstrate, sufficient). One idea per sentence; break up nested clauses. Concrete over abstract. Cut \
filler like "basically" and "in order to." Vary sentence length on purpose — some short and blunt, some \
longer — read it back and check it doesn't fall into a monotone rhythm where every sentence runs the \
same length.

Length: let it breathe naturally with the content — aim for roughly 2,200-3,000 words total, distributed \
unevenly across babak as the content needs (babak 2 and 5 are usually longest).

Output only the script text. No headers, no babak labels, no markdown."""

_POLISH = """You are an editor for a psychology-essay YouTube script (channel tone: calm, intimate, \
conversational — think "Kee"). Your main job this pass is HUMANIZING — this draft was written by an \
LLM and reads like one. Hunt down and rewrite every generic AI-speak pattern, even if it means \
rephrasing a sentence completely.

BANNED PATTERNS — find every instance and rewrite it into something a specific, real human would \
actually say. Do not just swap a synonym, change the sentence's actual structure:
- "Not because X, but because Y" (and reverse: "It's not X, it's Y"), "Less X, more Y", "Stop thinking \
X, start thinking Y", "The real/hidden question isn't X, it's Y", "It was never about X, it was always \
about Y", "Most people think X, but..." — every variant of the negate-then-correct reframe. Test before \
cutting: is Y actually concrete (a number, a mechanism, a named thing)? If Y is just vague significance \
("it's about connection", "it's about mindset"), the whole sentence is hollow — cut it and state the real \
point directly. If Y is genuinely concrete and the script delivers on it, the contrast can stay.
- "It's not just about X — it's about Y" / "It's not just X, it's Y"
- "Here's the thing" / "The truth is" / "At the end of the day" / "In other words" / "What if I told you"
- "And that's the beauty of it" / "And that's exactly the point" / abstract poetic closers like \
"it's a quiet rebellion" / "a quiet kind of [noun]"
- Significance inflation on normal facts ("this marks a pivotal shift", "a profound truth emerges") — \
state the fact plainly, let the weight come from the fact itself.
- Banned metaphor families: journeys, battlefields, machines-for-people, flywheels, engines/fuel, \
icebergs, bridges. Banned metaphor verbs: baked in, bolted on, woven, layered, distilled, unpacked, \
crystallized, surfaced, threaded, sculpted, anchored. Replace with literal verbs (cut, added, caused, \
showed, fixed) — write literally unless the analogy is shorter AND clearer than the literal version.
- Vocabulary tells: delve, harness, unlock, tapestry, leverage, seamless, elevate, robust, crucial, \
pivotal, holistic, frictionless, empower, foster, testament — these do PR for an idea instead of \
describing it. Cut or replace with a plain word.
- Rule-of-three lists ("you speak up, you set a boundary, you say no") — vary the count, don't default to 3
- Parallel hammering ("You don't just X. You Y. You Z.") used more than once in the whole script
- Symmetric parallelism that sounds good but says nothing ("you protect, you provide, you persist")
- Rhetorical question chains (more than one rhetorical question back to back)
- Overusing em-dashes for dramatic pause — if more than ~3 in the whole script, cut most of them and \
restructure the sentence instead
- "Subtle, but powerful" / "small, but real" / any "[adjective], but [adjective]" hedge construction
- Therapy-speak buzzwords used like jargon-dropping rather than genuine explanation: "hold space", \
"sit with it", "show up for yourself", "do the work" — if used, ground it in a concrete action instead
- Vagueness compression: a category standing in for a real instance ("users were frustrated" energy) — \
push every claim toward a specific, lived detail instead of a general description.
- "And then... and then..." beat-piling in the wound section (babak 2) with no causal tension — each \
beat there should connect with BUT or THEREFORE, never just sequence.

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
3. Make sure the ending explicitly echoes the opening hook's exact image or A-vs-B contrast — this \
loop-back is mandatory, check it's actually there and sharpen it if it's vague. The closing line should \
be quotable on its own (a "last dab"), not a generic wrap-up.
4. Make sure the very first 1-2 sentences are an instantly-recognizable "wait, that's me" moment with a \
real A-vs-B contrast — not a general statement about psychology or this archetype. Sharpen it if it \
reads slow or warmed-up instead of immediately gripping.
5. Make sure there's a natural, non-corporate-sounding mention of "Apophenia" inviting the viewer to \
keep watching more — add one if it's missing, sharpen it if it sounds like an ad read.
5b. Make sure the VERY LAST sentence is a spoken like/comment/subscribe/share ask, anchored to the \
specific archetype just discussed (not generic) and phrased like a real person, not an ad read — add \
one if it's missing, sharpen it if it sounds canned.
6. Keep delivery conversational, not academic. No citations, no "research shows" hedging.
7. Keep reading level around 8th grade — common words over fancy ones, one idea per sentence. Flag and \
simplify any sentence that needs a second read to parse.
8. Do not introduce any named historical or real individual anywhere in the script.
9. Do NOT add chapter headers, babak labels, or section markers.

Draft:
{draft}

Output only the polished script."""

_AUDIT = """You are doing a final mechanical check on a YouTube script — NOT a rewrite. The script \
already went through a humanizing pass, but a few banned AI-speak patterns sometimes survive that \
pass anyway. Your only job: find every remaining instance of these patterns and rewrite ONLY that \
sentence (preserve everything else word-for-word, including all other phrasing/punctuation choices \
already made):

- "Not because X, but because Y" / "It's not X, it's Y" / "isn't just about X — it's about Y" / \
"isn't just X, it's Y" / "Less X, more Y" / "Most people think X, but..." — say what the sentence \
means directly instead of through this negate-then-correct frame, unless Y is genuinely concrete and \
earned (a number, a mechanism) rather than vague significance.
- "Here's the thing" / "The truth is" / "At the end of the day" / "In other words" / "What if I told you"
- "And that's the beauty of it" / "And that's exactly the point" / abstract closers like "a quiet \
rebellion" or "a quiet kind of [noun]"
- Vocabulary tells: delve, harness, unlock, tapestry, leverage, seamless, elevate, robust, crucial, \
pivotal, holistic, frictionless, empower, foster
- Rule-of-three lists, or "You don't just X. You Y. You Z." used more than once total in the script
- More than one rhetorical question in a row

If the script has NONE of these patterns, output it completely unchanged. Do not "improve" anything \
else — this is a targeted fix, not another editing pass.

Script:
{script}

Output the full script, unchanged except for any rewritten sentences."""

_REWRITE_SENTENCE = """Rewrite this one sentence from a YouTube psychology-essay script. It uses a \
generic AI-speak construction (negate-then-correct framing like "not because X, but because Y", a \
PR-vocabulary word like "harness"/"unlock"/"leverage", or a stock phrase like "here's the thing"/"the \
truth is"/"at the end of the day"). Say the same thing directly and conversationally instead — like a \
specific person explaining it to a friend, not an essay structure. Keep it roughly the same length and \
keep any specific content/claims intact.

Sentence:
{sentence}

Output ONLY the rewritten sentence, nothing else."""


def _call(model, prompt, max_tokens=8000):
    return _llm.call(prompt, max_tokens=max_tokens)


def generate_script(topic_data):
    category = topic_data["category"]
    topic = topic_data["topic"]
    angle = topic_data["angle"]
    variant_key = _hook_variant_for(topic_data["id"])
    hook_instruction = _HOOK_VARIANTS[variant_key]

    print(f"    Hook variant: {variant_key}")
    print("    Drafting...")
    draft = _call(None, _DRAFT.format(
        topic=topic, angle=angle, category=category, hook_instruction=hook_instruction,
    ))

    print("    Polishing...")
    polished = _call(None, _POLISH.format(draft=draft))

    print("    Auditing for leftover AI-speak...")
    final = _call(None, _AUDIT.format(script=polished), max_tokens=8000)

    for round_num in range(2):
        bad_sentence = _find_banned_sentence(final)
        if not bad_sentence:
            break
        print(f"    Banned pattern survived audit pass — rewriting one sentence (round {round_num + 1})...")
        rewritten = _call(None, _REWRITE_SENTENCE.format(sentence=bad_sentence), max_tokens=300).strip()
        final = final.replace(bad_sentence, rewritten, 1)

    word_count = len(final.split())
    print(f"    Script: {word_count:,} words (~{word_count // 140:.0f} min audio)")
    return final.strip()
