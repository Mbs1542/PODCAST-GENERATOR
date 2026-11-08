"""Stage 4: write the script one planned segment at a time.

Each call sees the full source text of the sections the segment draws on (not a
summary), the end of the previous segment for continuity, and what was already
covered. There is no global length cap: length comes from the plan.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .coerce import text
from .llm import LLMClient
from .planner import segment_concepts
from .prompts import write_system_prompt, write_user_prompt
from .transcript import CleanTranscript

SPEAKERS = ("Roee", "Noa")
SOURCE_MAX_CHARS = 24_000
_SPEAKER_ALIASES = {"roee": "Roee", "רועי": "Roee", "noa": "Noa", "נועה": "Noa"}
_DELIVERY_STRIP_RE = re.compile(r"[^\w\s,\-]", re.U)

Turn = Dict[str, str]


def segment_source(segment: Dict[str, Any], transcript: CleanTranscript, profile: Dict[str, Any]) -> str:
    """The text a segment may draw facts from.

    Body segments get their transcript sections verbatim. The opening and closing
    have no sections of their own, so they get the lesson's summary, facts and
    definitions, which were extracted from the transcript.
    """
    if segment.get("source_sections"):
        sections = [transcript.get(sid) for sid in segment["source_sections"]]
        source = "\n\n".join(f"[{s.id}] {s.title}\n{s.text}" for s in sections if s)
    else:
        lines = [profile.get("summary", "")]
        lines += [f"- {fact['claim']}" for fact in profile.get("key_facts", [])]
        lines += [f"- {c['name']}: {c['definition']}" for c in profile.get("concepts", []) if c.get("definition")]
        source = "\n".join(line for line in lines if line)
    if len(source) > SOURCE_MAX_CHARS:
        source = source[:SOURCE_MAX_CHARS] + "\n[...]"
    return source


def normalize_turns(raw: Dict[str, Any]) -> List[Turn]:
    turns: List[Turn] = []
    for item in raw.get("turns") or raw.get("dialogue") or []:
        if not isinstance(item, dict):
            continue
        body = " ".join(text(item.get("text")).split())
        if not body:
            continue
        speaker = text(item.get("speaker"))
        turn = {"speaker": _SPEAKER_ALIASES.get(speaker.lower(), speaker), "text": body}
        # The voice hint is embedded in provider syntax later ([...] / style), so keep it plain
        delivery = " ".join(_DELIVERY_STRIP_RE.sub("", text(item.get("delivery"))).split())[:40]
        if delivery:
            turn["delivery"] = delivery
        turns.append(turn)
    return turns


_PUNCT_RE = re.compile(r"[^\w\s]", re.U)


def overused_phrases(turns: List[Turn], limit: int = 10) -> List[str]:
    """Openers and short reactions the episode already leans on: two-word openers used 3+ times,
    and whole short turns ("רגע, רגע.", "בדיוק.") used twice or more."""
    from collections import Counter

    openers: Counter = Counter()
    short: Counter = Counter()
    for turn in turns:
        words = _PUNCT_RE.sub(" ", turn["text"]).split()
        if not words:
            continue
        if len(words) <= 3:
            short[" ".join(words)] += 1
        elif len(words) >= 2:
            openers[" ".join(words[:2])] += 1
    phrases = [p for p, n in short.most_common() if n >= 2] + [p for p, n in openers.most_common() if n >= 3]
    return phrases[:limit]


def used_examples(plan: Dict[str, Any], index: int) -> List[str]:
    seen: List[str] = []
    for segment in plan["segments"][:index]:
        for example in segment.get("examples", []):
            if example and example not in seen:
                seen.append(example)
    return seen[:12]


class SegmentWriter:
    def __init__(
        self,
        llm: LLMClient,
        language: str,
        profile: Dict[str, Any],
        plan: Dict[str, Any],
        transcript: CleanTranscript,
    ) -> None:
        self.llm = llm
        self.language = language
        self.profile = profile
        self.plan = plan
        self.transcript = transcript

    def source_for(self, index: int) -> str:
        return segment_source(self.plan["segments"][index], self.transcript, self.profile)

    def write(
        self,
        index: int,
        previous_lines: List[Turn],
        covered_titles: List[str],
        revision_issues: Optional[List[str]] = None,
        previous_draft: Optional[List[Turn]] = None,
        earlier_turns: Optional[List[Turn]] = None,
    ) -> List[Turn]:
        segment = self.plan["segments"][index]
        notes = [
            f"{c['name']}: {c['definition']}" if c.get("definition") else c["name"]
            for c in segment_concepts(segment, self.profile)
        ]
        prompt = write_user_prompt(
            episode=self.plan,
            profile=self.profile,
            segment=segment,
            position=(index + 1, len(self.plan["segments"])),
            covered_titles=covered_titles,
            previous_lines=previous_lines,
            concept_notes=notes,
            source=self.source_for(index),
            target_words=segment["target_words"],
            revision_issues=revision_issues,
            previous_draft=previous_draft,
            used_examples=used_examples(self.plan, index),
            used_phrases=overused_phrases(earlier_turns or []),
        )
        # Hebrew runs ~2-3 tokens per word plus JSON overhead; leave generous headroom
        max_tokens = min(8000, max(1200, segment["target_words"] * 4 + 400))
        raw = self.llm.chat_json(
            "write",
            write_system_prompt(self.language),
            prompt,
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return normalize_turns(raw)
