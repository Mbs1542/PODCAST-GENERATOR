"""Stage 1: turn a raw transcript (SRT, VTT or plain text) into clean, titled sections.

Cleanup is deliberately conservative. Code removes fillers and stutters; the checker
model only proposes section boundaries and word-level fixes for misheard terms,
which are applied by exact string replacement. The model never rewrites the
lecture, so nothing the lecturer said can be lost or invented here.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..utils.logging import get_logger
from .llm import LLMClient
from .prompts import CLEAN_SYSTEM, clean_user_prompt

LECTURE_WORDS_PER_MINUTE = 120
PARAGRAPH_TARGET_CHARS = 600
PARAGRAPH_MAX_CHARS = 900
CUE_PAUSE_SECONDS = 2.0
WINDOW_MAX_CHARS = 8000
SECTION_FALLBACK_CHARS = 2500
SECTION_MAX_CHARS = 12000
MAX_FIXES_PER_WINDOW = 15

_CUE_RE = re.compile(
    r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+")
_FILLER_RE = re.compile(r"^(?:אמ+|אה+|אהמ+|הממ+|מממ+|um+|uh+|uhm+|erm+|hmm+)$", re.I)
_PUNCT_STRIP = ",.;:!?…\"'()"


@dataclass
class Paragraph:
    text: str
    start: Optional[float] = None
    end: Optional[float] = None


@dataclass
class Section:
    id: str
    title: str
    text: str
    start: Optional[float] = None
    end: Optional[float] = None


@dataclass
class CleanTranscript:
    sections: List[Section]
    language: str
    word_count: int
    duration_seconds: Optional[float] = None
    fixes: List[Dict[str, str]] = field(default_factory=list)
    sectioned_by: str = "fallback"

    @property
    def lecture_minutes(self) -> float:
        if self.duration_seconds:
            return self.duration_seconds / 60.0
        return self.word_count / LECTURE_WORDS_PER_MINUTE

    def section_ids(self) -> List[str]:
        return [s.id for s in self.sections]

    def get(self, section_id: str) -> Optional[Section]:
        return next((s for s in self.sections if s.id == section_id), None)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CleanTranscript":
        payload = dict(data)
        payload["sections"] = [Section(**s) for s in payload.get("sections", [])]
        payload.pop("_key", None)
        return cls(**payload)


# --------------------------------------------------------------------------- parsing


def _to_seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_cues(raw: str) -> List[Paragraph]:
    """Parse SRT/VTT cues into timed lines. Returns [] for plain text."""
    if "-->" not in raw:
        return []
    cues: List[Paragraph] = []
    current: Optional[Paragraph] = None
    for line in raw.splitlines():
        match = _CUE_RE.match(line)
        if match:
            current = Paragraph("", _to_seconds(match.group(1)), _to_seconds(match.group(2)))
            cues.append(current)
            continue
        stripped = _TAG_RE.sub("", line).strip()
        if not stripped:
            current = None
            continue
        if current is None:
            # Cue numbers, the WEBVTT header and NOTE blocks sit outside cues
            continue
        current.text = f"{current.text} {stripped}".strip()

    # Auto captions repeat lines across overlapping cues; fold exact repeats into one
    merged: List[Paragraph] = []
    for cue in cues:
        if not cue.text:
            continue
        if merged and cue.text == merged[-1].text:
            merged[-1].end = cue.end
            continue
        if merged and merged[-1].text and cue.text.startswith(merged[-1].text):
            merged[-1].text = cue.text
            merged[-1].end = cue.end
            continue
        merged.append(cue)
    return merged


def _split_long_block(text: str) -> List[str]:
    """Split a block into ~600-char pieces on sentence ends, or on words for unpunctuated ASR output."""
    if len(text) <= PARAGRAPH_MAX_CHARS:
        return [text]
    sentences = [s for s in _SENTENCE_END_RE.split(text) if s.strip()]
    if len(sentences) <= 1:
        words = text.split()
        sentences = [" ".join(words[i : i + 20]) for i in range(0, len(words), 20)]
    pieces: List[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > PARAGRAPH_TARGET_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def build_paragraphs(raw: str) -> List[Paragraph]:
    cues = parse_cues(raw)
    if cues:
        paragraphs: List[Paragraph] = []
        current: Optional[Paragraph] = None
        for cue in cues:
            pause = (cue.start - current.end) if current and current.end is not None and cue.start is not None else 0
            if current is None or pause > CUE_PAUSE_SECONDS or len(current.text) >= PARAGRAPH_TARGET_CHARS:
                current = Paragraph(cue.text, cue.start, cue.end)
                paragraphs.append(current)
            else:
                current.text = f"{current.text} {cue.text}"
                current.end = cue.end
        return paragraphs

    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]
    paragraphs = []
    for block in blocks:
        block = " ".join(block.split())
        paragraphs.extend(Paragraph(piece) for piece in _split_long_block(block))
    return paragraphs


# --------------------------------------------------------------------------- cleanup


def clean_text(text: str) -> str:
    """Remove filler words and immediate word repeats ("את את") without touching content."""
    kept: List[str] = []
    for token in text.split():
        bare = token.strip(_PUNCT_STRIP)
        if bare and _FILLER_RE.match(bare):
            continue
        if kept and token == kept[-1] and token.strip(_PUNCT_STRIP) == token:
            continue
        kept.append(token)
    cleaned = " ".join(kept)
    cleaned = re.sub(r"\s+([,.;:!?…])", r"\1", cleaned)
    cleaned = re.sub(r"([,.;:!?])\1+", r"\1", cleaned)
    return cleaned.strip(" ,")


def _apply_fixes(paragraphs: List[Paragraph], fixes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Apply proposed fixes that are safe: exact match, short, and not a rewrite."""
    applied: List[Dict[str, str]] = []
    joined = "\n".join(p.text for p in paragraphs)
    for fix in fixes[:MAX_FIXES_PER_WINDOW]:
        wrong = str(fix.get("wrong", "")).strip()
        right = str(fix.get("right", "")).strip()
        if len(wrong) < 2 or not right or wrong == right or wrong not in joined:
            continue
        if len(wrong.split()) > 4 or len(right) > 2 * len(wrong) + 10:
            continue
        for paragraph in paragraphs:
            paragraph.text = paragraph.text.replace(wrong, right)
        joined = joined.replace(wrong, right)
        applied.append({"wrong": wrong, "right": right})
    return applied


def _windows(paragraphs: List[Paragraph]) -> List[List[Paragraph]]:
    windows: List[List[Paragraph]] = []
    current: List[Paragraph] = []
    size = 0
    for paragraph in paragraphs:
        if current and size + len(paragraph.text) > WINDOW_MAX_CHARS:
            windows.append(current)
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph.text)
    if current:
        windows.append(current)
    return windows


def _fallback_starts(paragraphs: List[Paragraph]) -> List[int]:
    starts, size = [0], 0
    for index, paragraph in enumerate(paragraphs):
        if size >= SECTION_FALLBACK_CHARS and index != starts[-1]:
            starts.append(index)
            size = 0
        size += len(paragraph.text)
    return starts


def _sections_from_starts(
    paragraphs: List[Paragraph], starts: List[int], titles: List[str]
) -> List[Section]:
    sections: List[Section] = []
    bounds = starts + [len(paragraphs)]
    for i, (begin, end) in enumerate(zip(bounds, bounds[1:])):
        chunk = paragraphs[begin:end]
        if not chunk:
            continue
        sections.append(
            Section(
                id="",
                title=titles[i],
                text="\n\n".join(p.text for p in chunk),
                start=chunk[0].start,
                end=chunk[-1].end,
            )
        )
    return sections


def _split_oversized(section: Section) -> List[Section]:
    if len(section.text) <= SECTION_MAX_CHARS:
        return [section]
    parts = section.text.split("\n\n")
    out: List[Section] = []
    current: List[str] = []
    for part in parts:
        if current and sum(len(p) for p in current) + len(part) > SECTION_MAX_CHARS // 2:
            out.append(Section("", section.title, "\n\n".join(current), section.start, section.end))
            current = []
        current.append(part)
    if current:
        out.append(Section("", section.title, "\n\n".join(current), section.start, section.end))
    return out


class TranscriptCleaner:
    """Stage 1. Works without a model (fallback sectioning) and gets better with one."""

    def __init__(
        self,
        checker: Optional[LLMClient],
        language: str,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.checker = checker
        self.language = language
        self.log = log or (lambda _msg: None)
        self.logger = get_logger(self.__class__.__name__)

    def run(self, raw: str) -> CleanTranscript:
        paragraphs = build_paragraphs(raw)
        for paragraph in paragraphs:
            paragraph.text = clean_text(paragraph.text)
        paragraphs = [p for p in paragraphs if p.text]
        if not paragraphs:
            raise ValueError("Transcript is empty after cleanup.")

        sections: List[Section] = []
        fixes: List[Dict[str, str]] = []
        sectioned_by = "fallback"
        windows = _windows(paragraphs)
        self.logger.info("Cleaning %d paragraphs in %d windows", len(paragraphs), len(windows))
        for window_index, window in enumerate(windows, start=1):
            result = self._ask_model(window, window_index) if self.checker else None
            if result is None:
                starts = _fallback_starts(window)
                titles = [""] * len(starts)
            else:
                starts, titles, proposed = result
                fixes.extend(_apply_fixes(window, proposed))
                sectioned_by = self.checker.name
            sections.extend(_sections_from_starts(window, starts, titles))

        final: List[Section] = []
        for section in sections:
            final.extend(_split_oversized(section))
        part_word = "Part" if self.language == "en" else "חלק"
        for number, section in enumerate(final, start=1):
            section.id = f"S{number}"
            if not section.title:
                section.title = f"{part_word} {number}"

        ends = [p.end for p in paragraphs if p.end is not None]
        starts_t = [p.start for p in paragraphs if p.start is not None]
        duration = (max(ends) - min(starts_t)) if ends and starts_t else None
        word_count = sum(len(s.text.split()) for s in final)
        self.log(f"Stage 1: {word_count} words in {len(final)} sections ({sectioned_by}), {len(fixes)} fixes")
        return CleanTranscript(
            sections=final,
            language=self.language,
            word_count=word_count,
            duration_seconds=duration,
            fixes=fixes,
            sectioned_by=sectioned_by,
        )

    def _ask_model(self, window: List[Paragraph], window_index: int):
        numbered = "\n\n".join(f"[{i}] {p.text}" for i, p in enumerate(window, start=1))
        try:
            reply = self.checker.chat_json(
                "clean",
                CLEAN_SYSTEM,
                clean_user_prompt(numbered, self.language),
                max_tokens=1500,
                temperature=0.1,
            )
        except Exception as exc:  # a failed window falls back to plain sections; it never stops the run
            self.logger.warning("Window %d: cleanup model failed (%s); using fallback sections", window_index, exc)
            return None

        starts: List[int] = []
        titles: List[str] = []
        for item in reply.get("sections", []) or []:
            try:
                index = int(item.get("start", 0)) - 1
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= index < len(window) and index not in starts:
                starts.append(index)
                titles.append(str(item.get("title", "")).strip()[:80])
        if not starts:
            return None
        order = sorted(range(len(starts)), key=lambda k: starts[k])
        starts = [starts[k] for k in order]
        titles = [titles[k] for k in order]
        if starts[0] != 0:
            starts.insert(0, 0)
            titles.insert(0, "")
        proposed = [f for f in reply.get("fixes", []) or [] if isinstance(f, dict)]
        return starts, titles, proposed
