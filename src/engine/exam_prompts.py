"""Prompts for exam mode. Instructions are in English (models follow them most reliably); every
question, option and explanation is requested in the exam's language.

The writing prompt follows the error-modelling method for wrong options (solve first, list specific
student errors, derive the wrong answer each error produces, keep the most plausible), seeded from the
lesson analysis. The checking prompt is a blind solver: it never sees which option is correct.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .prompts import language_name

BLOOM_BANDS = {
    "easy": ("remember", "understand"),
    "medium": ("understand", "apply", "analyze"),
    "hard": ("analyze", "evaluate", "create"),
}

DIFFICULTY_TASKS = {
    "easy": "Recall or explain one concept exactly as the lecture defines it, from one section. "
            "Wrong options are other concepts from the same lecture, clearly different on reflection.",
    "medium": "Apply or interpret one concept in a short NEW example or scenario that the lecture did not give "
              "word for word. Wrong options are what a student who holds one of the listed misconceptions would answer.",
    "hard": "Analyze or evaluate: combine two concepts (from different lectures when available) or reason through two "
            "steps. Wrong options are near-misses that are each partly true but fail on one specific point.",
}

STYLES = {
    "final": "a university final exam: precise, formal, one clear task per question",
    "quick": "a quick self-check after the lecture: short, friendly stems",
    "oral": "preparation for an oral exam: open questions that ask to explain and justify",
}

_HEBREW_RULES = (
    "Write natural, correct Modern Hebrew as an Israeli lecturer would, not translated phrasing. "
    "All options of a question must agree in grammatical gender, number and definiteness (the definite article ה) "
    "so grammar never gives the answer away. Keep English technical terms where the lecture uses them, "
    "with the Hebrew term first when one exists. "
)

TOPICS_SYSTEM = "You merge course concepts across lectures for an exam. Reply with JSON only."


def topics_prompt(concepts: List[Dict[str, Any]], language: str) -> str:
    return f"""Below are the key concepts of several lectures of one course, each with an id.
Group concepts that are the SAME idea (same meaning, possibly different wording or language) into one topic.
Do not merge concepts that are merely related. Every id must appear in exactly one topic.

Return JSON: {{"topics": [{{"name": "...", "definition": "...", "members": ["L1#0", "L2#3"]}}]}}
Write names and definitions in {language_name(language)}.

CONCEPTS:
{json.dumps(concepts, ensure_ascii=False, indent=1)}"""


WRITE_SYSTEM = (
    "You are an expert university examiner and item writer. You write exam questions that test real understanding, "
    "are answerable from the given lecture sources alone, and follow item-writing best practice. Reply with JSON only."
)


def write_prompt(topic: Dict[str, Any], items: List[Dict[str, str]], sources: str, language: str,
                 num_options: int, style: str, instructions: str, avoid: Optional[List[str]] = None) -> str:
    wanted = "\n".join(f"{n}. type={i['type']}, difficulty={i['difficulty']}: {DIFFICULTY_TASKS[i['difficulty']]}"
                       for n, i in enumerate(items, start=1))
    seeds = "\n".join(f"- {m}" for m in topic.get("misconceptions", [])[:8]) or "- (none listed: infer typical student errors)"
    neighbours = ", ".join(topic.get("neighbours", [])[:8]) or "(none)"
    hebrew = _HEBREW_RULES if language == "he" else ""
    extra = f"\nThe student also asks: {instructions}\n" if instructions else ""
    if avoid:
        extra += ("\nALREADY ASKED in the student's earlier sets. Do not repeat or reword these; ask about a different "
                  "aspect, example or step of the topic:\n" + "\n".join(f"- {a}" for a in avoid) + "\n")
    return f"""Write {len(items)} exam question(s) about the topic below, for {STYLES.get(style, STYLES['final'])}.
Write every question, option, explanation and answer in {language_name(language)}. {hebrew}

TOPIC: {topic['name']}
DEFINITION FROM THE LECTURE: {topic.get('definition', '')}
NEIGHBOURING CONCEPTS (good sources of wrong options): {neighbours}
KNOWN STUDENT MISCONCEPTIONS (seed wrong options from these):
{seeds}
{extra}
QUESTIONS TO WRITE, in this order:
{wanted}

HOW TO WRITE A MULTIPLE-CHOICE QUESTION (type=mcq):
1. Write a focused stem: a complete question that a student could answer without seeing the options. End it with "?".
   Prefer positive wording. If a negative is unavoidable, mark it as **לא** / **NOT**.
2. Solve it yourself from the SOURCES. The correct answer must be stated or directly implied there.
3. List specific student errors for this question (from the misconceptions, the neighbouring concepts, or a typical
   reasoning slip). For each error, derive the exact wrong answer a student making it would choose.
4. Keep the {num_options - 1} most plausible wrong answers. Each must be clearly wrong under any reasonable reading,
   yet attractive to a student with that error. Never use "all of the above", "none of the above", combinations
   ("A and B"), or true/false options. Keep all options similar in length and form; the correct answer must not be
   the longest or the most detailed.
5. Do NOT assign letters or positions: return the correct answer and the wrong answers as separate fields.

HOW TO WRITE AN OPEN QUESTION (type=open): a question that asks to explain, compare, apply or justify; a model answer
of 2-5 sentences grounded in the sources; a rubric of 2-5 scoring points with weights that sum to 10.

Every question cites the source section(s) it is based on, by id, with a SHORT exact quote (copied character for
character, not translated or reworded, up to 15 words) from that section.
Write plain text only: no Markdown, no LaTeX or $...$ math; write symbols and variables as plain letters (n, p, R²).

Return JSON:
{{"questions": [
  {{"type": "mcq", "difficulty": "easy|medium|hard", "bloom": "remember|understand|apply|analyze|evaluate|create",
    "stem": "...?", "correct_answer": "...",
    "distractors": [{{"text": "...", "error": "the student error it models", "why_wrong": "one sentence"}}],
    "explanation": "why the correct answer is right",
    "sources": [{{"section": "L1:S3", "quote": "..."}}]}},
  {{"type": "open", "difficulty": "...", "bloom": "...", "stem": "...?", "model_answer": "...",
    "rubric": [{{"point": "...", "weight": 4}}], "sources": [{{"section": "...", "quote": "..."}}]}}
]}}

SOURCES (lecture sections; ids in square brackets):
{sources}"""


CHECK_SYSTEM = (
    "You are a strict exam reviewer. You solve each question from the given lecture sources only, without knowing "
    "the intended answer, and report problems. Reply with JSON only."
)


def check_prompt(questions: List[Dict[str, Any]], sources: str) -> str:
    return f"""Review each question below using ONLY the SOURCES.

For a multiple-choice question:
- "defensible": list EVERY option letter that a careful student could defend as correct from the sources
  (usually exactly one; list more if two options are both right; list none if no option is supported).
- "section": the source section id that supports your answer.
- "equivalent": pairs of option letters that mean the same thing, if any.
- "bloom": the cognitive level the question really tests (remember, understand, apply, analyze, evaluate, create).
- "issues": short notes on real problems only (ambiguous stem, a cue that gives the answer away, grammar that only
  fits one option, a question the sources do not cover). Empty when fine.

For an open question: "answerable" (can the sources answer it?), "answer_supported" (is the model answer correct and
supported by the sources?), "bloom", "issues".

Return JSON: {{"reviews": [{{"id": "q1", "defensible": ["B"], "section": "L1:S3", "equivalent": [], "bloom": "apply",
"issues": []}}, {{"id": "q2", "answerable": true, "answer_supported": true, "bloom": "analyze", "issues": []}}]}}

QUESTIONS:
{json.dumps(questions, ensure_ascii=False, indent=1)}

SOURCES:
{sources}"""


REPAIR_SYSTEM = WRITE_SYSTEM


def repair_prompt(question: Dict[str, Any], problems: List[str], sources: str, language: str, num_options: int) -> str:
    hebrew = _HEBREW_RULES if language == "he" else ""
    return f"""This exam question failed review. Rewrite it so that every problem is fixed, keeping its topic,
type and difficulty. Write in {language_name(language)}. {hebrew}
For a multiple-choice question: exactly one option may be defensible from the sources, with {num_options - 1}
plausible wrong answers derived from specific student errors; no letters; similar option lengths.

PROBLEMS:
{chr(10).join('- ' + p for p in problems)}

QUESTION:
{json.dumps(question, ensure_ascii=False, indent=1)}

Return JSON with the same fields as the question (type, difficulty, bloom, stem, correct_answer, distractors
[text, error, why_wrong], explanation, model_answer, rubric, sources [section, quote]).

SOURCES:
{sources}"""
