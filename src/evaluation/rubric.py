"""The critic: an English rubric for judging a finished podcast script against its lesson.

Ten dimensions, each scored 1-10 against fixed anchors so scores stay comparable across
runs. Faithfulness gates the overall score: a script that invents facts cannot score
well however lively it is. Every score must cite evidence from the script.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

DIMENSIONS: List[Dict[str, Any]] = [
    {"key": "faithfulness", "weight": 2.0, "name": "Faithfulness to the source",
     "anchors": "2 = invents facts, numbers or names; 5 = mostly grounded, a few unsupported specifics; "
                "8 = everything checkable is in the source, analogies are clearly framed; 10 = flawless, nuance preserved"},
    {"key": "coverage", "weight": 1.5, "name": "Coverage of the lesson",
     "anchors": "2 = misses the core ideas; 5 = core ideas present but thin or one missing; "
                "8 = every core idea with fitting depth; 10 = complete, and the depth is proportioned to importance"},
    {"key": "pedagogy", "weight": 1.5, "name": "Teaching quality",
     "anchors": "2 = definitions recited, no understanding built; 5 = clear but abstract, few examples; "
                "8 = concrete before abstract, builds step by step, misconceptions handled; "
                "10 = a student could pass a quiz on it after one listen"},
    {"key": "dialogue_dynamics", "weight": 1.2, "name": "Two-host dynamics",
     "anchors": "2 = interviewer and expert ping-pong or monologues; 5 = some back-and-forth, reactions feel scripted; "
                "8 = both hosts contribute ideas, react and build on each other; "
                "10 = indistinguishable from two sharp friends genuinely working it out"},
    {"key": "engagement", "weight": 1.0, "name": "Engagement",
     "anchors": "2 = flat, no reason to keep listening; 5 = some hooks; "
                "8 = strong hook, curiosity gaps, real 'aha' moments; 10 = hard to stop listening"},
    {"key": "structure", "weight": 1.0, "name": "Structure and flow",
     "anchors": "2 = disjointed; 5 = logical but abrupt transitions, little signposting; "
                "8 = clear arc, signposting, smooth transitions, crisp recap; 10 = every segment earns its place"},
    {"key": "language_quality", "weight": 1.5, "name": "Spoken Hebrew quality",
     "anchors": "Judged in the podcast's language, which is Hebrew unless English was chosen. "
                "2 = translationese, formal or literary register, gender-agreement errors; 5 = correct but stiff, "
                "sounds written; 8 = natural spoken Israeli Hebrew, correct gender, idiomatic reactions, English terms "
                "only where Israelis use them; 10 = indistinguishable from two native Israeli hosts"},
    {"key": "fit", "weight": 1.0, "name": "Fit to the request",
     "anchors": "2 = ignores the requested format, level, focus or length; 5 = partly fits; "
                "8 = format, audience level, focus and length all respected; 10 = feels made for exactly this listener"},
    {"key": "audio_readiness", "weight": 0.8, "name": "Ready for speech synthesis",
     "anchors": "2 = markup, symbols, unreadable numbers, very long lines; 5 = a few things a voice would stumble on; "
                "8 = everything speakable, sensible delivery hints; 10 = written for the ear, line by line. "
                "Calibration (tested on this product's voices): English terms and acronyms in Latin letters inside "
                "Hebrew (DNA, QRS, AV node, overfitting) are pronounced correctly; do not penalize them or ask for "
                "Hebrew transliteration. Do penalize digits, symbols, formulas or code read character by character, "
                "Hebrew abbreviations with gershayim a voice may not expand (e.g. אק\"ג), lone single-letter variables, "
                "and very long sentences."},
    {"key": "notebooklm_parity", "weight": 1.3, "name": "Parity with a top AI audio overview",
     "anchors": "2 = clearly worse on every axis; 5 = noticeably weaker; 8 = comparable; "
                "10 = better than a top-tier NotebookLM Audio Overview of the same lesson in the same language"},
]
DIMENSION_KEYS = [d["key"] for d in DIMENSIONS]
FAITHFULNESS_GATE = 4  # at or below this, the overall score is capped
GATED_CAP = 4.0

CRITIC_SYSTEM = """You are an exacting critic of educational audio. You have produced and reviewed hundreds of two-host explainer podcasts and know what the best AI audio overviews (NotebookLM) sound like. You judge a podcast SCRIPT generated from a lesson, against that lesson and against what the listener asked for.

How you work:
- Read the source first, then the request, then the script.
- Score each dimension from 1 to 10 using its anchors. Be strict and calibrated: 8 means genuinely good, 10 is rare. Do not reward length.
- Every score needs evidence: quote the script exactly (in its original language) or point to what is missing from the source.
- Faithfulness: any specific fact, number, name, date or definition that is not in the source counts against it, unless it is clearly framed as an analogy or general aside.
- Judge the script as it will be heard: two voices, no visuals.
- The product is built for Hebrew listeners. Unless the request says the podcast language is English, the script must be natural spoken Israeli Hebrew, even when the lecture was in English; a script that reads like a translation loses points on language quality and NotebookLM parity.
- Recommendations must be concrete changes to the generating pipeline (which stage, what to change in its prompt or settings), not generic advice.

Reply with JSON only."""


def _format_style(key: str) -> str:
    from ..engine.prompts import FORMATS

    if key in FORMATS:
        return FORMATS[key]["style"]
    return "the pipeline picks the format that suits the lesson; judge whether its choice fits"


def critic_user_prompt(
    scenario: Dict[str, Any],
    source: str,
    profile: Dict[str, Any],
    plan: Dict[str, Any],
    dialogue: Dict[str, Any],
    report: Dict[str, Any],
) -> str:
    anchors = "\n".join(f"- {d['key']} ({d['name']}): {d['anchors']}" for d in DIMENSIONS)
    chapters = {c["start_index"]: c["title"] for c in dialogue.get("metadata", {}).get("chapters", [])}
    lines = []
    for index, turn in enumerate(dialogue.get("dialogue", [])):
        if index in chapters:
            lines.append(f"\n## {chapters[index]}")
        delivery = f" ({turn['delivery']})" if turn.get("delivery") else ""
        lines.append(f"[{index + 1}] {turn['speaker']}{delivery}: {turn['text']}")
    request = {
        "lesson": scenario.get("title"),
        "lecture_language": scenario.get("language", "he"),
        "podcast_language": scenario.get("output_language", "he"),
        "requested_format": scenario.get("format", "auto"),
        "what_that_format_means": _format_style(scenario.get("format", "auto")),
        "audience_level": scenario.get("level"),
        "requested_minutes": scenario.get("target_minutes") or "automatic (about 30% of the lecture)",
        "listener_focus": scenario.get("focus") or "none",
        "what_this_scenario_tests": scenario.get("tests"),
    }
    metrics = {k: report.get(k) for k in (
        "format", "target_minutes", "estimated_minutes", "core_concept_coverage", "unsupported_high", "segments_with_open_issues",
        "polish_edits", "overused_phrases",
    )}
    return f"""Judge this podcast script.

REQUEST:
{json.dumps(request, ensure_ascii=False, indent=1)}

DIMENSIONS AND ANCHORS:
{anchors}

Return JSON:
{{
  "scores": {{"<dimension key>": {{"score": <1-10>, "evidence": "exact quote or what is missing", "why": "one sentence"}}}},
  "verdict": "ship | revise | fail",
  "strengths": ["up to 3, each with a quote"],
  "issues": [{{"severity": "critical | major | minor", "dimension": "<key>", "line": <script line number or null>, "quote": "exact quote", "problem": "...", "fix": "..."}}],
  "pipeline_recommendations": [{{"stage": "clean | profile | plan | write | verify | audio", "change": "a concrete change to that stage's prompt or settings", "expected_gain": "which dimension improves and why"}}]
}}
Give at most 6 issues (most severe first) and at most 4 pipeline recommendations.

AUTOMATIC CHECKS (from the pipeline itself):
{json.dumps(metrics, ensure_ascii=False)}

LESSON ANALYSIS (what the pipeline understood):
{json.dumps({k: profile.get(k) for k in ("title", "summary", "level", "lesson_type", "learning_objectives")}, ensure_ascii=False)}
Core concepts: {", ".join(c["name"] for c in profile.get("concepts", []) if c.get("importance") == 3)}

EPISODE PLAN:
{json.dumps([{k: s.get(k) for k in ("kind", "title", "goal", "concepts", "beats", "target_words")} for s in plan.get("segments", [])], ensure_ascii=False)}

SOURCE (lecture transcript):
{source}

SCRIPT:
{chr(10).join(lines)}"""


SUMMARY_SYSTEM = (
    "You are the lead producer improving an automated two-host educational podcast pipeline. "
    "You read critic reviews across many test lessons and decide what to change first. Reply with JSON only."
)


def summary_user_prompt(results: List[Dict[str, Any]], averages: Dict[str, float]) -> str:
    compact = [
        {
            "scenario": r["scenario"]["id"],
            "tests": r["scenario"].get("tests"),
            "overall": r.get("overall"),
            "weakest": sorted(r.get("scores", {}).items(), key=lambda kv: kv[1])[:3],
            "issues": [{k: i.get(k) for k in ("severity", "dimension", "problem", "fix")} for i in r.get("issues", [])[:4]],
            "recommendations": r.get("pipeline_recommendations", [])[:4],
        }
        for r in results
    ]
    return f"""Average scores per dimension (1-10): {json.dumps(averages)}

Critic reviews per scenario:
{json.dumps(compact, ensure_ascii=False)}

Find the patterns that repeat across scenarios and turn them into the 10 changes with the highest expected gain, most important first. Prefer one change that fixes many scenarios over many narrow fixes.

Return JSON:
{{
  "diagnosis": "3-5 sentences: where the pipeline stands and what holds it back most",
  "improvements": [{{"rank": 1, "stage": "clean | profile | plan | write | verify | audio", "change": "concrete change", "fixes": ["dimension keys"], "scenarios": ["ids it would help"], "effort": "small | medium | large"}}]
}}"""


def weighted_overall(scores: Dict[str, float]) -> float:
    """Weighted mean of the dimension scores, capped when faithfulness fails the gate."""
    total = sum(d["weight"] for d in DIMENSIONS if d["key"] in scores)
    if not total:
        return 0.0
    value = sum(scores[d["key"]] * d["weight"] for d in DIMENSIONS if d["key"] in scores) / total
    if scores.get("faithfulness", 10) <= FAITHFULNESS_GATE:
        value = min(value, GATED_CAP)
    return round(value, 2)
