"""Stage 3: plan the episode (format, length, segments) from the lesson profile.

The plan is saved as JSON before any script is written, so it can be reviewed and
edited; the writer always follows the saved plan.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..utils.logging import get_logger
from .llm import LLMClient, LLMError
from .coerce import clamp_int, str_list, text
from .prompts import FORMATS, PLAN_SYSTEM, plan_user_prompt
from .transcript import CleanTranscript

SPEECH_WORDS_PER_MINUTE = {"he": 135, "en": 150}
MIN_SEGMENT_WORDS = 60
INTRO_SHARE = 0.07
CLOSING_SHARE = 0.08
SEGMENT_KINDS = ("intro", "body", "closing")
BRIEF_MINUTES = 5.0  # a "brief" episode, unless the listener sets a length


def target_minutes(lecture_minutes: float, override: float = 0) -> float:
    """Episode length: about 30% of the lecture, 6-30 minutes.

    Very short sources get a short episode instead of being padded to 6 minutes,
    because padding is exactly where invented content comes from.
    """
    if override and override > 0:
        return round(min(max(override, 2.0), 60.0), 1)
    floor = min(6.0, max(2.0, lecture_minutes))
    return round(min(max(lecture_minutes * 0.3, floor), 30.0), 1)


def body_segment_range(minutes: float) -> tuple[int, int]:
    count = max(1, min(10, round(minutes / 3.5)))
    return max(1, count - 1), count + 1


def suggest_format(profile: Dict[str, Any]) -> str:
    if profile.get("lesson_type") == "story":
        return "story"
    if profile.get("lesson_type") == "discussion":
        return "debate"
    if profile.get("level") == "intro":
        return "beginner_explainer"
    return "deep_dive"


def _segment(raw: Dict[str, Any], concept_names: Dict[str, str], valid_ids: set) -> Dict[str, Any]:
    kind = text(raw.get("kind")).lower()
    concepts = []
    for name in str_list(raw.get("concepts")):
        canonical = concept_names.get(name.lower())
        if canonical and canonical not in concepts:
            concepts.append(canonical)
    sources = []
    for sid in str_list(raw.get("source_sections")):
        if sid in valid_ids and sid not in sources:
            sources.append(sid)
    return {
        "kind": kind if kind in SEGMENT_KINDS else "body",
        "title": text(raw.get("title")),
        "goal": text(raw.get("goal")),
        "concepts": concepts,
        "source_sections": sources,
        "examples": str_list(raw.get("examples")),
        "beats": str_list(raw.get("beats"))[:4],
        "weight": clamp_int(raw.get("weight"), 1, 3, 2),
    }


def normalize_plan(
    raw: Dict[str, Any],
    profile: Dict[str, Any],
    transcript: CleanTranscript,
    minutes: float,
    language: str,
    forced_format: Optional[str] = None,
    focus: str = "",
) -> Dict[str, Any]:
    """Make the plan complete and consistent no matter what the model returned."""
    section_ids = transcript.section_ids()
    valid_ids = set(section_ids)
    concept_names = {c["name"].lower(): c["name"] for c in profile["concepts"]}
    concept_sections = {c["name"]: c["section_ids"] for c in profile["concepts"]}

    fmt = forced_format or text(raw.get("format"))
    if fmt not in FORMATS or (not forced_format and FORMATS[fmt].get("manual_only")):
        fmt = suggest_format(profile)

    segments = [_segment(s, concept_names, valid_ids) for s in raw.get("segments") or [] if isinstance(s, dict)]
    body = [s for s in segments if s["kind"] == "body"]
    if not body:
        raise LLMError("Episode plan has no body segments.")
    intro = next((s for s in segments if s["kind"] == "intro"), None) or {
        "kind": "intro", "title": text(raw.get("title")) or profile["title"], "goal": "",
        "concepts": [], "source_sections": [], "examples": [], "beats": [], "weight": 1,
    }
    closing = next((s for s in reversed(segments) if s["kind"] == "closing"), None) or {
        "kind": "closing", "title": "", "goal": "", "concepts": [], "source_sections": [], "examples": [],
        "beats": [], "weight": 1,
    }

    # Every core concept must be taught somewhere: attach any the model skipped to the
    # body segment that draws on the same part of the lecture.
    covered = {name for seg in body for name in seg["concepts"]}
    for concept in profile["concepts"]:
        if concept["importance"] < 3 or concept["name"] in covered:
            continue
        wanted = set(concept["section_ids"])
        best = max(body, key=lambda seg: len(wanted & set(seg["source_sections"])))
        best["concepts"].append(concept["name"])
        best["source_sections"].extend(sid for sid in concept["section_ids"] if sid not in best["source_sections"])
        covered.add(concept["name"])

    # A body segment must have source text to stay grounded in
    for index, seg in enumerate(body):
        if not seg["source_sections"]:
            for name in seg["concepts"]:
                seg["source_sections"].extend(s for s in concept_sections.get(name, []) if s not in seg["source_sections"])
        if not seg["source_sections"]:
            share = max(1, len(section_ids) // len(body))
            seg["source_sections"] = section_ids[index * share : (index + 1) * share] or section_ids[-1:]
        seg["source_sections"].sort(key=section_ids.index)

    plan = {
        "format": fmt,
        "title": text(raw.get("title")) or profile["title"],
        "hook": text(raw.get("hook")),
        "takeaways": str_list(raw.get("takeaways"))[:5],
        "focus": focus,
        "target_minutes": minutes,
        "host_roles": FORMATS[fmt]["roles"],
        "segments": [intro, *body, closing],
    }
    return assign_word_budget(plan, language)


def assign_word_budget(plan: Dict[str, Any], language: str) -> Dict[str, Any]:
    """Split target_minutes into per-segment word targets by weight, and renumber segments.

    Also used after the plan is edited (length, weights, removed or reordered segments).
    """
    segments = plan["segments"]
    body = [seg for seg in segments if seg.get("kind") == "body"]
    edges = [seg for seg in segments if seg.get("kind") != "body"]
    if not body:
        raise LLMError("Episode plan has no body segments.")

    wpm = SPEECH_WORDS_PER_MINUTE.get(language, 135)
    total_words = round(float(plan["target_minutes"]) * wpm)
    for seg in edges:
        share = INTRO_SHARE if seg.get("kind") == "intro" else CLOSING_SHARE
        seg["target_words"] = max(MIN_SEGMENT_WORDS, round(total_words * share))
    body_words = max(total_words - sum(seg["target_words"] for seg in edges), MIN_SEGMENT_WORDS * len(body))
    for seg in body:
        seg["weight"] = clamp_int(seg.get("weight"), 1, 3, 2)
    weight_sum = sum(seg["weight"] for seg in body)
    for seg in body:
        seg["target_words"] = max(MIN_SEGMENT_WORDS, round(body_words * seg["weight"] / weight_sum))

    for number, seg in enumerate(segments, start=1):
        seg["id"] = f"P{number}"
    # Who carries each body segment's explanation alternates, so neither host is stuck as the
    # permanent examiner or student (debates already split the floor by position)
    for index, seg in enumerate(body):
        if plan.get("format") == "debate":
            seg.pop("lead", None)
        else:
            seg["lead"] = "Roee" if index % 2 == 0 else "Noa"
    plan["target_words"] = sum(seg["target_words"] for seg in segments)
    plan["host_roles"] = FORMATS.get(plan.get("format"), FORMATS["deep_dive"])["roles"]
    return plan


class EpisodePlanner:
    def __init__(
        self,
        llm: LLMClient,
        language: str,
        format_override: str = "auto",
        minutes_override: float = 0,
        log: Optional[Callable[[str], None]] = None,
        focus: str = "",
    ) -> None:
        self.llm = llm
        self.language = language
        self.forced_format = format_override if format_override in FORMATS else None
        self.minutes_override = minutes_override
        self.focus = focus.strip()
        self.log = log or (lambda _msg: None)
        self.logger = get_logger(self.__class__.__name__)

    def run(self, profile: Dict[str, Any], transcript: CleanTranscript) -> Dict[str, Any]:
        minutes = target_minutes(transcript.lecture_minutes, self.minutes_override)
        if self.forced_format == "brief" and not self.minutes_override:
            minutes = min(minutes, BRIEF_MINUTES)
        section_list = "\n".join(f"{s.id}: {s.title} ({len(s.text.split())})" for s in transcript.sections)
        raw = self.llm.chat_json(
            "plan",
            PLAN_SYSTEM,
            plan_user_prompt(
                profile,
                section_list,
                self.language,
                minutes,
                body_segment_range(minutes),
                suggest_format(profile),
                self.forced_format,
                self.focus,
            ),
            max_tokens=3500,
            temperature=0.4,
        )
        plan = normalize_plan(raw, profile, transcript, minutes, self.language, self.forced_format, self.focus)
        self.log(
            f"Stage 3: format={plan['format']}, ~{plan['target_minutes']:g} min "
            f"(lecture ~{transcript.lecture_minutes:.0f} min), {len(plan['segments'])} segments"
        )
        return plan


def concept_lookup(profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["name"]: c for c in profile["concepts"]}


def segment_concepts(plan_segment: Dict[str, Any], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    lookup = concept_lookup(profile)
    return [lookup[name] for name in plan_segment.get("concepts", []) if name in lookup]
