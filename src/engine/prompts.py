"""All prompts of the script engine, in one place so they can be tuned together.

Instructions are written in English (models, especially small local ones, follow
English instructions most reliably); every human-readable output is requested in
the lesson's language.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

LANGUAGE_NAMES = {"he": "Hebrew", "en": "English"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, "Hebrew")


# --------------------------------------------------------------------------- formats

FORMATS: Dict[str, Dict[str, Any]] = {
    "deep_dive": {
        "label": "Deep dive",
        "fits": "most lessons; builds from the foundations to the hardest idea using the lecturer's own examples",
        "style": "Build understanding step by step; linger on the hardest idea; use the lecturer's examples.",
        "roles": {
            "Roee": "lead host who studied the lesson closely and explains through concrete examples",
            "Noa": "sharp co-host who knows the field a little, connects ideas to real situations, "
            "pushes back and asks the 'wait, why?' questions",
        },
    },
    "beginner_explainer": {
        "label": "Beginner explainer",
        "fits": "intro-level lessons or listeners new to the field: slower pace, everyday analogies, frequent checks",
        "style": "Slow down on every new term; one idea at a time; Noa restates ideas in her own words.",
        "roles": {
            "Roee": "patient explainer who never skips a step",
            "Noa": "smart newcomer who says out loud what a beginner would find confusing and sums up in her own words",
        },
    },
    "exam_prep": {
        "label": "Exam prep",
        "fits": "technical or definition-heavy lessons students must master: precise definitions, distinctions "
        "between similar concepts, typical mistakes",
        "style": "Be precise; contrast easily confused concepts; end each body segment with a quick self-check "
        "question that the other host answers, and let the answer be the segment's last word on it.",
        "roles": {
            "Roee": "tutor who knows where students lose points",
            "Noa": "student preparing for the exam who tests herself and sometimes answers wrong first",
        },
    },
    "story": {
        "label": "Story",
        "fits": "history, humanities, case studies or any lesson built around events and people",
        "style": "Tell it as a narrative in order: situation, tension, turning point, consequences.",
        "roles": {
            "Roee": "storyteller who sets the scene",
            "Noa": "listener who asks what it meant, why it mattered and what came next",
        },
    },
    "debate": {
        "label": "Debate",
        "fits": "lessons that present competing views, trade-offs or open questions",
        "style": "The hosts take different angles on each trade-off, both grounded in the lesson, and end by "
        "stating when each view holds.",
        "roles": {
            "Roee": "argues for one approach",
            "Noa": "argues for the other",
        },
    },
    # The two below are chosen by the listener only; automatic format choice never picks them
    "brief": {
        "label": "Brief",
        "manual_only": True,
        "fits": "a short recap when the listener wants only the essentials",
        "style": "Only the few ideas a listener must keep; one clear example each; no detours, no long "
        "warm-up; still a real conversation, not a list read aloud.",
        "roles": {
            "Roee": "host who gets straight to the point",
            "Noa": "co-host who asks the one question that makes each idea click",
        },
    },
    "critique": {
        "label": "Critical review",
        "manual_only": True,
        "fits": "examining the lesson itself: what is convincing, what rests on assumptions, what is left open",
        "style": "For each main claim of the lesson: state it fairly, then weigh it - what supports it in the "
        "lesson, what it assumes, where it stops applying, what the lesson leaves open. Give the objections raised "
        "in the lesson real airtime and attribute them ('one of the students pushed back...'). A single success "
        "story is an example, not proof. Judge only what the lesson says; never invent counter-evidence or "
        "outside sources.",
        "roles": {
            "Roee": "host who presents each claim at its strongest",
            "Noa": "critical co-host who probes assumptions and limits, fairly and with respect",
        },
    },
}


def formats_menu() -> str:
    return "\n".join(f"- {key}: {spec['fits']}" for key, spec in FORMATS.items() if not spec.get("manual_only"))


# --------------------------------------------------------------------------- stage 1: clean

CLEAN_SYSTEM = (
    "You prepare lecture transcripts for further processing. You never rewrite, shorten or summarize "
    "the lecture. You only split it into topical sections and point out obvious speech-recognition "
    "errors. Reply with JSON only."
)


def clean_user_prompt(numbered_paragraphs: str, language: str) -> str:
    name = language_name(language)
    return f"""The transcript below is split into numbered paragraphs. Language: {name}.

Tasks:
1. sections: group consecutive paragraphs by topic. For each section give the number of its first paragraph and a short title (2-6 words, in {name}). Usually a new section every 3-8 paragraphs; never one section per paragraph.
2. fixes: list words that are clearly speech-recognition errors (a misheard technical term or name, an English word written phonetically) with the exact wrong text as it appears and its correction. Only errors you are sure of, at most 15. Do not fix grammar, style or spoken phrasing.

Return JSON:
{{"sections": [{{"start": 1, "title": "..."}}], "fixes": [{{"wrong": "...", "right": "..."}}]}}

Transcript:
{numbered_paragraphs}"""


# --------------------------------------------------------------------------- stage 2: profile

PROFILE_SYSTEM = (
    "You are an expert instructional designer. You analyse a lecture transcript so that an educational "
    "podcast can be built from it. You are precise and stay strictly within what the lecture says. "
    "Reply with JSON only."
)

_PROFILE_SHAPE = """{
  "title": "short title of the lesson",
  "summary": "4-6 sentences on what the lesson teaches",
  "domain": "the field, e.g. computer networking, organic chemistry, Jewish history",
  "level": "intro | intermediate | advanced",
  "lesson_type": "lecture | tutorial | workshop | discussion | story",
  "audience": "who the lesson is for",
  "learning_objectives": ["what a student can do after the lesson"],
  "concepts": [{"name": "...", "definition": "as the lecturer defined or used it", "depends_on": ["other concept names"], "section_ids": ["S3"], "importance": 3}],
  "examples": [{"description": "an example, analogy, story or demo the lecturer used", "illustrates": "concept name", "section_id": "S3"}],
  "key_facts": [{"claim": "a specific fact, number, rule or distinction stated in the lesson", "section_id": "S2"}],
  "misconceptions": ["confusions the lecturer warned about or that this material typically causes"],
  "open_questions": ["questions the lecturer raised or left open"],
  "arguments": [{"claim": "a claim that was argued or disputed in the lesson", "raised_by": "who made it: the lecturer, a student, a cited view", "support": "the reasoning or evidence given for it", "objections": ["counterpoints, limits or doubts raised in the lesson, with who raised them"], "section_id": "S4"}]
}"""


def profile_user_prompt(
    source: str,
    language: str,
    hints: Optional[Dict[str, Any]] = None,
    part: Optional[tuple[int, int]] = None,
) -> str:
    name = language_name(language)
    hint_lines = ""
    if hints:
        hint_lines = (
            "\nThe uploader described the lesson as follows. Treat it as a hint only; the transcript wins:\n"
            + json.dumps(hints, ensure_ascii=False)
            + "\n"
        )
    part_line = f"\nThis is part {part[0]} of {part[1]} of a long lesson. Analyse only this part.\n" if part else ""
    return f"""Analyse this lesson. Write every human-readable value in {name}; keep technical terms the way the lecturer used them.
{hint_lines}{part_line}
Return JSON with exactly these keys:
{_PROFILE_SHAPE}

Rules:
- importance: 3 = a core idea of the lesson, 2 = supporting, 1 = mentioned in passing.
- List concepts so that each one comes after the concepts it depends on.
- section_ids refer to the [S#] markers in the transcript.
- Capture the lecturer's own examples faithfully; they are the most valuable material for the podcast.
- arguments: only claims that were actually argued, challenged or weighed in the lesson (empty list if none). Keep every objection a student or speaker raised, even if the discussion moved on without resolving it; these are easily lost.
- Do not add facts that are not in the transcript.

Transcript:
{source}"""


def profile_merge_prompt(partials: List[Dict[str, Any]], language: str) -> str:
    return f"""These are analyses of consecutive parts of one lesson. Merge them into a single analysis with the same JSON keys, written in {language_name(language)}.
Merge duplicate concepts (keep all their section_ids and the highest importance), keep concepts in dependency order, keep all distinct examples and facts, and write one summary for the whole lesson.

Return JSON with exactly these keys:
{_PROFILE_SHAPE}

Partial analyses:
{json.dumps(partials, ensure_ascii=False)}"""


# --------------------------------------------------------------------------- stage 3: plan

PLAN_SYSTEM = (
    "You are the showrunner of an educational podcast with two hosts, Roee and Noa. You design episodes "
    "that make one specific lesson clear and memorable. Reply with JSON only."
)


def plan_user_prompt(
    profile: Dict[str, Any],
    section_list: str,
    language: str,
    target_minutes: float,
    body_range: tuple[int, int],
    suggested_format: str,
    forced_format: Optional[str],
    focus: str = "",
) -> str:
    name = language_name(language)
    focus_line = (
        f"\nThe listener asked for this focus: {focus}\nShape the episode around it (which ideas get the most "
        "time, which examples are used, what the hook is) while staying faithful to the lesson.\n"
        if focus else ""
    )
    if forced_format == "critique":
        format_line = (
            "Use the format: critique. Build each body segment on one claim from the analysis's \"arguments\" "
            "(or a central claim of the lesson). Its beats must include: the claim at its strongest with the support "
            "given in the lesson; the strongest objection or limit raised in the lesson, attributed to who raised it; "
            "and what stays open. Never present one success story or one side as settled when the lesson disputed it."
        )
    elif forced_format == "debate":
        format_line = (
            "Use the format: debate. Each body segment takes one disputed question from the lesson. Its beats name "
            "the two positions (Roee defends the first, Noa the second), the lesson's strongest argument for each, "
            "and the moment they clash. The takeaways say plainly when each view holds, as the lesson suggests."
        )
    elif forced_format:
        format_line = f"Use the format: {forced_format}."
    else:
        format_line = (
            f"Choose the format that fits this lesson best. Suggested: {suggested_format}; "
            "choose another only if it clearly fits better."
        )
    return f"""Design one episode from this lesson analysis. Write all text values in {name}.

Length: about {target_minutes:g} minutes of speech. Plan {body_range[0]}-{body_range[1]} body segments, plus one short opening segment and one closing segment.
{focus_line}
Formats:
{formats_menu()}
{format_line}

Return JSON:
{{
  "format": "<format key>",
  "title": "episode title",
  "hook": "the opening hook: a question, surprise or real situation from the lesson that makes a listener want to keep listening",
  "segments": [
    {{"kind": "intro | body | closing", "title": "...", "goal": "what the listener understands by the end of the segment",
      "concepts": ["concept names exactly as in the analysis"], "source_sections": ["S1", "S2"],
      "examples": ["which of the lecturer's examples to use"],
      "beats": ["2-4 moments the segment must hit, e.g. the question that opens it, the misconception to clear up, the analogy, the 'aha'"],
      "weight": 2}}
  ],
  "takeaways": ["3 short takeaways for the closing"]
}}

Rules:
- Think like the producer of a great two-host explainer: every segment needs a reason to listen (a puzzle, a surprise, a practical stake) and a moment where the idea clicks. Put those moments in "beats".
- Order body segments so that concepts come after the concepts they depend on.
- Every concept with importance 3 must be covered by a body segment; importance 2 where it fits.
- weight (1-3) is the relative length of a segment; 3 = the heaviest idea of the episode.
- source_sections lists the transcript sections the segment draws on.
- Build each segment around concrete material from the lesson (an example, a demo, a distinction), not around abstract definitions.

Transcript sections (id: title, words):
{section_list}

Lesson analysis:
{json.dumps(profile, ensure_ascii=False)}"""


# --------------------------------------------------------------------------- stage 4: write

_HEBREW_STYLE = (
    "- Natural Israeli spoken Hebrew (e.g. 'בעצם', 'רגע', 'זאת אומרת', 'נכון?'), not formal or literary "
    "Hebrew; avoid 'ובכן', 'הבה', 'אכן', 'הנני'.\n"
    "- Reactions sound Israeli, not translated: 'רגע, רגע.', 'אה, עכשיו זה מתחבר.', 'בדיוק.', 'וואו.', "
    "'אוקיי, זה מטורף.' Use them sparingly so they stay real.\n"
    "- Correct gender: Roee speaks as a man and Noa as a woman, and they address each other accordingly.\n"
)


def write_system_prompt(language: str) -> str:
    name = language_name(language)
    hebrew = _HEBREW_STYLE if language != "en" else ""
    return f"""You write the script of an educational podcast in {name} that should feel like the best AI audio overviews: two hosts, Roee (male) and Noa (female), genuinely exploring a lesson together. Reply with JSON only.

What makes it work:
- Discovery, not lecturing. The hosts uncover the ideas together. Every segment has a small arc: a question or puzzle, working it out, the moment it clicks ("aha"), and a one-sentence takeaway said naturally.
- Two smart hosts. Both know the material and they trade roles: one explains while the other connects it to real life, offers the analogy, pushes back, or voices what a listener is probably thinking. Never an interviewer and an expert.
- Real reactions. Short backchannels and reactions as their own turns, finishing each other's thoughts, the odd moment of gentle humor. A turn may break off with "—" when the other host jumps in.
- A real knowledge gap in every segment. One host knows this part better; the other genuinely works it out loud: makes a reasonable wrong guess or voices the common misconception, and is corrected warmly with the lesson's own reasoning. Let them swap who knows more when the topic changes.
- Spontaneous, not scripted. Now and then a self-correction ("כלומר, לא בדיוק—"), a thought cut off by the other host, a quick confirmation turn ("כן.", "נכון.", "אה-הא."). Keep it light: at most one filler or backchannel every few lines; never chains of "אממ".
- No mechanical habits. Do not praise the other host's questions ("שאלה מצוינת"), do not announce topics ("בוא נדבר על..."), do not say "כמו שאמרנו". No empty hand-off questions ("איזה?", "ומה עוד?", "איך זה עובד?") whose only job is to pass the floor: when a host asks, the question carries a guess, a doubt or an idea. End a segment on the click or on a question that pulls toward the next idea, not on a recap.
- Talk to the podcast listener, not to the people who sat in the lecture: never address the original class (their internship, their exam on Sunday, "as you saw on the board") unless the listener's own focus says so.
- Concrete before abstract. Lead with the lecturer's example, a vivid image or an everyday analogy, then name the concept.
- Signposting and recall. Tell the listener where the conversation is going, connect back to earlier ideas by name, and keep the thread obvious.
- Rhythm, written for the ear. Most turns are 5-30 words. When something needs explaining, one turn may run to about 60 words, and then the other host reacts, checks or adds before the explanation continues. Break a long explanation across several turns rather than one block. Vary the pace: a few one-word reactions, a few longer turns.
- Spoken, everyday {name}, never written prose.
{hebrew}
Grounding:
- Every fact, number, name, definition or claim must come from the SOURCE. Never invent facts, statistics, studies or quotes. Analogies and examples of your own are welcome only when they are clearly presented as analogies.
- The SOURCE may be in another language. Never translate it sentence by sentence: explain it in natural {name} as if you had learned it yourself, keeping the technical terms that {name} speakers actually use.

Format of each turn:
- "text" is exactly what is said aloud: no markdown, lists, emojis, stage directions or speaker names. Write numbers as words (a four-digit year may stay in digits), and write symbols, units, formulas and abbreviations the way they are said (e.g. "שבעים אחוז", not "70%"). Never read code or formulas character by character; say what they do. English technical terms, acronyms and code names stay in Latin letters as English (TCP/IP, OSI, MAC, useState, NASA), always written the same way, never spelled out phonetically in Hebrew letters; the voices pronounce them correctly. Avoid Hebrew acronyms with gershayim (אק"ג, הלמ"ס): say the full words or the common English form (ECG).
- "delivery" is an optional short English hint for the voice when tone matters, e.g. "curious", "excited", "thoughtful", "amused", "surprised", "laughing softly". Leave it empty for neutral lines; use it on roughly a third of the lines."""


def write_user_prompt(
    *,
    episode: Dict[str, Any],
    profile: Dict[str, Any],
    segment: Dict[str, Any],
    position: tuple[int, int],
    covered_titles: List[str],
    previous_lines: List[Dict[str, str]],
    concept_notes: List[str],
    source: str,
    target_words: int,
    revision_issues: Optional[List[str]] = None,
    previous_draft: Optional[List[Dict[str, str]]] = None,
    used_examples: Optional[List[str]] = None,
    used_phrases: Optional[List[str]] = None,
) -> str:
    spec = FORMATS.get(episode.get("format", ""), FORMATS["deep_dive"])
    roles = "; ".join(f"{who}: {role}" for who, role in spec["roles"].items())
    kind = segment.get("kind", "body")
    if kind == "intro":
        kind_rules = (
            f"This is the opening. Start with the hook: {episode.get('hook', '')}\n"
            "Welcome the listeners briefly and say in a line or two what the episode is about. Do not read out an agenda."
        )
    elif kind == "closing":
        takeaways = "; ".join(episode.get("takeaways", []))
        kind_rules = (
            "This is the closing. Tie the ideas together, bring these takeaways up naturally in conversation "
            f"(not as a list): {takeaways}. End with a short goodbye."
        )
        if episode.get("format") == "debate":
            kind_rules += " Before the goodbye, the hosts say plainly when each view holds, as the lesson suggests."
    else:
        kind_rules = (
            "Do not greet or say goodbye, and do not recap earlier segments. Deliver everything the segment's "
            "title and goal promise: if they name several things, each one gets real time."
        )
        lead = segment.get("lead")
        if episode.get("format") == "debate":
            kind_rules += (
                "\nDebate: the hosts hold opposing positions on this segment's question (Roee the first position "
                "in the beats, Noa the second). Each argues with the lesson's own reasoning and challenges the "
                "other directly; no neutral narration, no short feeder questions, and a host concedes a point only "
                "where the lesson supports it. This replaces the knowledge-gap rule."
            )
        elif lead in ("Roee", "Noa"):
            other = "Noa" if lead == "Roee" else "Roee"
            kind_rules += (
                f"\nIn this segment {lead} carries most of the explanation and {other} is the one working it out: "
                f"{other} asks, guesses, connects and pushes back, and also contributes at least one idea or example "
                f"of their own. This overrides the default host roles for this segment."
            )

    covered = "; ".join(covered_titles) if covered_titles else "nothing yet"
    previous = "\n".join(f"{line['speaker']}: {line['text']}" for line in previous_lines) or "(this is the start)"
    concepts = "\n".join(f"- {note}" for note in concept_notes) or "- (no specific concepts)"
    examples = "; ".join(segment.get("examples", [])) or "pick the most concrete material in the source"
    beats = "\n".join(f"- {beat}" for beat in segment.get("beats", [])) or "- (build your own arc: question, working it out, the click, takeaway)"
    focus = episode.get("focus", "")
    focus_line = f"The listener asked for this focus; keep it in mind: {focus}\n" if focus else ""
    # The episode's memory, so segment-by-segment writing does not repeat itself
    ledger = ""
    if used_examples:
        ledger += f"Examples and analogies already used (refer back by name if useful, do not re-explain): {'; '.join(used_examples)}\n"
    if used_phrases:
        ledger += f"Reactions and openers already used too often, avoid them in this segment: {', '.join(used_phrases)}\n"

    revision = ""
    if revision_issues:
        draft = "\n".join(f"{t['speaker']}: {t['text']}" for t in previous_draft or [])
        issues = "\n".join(f"- {issue}" for issue in revision_issues)
        revision = f"\nYour previous draft of this segment had these problems. Fix all of them:\n{issues}\n\nPrevious draft:\n{draft}\n"

    return f"""Episode: {episode.get('title', '')}. Format: {spec['label']}. {spec['style']}
Host roles: {roles}
Audience: {profile.get('audience', '')}; level: {profile.get('level', '')}.

Write segment {position[0]} of {position[1]} ({kind}): "{segment.get('title', '')}"
Goal: {segment.get('goal', '')}
Must cover:
{concepts}
Use these examples from the lesson: {examples}
Moments this segment should hit:
{beats}
{focus_line}{kind_rules}
Already covered in earlier segments: {covered}. Do not explain those again; refer back briefly if needed.
{ledger}
The previous segment ended with:
{previous}
Continue naturally from there.

Length: about {target_words} words in total (within 15%).
{revision}
SOURCE (lecture transcript excerpts, the only allowed source of facts):
{source}

Return JSON: {{"turns": [{{"speaker": "Roee", "text": "...", "delivery": "curious"}}, {{"speaker": "Noa", "text": "...", "delivery": ""}}]}}"""


# --------------------------------------------------------------------------- stage 4b: polish

POLISH_SYSTEM = (
    "You are the story editor of a top two-host explainer podcast. You get a finished episode that was "
    "written segment by segment and make it sound like one continuous conversation, with small surgical "
    "edits. You never add information. Reply with JSON only."
)


def polish_user_prompt(numbered_script: str, plan: Dict[str, Any], language: str) -> str:
    name = language_name(language)
    hebrew = (
        "- Hebrew: fix gender agreement (Roee is male, Noa is female, and they address each other accordingly), "
        "replace written or translated-sounding phrasing with natural spoken Israeli Hebrew.\n"
        if language != "en" else ""
    )
    return f"""Edit this {name} podcast episode ("{plan.get('title', '')}") so it plays as one natural conversation.

Fix, in this order of importance:
- Seams between segments: a segment that restarts, re-greets, re-introduces the topic or recaps what was just said. The first lines after a "##" heading should flow from the line before it.
- Repetition across the episode: the same reaction, opener or catchphrase used again and again (e.g. the same "wait, wait", "exactly", "great question", "that's fascinating"). Keep the first or best use and vary or drop the rest. Repeated praise of the other host's questions should go.
- Mechanical patterns: every segment ending the same way, lines that announce instead of explain ("let's talk about X"), hollow filler.
- Callbacks: where a later line could name an example or image the hosts already used earlier, reword it to refer back to it (only to things already said in the episode).
{hebrew}- Delivery hints that do not match the line.

Rules:
- Only "replace" (reword one line, same speaker, same meaning, about the same length) or "delete" (only a short reaction or filler line). Never add new lines.
- Never add or change facts, numbers, names, definitions or examples. Do not touch explanations except to remove repetition or fix language.
- Make only edits that clearly help; at most one edit per four lines. Most lines should stay exactly as they are.

Return JSON:
{{"edits": [{{"line": <line number>, "action": "replace", "text": "the new line", "delivery": "optional hint", "why": "seam | repetition | mechanical | callback | language | delivery"}},
           {{"line": <line number>, "action": "delete", "why": "repetition"}}],
  "notes": ["up to 3 short notes on what else is weak in the episode, for the producer"]}}

EPISODE:
{numbered_script}"""


# --------------------------------------------------------------------------- stage 5: verify

VERIFY_SYSTEM = "You are a strict fact-checker for an educational podcast. Reply with JSON only."


def verify_user_prompt(numbered_dialogue: str, source: str) -> str:
    return f"""SOURCE is part of a lecture transcript. DIALOGUE is a podcast segment written from it.
Find statements in the DIALOGUE that present facts (numbers, names, dates, definitions, rules, cause-and-effect claims) that the SOURCE does not support.
Ignore greetings, reactions, questions, opinions, paraphrases of what the source says, and anything clearly presented as an analogy or an example.
severity "high": contradicts the source or invents a specific fact. severity "low": plausible general knowledge that is not in the source.

Return JSON: {{"unsupported": [{{"turn": <turn number>, "claim": "...", "severity": "high"}}]}}
Return an empty list when everything is supported.

SOURCE:
{source}

DIALOGUE:
{numbered_dialogue}"""
