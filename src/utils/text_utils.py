from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import arabic_reshaper
from bidi.algorithm import get_display

# BiDi isolation markers (avoid LTR fragments flipping surrounding RTL)
LRI = "\u2066"  # left-to-right isolate
PDI = "\u2069"  # pop directional isolate
RLM = "\u200f"  # right-to-left mark

try:  # Optional dependency used elsewhere; keep soft import
    from langdetect import detect, DetectorFactory

    DetectorFactory.seed = 0  # deterministic langdetect
except Exception:  # pragma: no cover - runtime guard
    detect = None

_HEBREW_CHAR_RE = re.compile(r"[\u0590-\u05FF]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class LanguageDetection:
    """Lightweight language distribution summary."""

    primary: str
    secondary: str
    hebrew_ratio: float
    latin_ratio: float
    other_ratio: float
    is_mixed: bool
    detector: str


def _isolate_ltr_segments(text: str) -> str:
    """
    Wrap embedded LTR spans (English/numbers/model names) with LRI/PDI to
    prevent reordering issues inside RTL sentences.
    """
    pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\+\/&_.]*")
    return pattern.sub(lambda m: f"{LRI}{m.group(0)}{PDI}", text)


def _anchor_punctuation(text: str) -> str:
    """
    Keep trailing punctuation on the RTL side.
    """
    if text and text[-1] in {"!", "?", "."}:
        return f"{text}{RLM}"
    return text


def shape_rtl(text: Optional[str], add_isolates: bool = True) -> str:
    """
    Apply Arabic reshaping + BiDi display for Hebrew/RTL text.

    - Uses arabic_reshaper + bidi.get_display for reliable glyph shaping.
    - Optionally wraps embedded LTR spans with LRI/PDI to avoid flip.
    - Anchors trailing punctuation to the RTL side.
    """
    if not text:
        return ""

    processed = text
    if add_isolates:
        processed = _isolate_ltr_segments(processed)
    processed = _anchor_punctuation(processed)

    # Final shaping into visual order
    try:
        reshaped = arabic_reshaper.reshape(processed)
        return get_display(reshaped)
    except Exception:
        # Defensive: fall back to original string if shaping fails
        return processed


def srt_text(logical: Optional[str]) -> str:
    """
    Logical text for SRT output (no shaping/reversal).

    - Trims and collapses internal whitespace to single spaces.
    - Never applies bidi.get_display or arabic_reshaper.
    """
    if not logical:
        return ""
    return " ".join(str(logical).strip().split())


def render_text(logical: Optional[str]) -> str:
    """
    Visual rendering of logical text (shaping only for display).

    - Keeps the logical order intact for upstream consumers.
    - Applies RTL shaping + isolates for rendering.
    """
    return shape_rtl(logical or "", add_isolates=True)


def wrap_logical_text(text: str, max_chars_per_line: int = 42) -> list[str]:
    """
    Wrap logical text on word boundaries before any RTL shaping.

    - Preserves whole words; only breaks mid-word if a single word exceeds the limit.
    - Returns a list of lines (without shaping).
    """
    if not text:
        return []

    words = text.split()
    lines: list[str] = []
    current: list[str] = []

    for word in words:
        tentative = " ".join(current + [word]).strip()
        if current and len(tentative) > max_chars_per_line:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)

    if current:
        lines.append(" ".join(current))

    # Fallback: ensure at least the original text is returned
    return lines or [text.strip()]


def analyze_language(text: str) -> LanguageDetection:
    """
    Score transcript language composition (Hebrew/Latin/Other) and pick primary.

    - Uses character ratios for robustness on mixed content.
    - Falls back to langdetect for a secondary signal when available.
    """
    raw = text or ""
    letters = [c for c in raw if c.isalpha()]
    total_letters = len(letters)
    total = total_letters or max(len(raw), 1)

    hebrew_count = sum(1 for c in raw if _HEBREW_CHAR_RE.match(c))
    latin_count = sum(1 for c in raw if _LATIN_CHAR_RE.match(c))
    hebrew_ratio = hebrew_count / total
    latin_ratio = latin_count / total
    other_ratio = max(0.0, 1.0 - hebrew_ratio - latin_ratio)

    is_mixed = hebrew_ratio > 0.15 and latin_ratio > 0.15

    primary = "unknown"
    secondary = "unknown"
    if hebrew_ratio >= latin_ratio and hebrew_ratio >= 0.2:
        primary = "he"
        secondary = "en" if latin_ratio >= 0.1 else "other"
    elif latin_ratio > hebrew_ratio and latin_ratio >= 0.2:
        primary = "en"
        secondary = "he" if hebrew_ratio >= 0.1 else "other"

    detector_used = "ratio"
    if primary == "unknown" and detect:
        try:
            detected = detect(raw)
            detector_used = "langdetect"
            if detected.startswith("he"):
                primary = "he"
            elif detected.startswith("en"):
                primary = "en"
            else:
                primary = detected.split("-")[0]
        except Exception:
            pass

    return LanguageDetection(
        primary=primary,
        secondary=secondary,
        hebrew_ratio=round(hebrew_ratio, 4),
        latin_ratio=round(latin_ratio, 4),
        other_ratio=round(other_ratio, 4),
        is_mixed=is_mixed,
        detector=detector_used,
    )

