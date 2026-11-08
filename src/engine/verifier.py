"""Stage 5: check each written segment and say exactly what to fix.

Structure, length, repetition and concept coverage are checked in code (free and
deterministic). Grounding, whether each factual statement is supported by the
source, is checked by the checker model, preferably the local one. Issues are
phrased as instructions because they are fed back to the writer verbatim.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from ..utils.logging import get_logger
from .coerce import clamp_int, text
from .llm import LLMClient
from .prompts import VERIFY_SYSTEM, verify_user_prompt
from .writer import SPEAKERS, Turn

MAX_TURN_WORDS = 85           # a listener loses the thread in longer single turns
MAX_AVERAGE_TURN_WORDS = 34   # above this the segment sounds like two lecturers, not a conversation
MAX_SAME_SPEAKER_RUN = 3
TOO_SHORT_RATIO = 0.6
TOO_LONG_RATIO = 1.8
LONG_WARNING_RATIO = 1.4
VERIFY_SOURCE_MAX_CHARS = 16_000
MIN_REPEAT_CHARS = 25

_NIQQUD_RE = re.compile(r"[֑-ׇ]")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.U)
_MARKDOWN_RE = re.compile(r"\*\*|__|^#{1,6}\s|^\s*[-*•]\s|\[[^\]]+\]\([^)]+\)", re.M)
_HEBREW_PREFIXES = "ובהכלמש"


def normalize_text(value: str) -> str:
    value = _NIQQUD_RE.sub("", value.lower())
    value = _NON_WORD_RE.sub(" ", value)
    return " ".join(value.split())


def _strip_prefixes(token: str) -> str:
    # Hebrew attaches up to two one-letter prefixes (ו, ה, ב, כ, ל, מ, ש): "ובענן" -> "ענן"
    for _ in range(2):
        if len(token) > 3 and token[0] in _HEBREW_PREFIXES:
            token = token[1:]
    return token


def _token_set(normalized: str) -> Set[str]:
    tokens = set(normalized.split())
    return tokens | {_strip_prefixes(t) for t in tokens}


def concept_variants(concept: str) -> List[str]:
    """'PEA (Pulseless Electrical Activity)' -> the whole name, 'PEA', 'Pulseless Electrical Activity'.
    Concepts are often named with a translation or abbreviation in brackets or after a slash; mentioning
    any one form covers the concept."""
    parts = [concept] + [p for p in re.split(r"[()\[\]/]", concept) if p.strip()]
    seen: List[str] = []
    for part in parts:
        part = part.strip(" -–,")
        if part and part not in seen:
            seen.append(part)
    return seen


def concept_mentioned(concept: str, normalized_text: str, tokens: Set[str]) -> bool:
    return any(_variant_mentioned(v, normalized_text, tokens) for v in concept_variants(concept))


def _variant_mentioned(concept: str, normalized_text: str, tokens: Set[str]) -> bool:
    """Loose match: the full name appears, or most of its significant words do."""
    name = normalize_text(concept)
    if not name:
        return True
    if name in normalized_text:
        return True
    significant = [t for t in name.split() if len(t) >= 3]
    if not significant:
        return False
    hits = sum(1 for t in significant if t in tokens or _strip_prefixes(t) in tokens)
    return hits / len(significant) >= 0.6


# Arabic, Cyrillic, Greek letters, CJK and others: a model glitch that a Hebrew voice would mangle
_FOREIGN_SCRIPT_RE = re.compile(r"[Ͱ-ϿЀ-ӿ؀-ۿݐ-ݿ฀-๿぀-ヿ一-鿿가-힯]")
_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")
_SYMBOL_RE = re.compile(r"[%=+×÷<>^*#@&$€₪/\\|{}\[\]]")
_YEAR_RE = re.compile(r"^(1[5-9]\d\d|20\d\d)$")


def unspeakable_parts(value: str) -> List[str]:
    """Digits and symbols that text-to-speech reads badly in Hebrew (gendered numbers, '%', '=').
    Four-digit years and digits inside a name (e.g. 'Web3', 'H2O') are fine."""
    found = [m.group(0) for m in _NUMBER_RE.finditer(value) if not _YEAR_RE.match(m.group(0))]
    found += sorted(set(_SYMBOL_RE.findall(value)))
    return found[:4]


def word_count(turns: List[Turn]) -> int:
    return sum(len(t["text"].split()) for t in turns)


@dataclass
class SegmentReport:
    segment_id: str
    title: str
    words: int
    target_words: int
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_concepts: List[str] = field(default_factory=list)
    unsupported: List[Dict[str, Any]] = field(default_factory=list)
    grounding_checked: bool = False
    revisions: int = 0

    @property
    def passed(self) -> bool:
        return not self.issues

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["passed"] = self.passed
        return data


class ScriptVerifier:
    def __init__(self, checker: Optional[LLMClient], check_grounding: bool = True) -> None:
        self.checker = checker
        self.check_grounding = check_grounding and checker is not None
        self.logger = get_logger(self.__class__.__name__)

    def check(
        self,
        segment: Dict[str, Any],
        turns: List[Turn],
        source: str,
        earlier_turns: List[Turn],
    ) -> SegmentReport:
        report = SegmentReport(
            segment_id=segment.get("id", ""),
            title=segment.get("title", ""),
            words=word_count(turns),
            target_words=int(segment.get("target_words", 0)),
        )
        self._check_structure(turns, earlier_turns, report)
        if not turns:
            return report

        spoken = normalize_text(" ".join(t["text"] for t in turns))
        tokens = _token_set(spoken)
        report.missing_concepts = [c for c in segment.get("concepts", []) if not concept_mentioned(c, spoken, tokens)]
        if report.missing_concepts:
            report.issues.append(
                "These concepts were not covered; explain each of them: " + ", ".join(report.missing_concepts)
            )

        if self.check_grounding:
            self._check_grounding(turns, source, report)
        return report

    def _check_structure(self, turns: List[Turn], earlier_turns: List[Turn], report: SegmentReport) -> None:
        if not turns:
            report.issues.append("The segment is empty.")
            return

        strangers = sorted({t["speaker"] for t in turns} - set(SPEAKERS))
        if strangers:
            report.issues.append(f"Only Roee and Noa may speak; remove lines by: {', '.join(strangers)}.")
        if len(turns) >= 4 and len({t["speaker"] for t in turns}) == 1:
            report.issues.append("Only one host speaks; make it a conversation between Roee and Noa.")

        if report.target_words:
            ratio = report.words / report.target_words
            if ratio < TOO_SHORT_RATIO:
                report.issues.append(
                    f"Too short: {report.words} words, the target is about {report.target_words}. "
                    "Go deeper using the source material; do not add facts that are not in it."
                )
            elif ratio > TOO_LONG_RATIO:
                report.issues.append(f"Too long: {report.words} words, the target is about {report.target_words}.")
            elif ratio > LONG_WARNING_RATIO:
                report.warnings.append(f"Long: {report.words} words vs a target of {report.target_words}.")

        if len(turns) >= 4 and report.words / len(turns) > MAX_AVERAGE_TURN_WORDS:
            report.issues.append(
                f"Turns average {report.words // len(turns)} words, which sounds like two lecturers. Break the "
                "explanations into shorter turns with reactions, checks and contributions from the other host "
                f"(most turns 5-30 words); keep the same content and about the same total length."
            )

        run = 1
        for number, (previous, turn) in enumerate(zip(turns, turns[1:]), start=2):
            run = run + 1 if turn["speaker"] == previous["speaker"] else 1
            if run == MAX_SAME_SPEAKER_RUN + 1:
                report.issues.append(f"{turn['speaker']} speaks {run} times in a row around line {number}; alternate.")

        seen = {normalize_text(t["text"]) for t in earlier_turns if len(t["text"]) >= MIN_REPEAT_CHARS}
        for number, turn in enumerate(turns, start=1):
            words = len(turn["text"].split())
            if words > MAX_TURN_WORDS:
                report.issues.append(
                    f"Line {number} is a {words}-word monologue; split it and let the other host react."
                )
            if _MARKDOWN_RE.search(turn["text"]):
                report.issues.append(f"Line {number} contains markdown or list formatting; write plain speech.")
            foreign = _FOREIGN_SCRIPT_RE.findall(turn["text"])
            if foreign:
                report.issues.append(
                    f"Line {number} contains characters from another alphabet ({''.join(foreign[:6])}); "
                    "rewrite the word in Hebrew or English."
                )
            unspeakable = unspeakable_parts(turn["text"])
            if unspeakable:
                report.issues.append(
                    f"Line {number} has text a voice would read badly ({', '.join(unspeakable)}); "
                    "write numbers and symbols out as spoken words."
                )
            normalized = normalize_text(turn["text"])
            if len(turn["text"]) >= MIN_REPEAT_CHARS and normalized in seen:
                report.issues.append(f"Line {number} repeats an earlier line word for word.")
            seen.add(normalized)

    def _check_grounding(self, turns: List[Turn], source: str, report: SegmentReport) -> None:
        numbered = "\n".join(f"[{i}] {t['speaker']}: {t['text']}" for i, t in enumerate(turns, start=1))
        if len(source) > VERIFY_SOURCE_MAX_CHARS:
            source = source[:VERIFY_SOURCE_MAX_CHARS] + "\n[...]"
        try:
            reply = self.checker.chat_json(
                "verify", VERIFY_SYSTEM, verify_user_prompt(numbered, source), max_tokens=1200, temperature=0.0
            )
        except Exception as exc:  # a failed check is reported, it never blocks the script
            self.logger.warning("Grounding check failed for %s: %s", report.segment_id, exc)
            report.warnings.append(f"Grounding check could not run: {exc}")
            return

        report.grounding_checked = True
        for item in reply.get("unsupported") or []:
            if not isinstance(item, dict) or not text(item.get("claim")):
                continue
            severity = text(item.get("severity")).lower()
            report.unsupported.append(
                {
                    "turn": clamp_int(item.get("turn"), 1, len(turns), 1),
                    "claim": text(item.get("claim")),
                    "severity": severity if severity in ("high", "low") else "low",
                }
            )
        high = [u for u in report.unsupported if u["severity"] == "high"]
        if high:
            claims = "; ".join(f"line {u['turn']}: {u['claim']}" for u in high)
            report.issues.append(f"Not supported by the source; remove or correct: {claims}")
        low = len(report.unsupported) - len(high)
        if low:
            report.warnings.append(f"{low} statement(s) go slightly beyond the source.")
