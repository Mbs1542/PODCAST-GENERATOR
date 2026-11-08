"""Stage 2: analyse the lesson (concepts, the lecturer's examples, facts, level, type).

This replaces the keyword classifier. The profile is what lets every later stage
adapt to the specific lesson instead of to one of six hard-coded categories.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from ..utils.logging import get_logger
from .coerce import clamp_int, str_list, text
from .llm import LLMClient, LLMError
from .prompts import PROFILE_SYSTEM, profile_merge_prompt, profile_user_prompt
from .transcript import CleanTranscript, Section

# The whole lesson goes to the model in one pass up to this size (roughly a 2.5-hour lecture);
# longer lessons are analysed in parts and merged.
SINGLE_PASS_CHARS = 120_000
PART_CHARS = 40_000

LEVELS = ("intro", "intermediate", "advanced")
LESSON_TYPES = ("lecture", "tutorial", "workshop", "discussion", "story")


def render_sections(sections: Iterable[Section]) -> str:
    return "\n\n".join(f"[{s.id}] {s.title}\n{s.text}" for s in sections)


def _group_sections(sections: List[Section], max_chars: int) -> List[List[Section]]:
    groups: List[List[Section]] = []
    current: List[Section] = []
    size = 0
    for section in sections:
        if current and size + len(section.text) > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(section)
        size += len(section.text)
    if current:
        groups.append(current)
    return groups


def normalize_profile(raw: Dict[str, Any], valid_section_ids: List[str]) -> Dict[str, Any]:
    """Coerce a model's profile into the exact shape later stages rely on."""
    valid = set(valid_section_ids)

    concepts: List[Dict[str, Any]] = []
    seen = set()
    for item in raw.get("concepts") or []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = text(item.get("name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        concepts.append(
            {
                "name": name,
                "definition": text(item.get("definition")),
                "depends_on": str_list(item.get("depends_on")),
                "section_ids": [sid for sid in str_list(item.get("section_ids")) if sid in valid],
                "importance": clamp_int(item.get("importance"), 1, 3, 2),
            }
        )
    if not concepts:
        raise LLMError("Lesson analysis returned no concepts.")

    examples = []
    for item in raw.get("examples") or []:
        if isinstance(item, dict) and text(item.get("description")):
            section_id = text(item.get("section_id"))
            examples.append(
                {
                    "description": text(item.get("description")),
                    "illustrates": text(item.get("illustrates")),
                    "section_id": section_id if section_id in valid else "",
                }
            )

    key_facts = []
    for item in raw.get("key_facts") or []:
        if isinstance(item, dict) and text(item.get("claim")):
            section_id = text(item.get("section_id"))
            key_facts.append({"claim": text(item.get("claim")), "section_id": section_id if section_id in valid else ""})

    level = text(raw.get("level")).lower()
    lesson_type = text(raw.get("lesson_type")).lower()
    return {
        "title": text(raw.get("title")),
        "summary": text(raw.get("summary")),
        "domain": text(raw.get("domain")),
        "level": level if level in LEVELS else "intermediate",
        "lesson_type": lesson_type if lesson_type in LESSON_TYPES else "lecture",
        "audience": text(raw.get("audience")),
        "learning_objectives": str_list(raw.get("learning_objectives")),
        "concepts": concepts,
        "examples": examples,
        "key_facts": key_facts,
        "misconceptions": str_list(raw.get("misconceptions")),
        "open_questions": str_list(raw.get("open_questions")),
    }


def metadata_hints(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The user's own description of the lesson, passed to the model as a hint."""
    hints = {
        key: metadata[key]
        for key in ("topic", "summary", "key_concepts")
        if metadata.get(key) and metadata.get(key) != "azure"
    }
    return hints or None


class LessonProfiler:
    def __init__(self, llm: LLMClient, language: str, log: Optional[Callable[[str], None]] = None) -> None:
        self.llm = llm
        self.language = language
        self.log = log or (lambda _msg: None)
        self.logger = get_logger(self.__class__.__name__)

    def run(self, transcript: CleanTranscript, hints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        source = render_sections(transcript.sections)
        if len(source) <= SINGLE_PASS_CHARS:
            raw = self.llm.chat_json(
                "profile",
                PROFILE_SYSTEM,
                profile_user_prompt(source, self.language, hints),
                max_tokens=4000,
                temperature=0.2,
            )
        else:
            groups = _group_sections(transcript.sections, PART_CHARS)
            self.logger.info("Long lesson: analysing %d parts", len(groups))
            partials = [
                self.llm.chat_json(
                    "profile",
                    PROFILE_SYSTEM,
                    profile_user_prompt(render_sections(group), self.language, hints, part=(i, len(groups))),
                    max_tokens=3000,
                    temperature=0.2,
                )
                for i, group in enumerate(groups, start=1)
            ]
            raw = self.llm.chat_json(
                "profile_merge",
                PROFILE_SYSTEM,
                profile_merge_prompt(partials, self.language),
                max_tokens=6000,
                temperature=0.2,
            )
        profile = normalize_profile(raw, transcript.section_ids())
        core = sum(1 for c in profile["concepts"] if c["importance"] == 3)
        self.log(
            f"Stage 2: {profile['domain'] or 'unknown domain'}, level={profile['level']}, "
            f"type={profile['lesson_type']}, {len(profile['concepts'])} concepts ({core} core), "
            f"{len(profile['examples'])} lecturer examples"
        )
        return profile
