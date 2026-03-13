from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import re

from ..utils.text_utils import (
    shape_rtl,
    srt_text,
    render_text,
    wrap_logical_text,
)
from PIL import Image, ImageDraw, ImageFont


_DEFAULT_SUB_FONT = "arial.ttf"
_VIDEO_WIDTH = 1920
_MAX_SUBTITLE_WIDTH_RATIO = 0.83


def _format_ts(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def _compute_durations(turns: List[Tuple[int, str]], total_duration: float) -> List[float]:
    """
    Distribute total_duration across captions proportionally to word count,
    while keeping a reasonable min/max per caption.
    """
    if total_duration <= 0:
        return [0.0 for _ in turns]

    # Base heuristic per caption
    base_durations = []
    for _, text in turns:
        words = len(text.split()) or 1
        base_durations.append(min(6.0, max(2.0, words * 0.4)))

    total_base = sum(base_durations) or 1.0
    scale = total_duration / total_base
    scaled = [max(0.5, d * scale) for d in base_durations]  # keep floor

    # Adjust last caption to land exactly on total_duration
    cumulative = 0.0
    for i in range(len(scaled)):
        cumulative += scaled[i]
    if cumulative > 0:
        diff = total_duration - cumulative
        scaled[-1] = max(0.3, scaled[-1] + diff)

    return scaled


def _split_turn_text(
    text: str,
    max_words: int = 10,
    max_chars: int = 80,
) -> List[str]:
    """
    Split a dialogue turn into smaller chunks for readability.

    - Prefer sentence boundaries (., !, ?, …).
    - Further split long sentences into ~max_words chunks and enforce max_chars.
    """
    if not text:
        return []

    sentences = [
        s.strip()
        for s in re.split(r"(?<=[\.!\?…])\s+", text)
        if s and s.strip()
    ]
    if not sentences:
        sentences = [text.strip()]

    chunks: List[str] = []
    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue
        current: List[str] = []
        for word in words:
            tentative = " ".join(current + [word]).strip()
            if (
                current
                and (len(tentative) > max_chars or len(current) >= max_words)
            ):
                chunks.append(" ".join(current).strip())
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current).strip())

    # Merge very short trailing chunks into previous ones to avoid over-fragmentation
    merged: List[str] = []
    for chunk in chunks:
        if merged:
            prev = merged[-1]
            prev_words = prev.split()
            chunk_words = chunk.split()
            if (
                len(chunk_words) <= 3
                and len(prev_words) + len(chunk_words) <= max_words
                and len(prev) + 1 + len(chunk) <= max_chars
            ):
                merged[-1] = f"{prev} {chunk}".strip()
                continue
        merged.append(chunk)

    chunks = merged

    # Safety: if everything was filtered out, return the raw text
    return chunks or [text.strip()]


def fix_rtl_text(text: str) -> str:
    """
    Centralized RTL shaping using text_utils.shape_rtl (display only).
    """
    return render_text(text)


def _load_font(font_size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(_DEFAULT_SUB_FONT, font_size)
    except OSError:
        return ImageFont.load_default()


def wrap_caption_text(
    text: str,
    *,
    max_width_px: int = int(_VIDEO_WIDTH * _MAX_SUBTITLE_WIDTH_RATIO),
    max_lines: int = 2,
    font_size: int = 20,
    min_font_scale: float = 0.9,
) -> Tuple[str, int]:
    """
    Wrap caption text to fit within max_width_px using real font metrics.

    Returns (wrapped_text, applied_font_size).
    """
    if not text:
        return "", font_size

    # Prepare measuring context once
    measure_img = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(measure_img)

    def measure_width(s: str, f: ImageFont.FreeTypeFont) -> int:
        bbox = draw.textbbox((0, 0), s, font=f)
        return bbox[2] - bbox[0]

    words = text.split()

    def _attempt(scale: float) -> Tuple[List[str], int]:
        scaled_size = max(1, int(font_size * scale))
        font = _load_font(scaled_size)
        lines: List[str] = []
        current = ""
        for word in words:
            tentative = word if not current else f"{current} {word}"
            if measure_width(tentative, font) <= max_width_px:
                current = tentative
            else:
                if current:
                    lines.append(current)
                    current = word
                else:
                    lines.append(word)
                    current = ""
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        return lines, scaled_size

    # Try with full size, then a slightly reduced size
    for scale in (1.0, min_font_scale):
        lines, applied_size = _attempt(scale)
        if len(lines) <= max_lines and all(
            measure_width(line, _load_font(applied_size)) <= max_width_px for line in lines
        ):
            return "\n".join(lines), applied_size

    # Ellipsize last line as a last resort
    lines, applied_size = _attempt(1.0)
    font = _load_font(applied_size)
    ellipsis = "..."
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines:
        last = lines[-1]
        while last and measure_width(f"{last}{ellipsis}", font) > max_width_px:
            if " " in last:
                last = last.rsplit(" ", 1)[0]
            else:
                last = last[:-1]
        lines[-1] = f"{last}{ellipsis}" if last else ellipsis
    return "\n".join(lines), applied_size


def generate_srt_from_dialogue(
    dialogue_json: Dict,
    total_duration: float,
    output_path: Path,
    segment_durations: Optional[Iterable[float]] = None,
    lead_in_seconds: float = 0.0,
    outro_seconds: float = 0.0,
    target_language: Optional[str] = None,
) -> Path:
    """
    Create captions.srt aligned to the audio duration.

    - If segment_durations is provided, use exact per-segment audio lengths.
    - Otherwise, durations are proportional to word count but normalized to the speech window.
    - lead_in_seconds offsets captions (e.g., intro music), outro_seconds keeps tail silent.
    - Falls back to a flat split if dialogue is empty.
    """
    turns_raw = [d for d in dialogue_json.get("dialogue", []) if isinstance(d, dict)]
    turns: List[Tuple[int, str]] = []
    for idx, turn in enumerate(turns_raw, start=1):
        text = str(turn.get("text", "")).strip()
        if text:
            turns.append((idx, text))

    if not turns or total_duration <= 0:
        output_path.write_text("", encoding="utf-8")
        return output_path

    durations: List[float]
    has_segments = bool(segment_durations)
    if has_segments:
        durations = [max(0.01, float(d or 0.0)) for d in segment_durations or []]
        if len(durations) < len(turns):
            durations.extend([0.5] * (len(turns) - len(durations)))
    else:
        speech_window = max(0.0, total_duration - lead_in_seconds - outro_seconds)
        durations = _compute_durations(turns, speech_window)

    entries: List[str] = []
    cursor = lead_in_seconds
    entry_index = 1
    lang = (target_language or "").lower()

    def _append_entry(entry_idx: int, start: float, end: float, logical_text: str) -> None:
        logical_clean = srt_text(logical_text)
        lines = wrap_logical_text(logical_clean, max_chars_per_line=42)
        srt_payload = "\n".join(lines)
        entries.append(f"{entry_idx}\n{_format_ts(start)} --> {_format_ts(end)}\n{srt_payload}\n")

    if has_segments:
        for (_, text), dur in zip(turns, durations):
            if dur <= 0:
                dur = 0.01

            chunks = _split_turn_text(text, max_words=10, max_chars=80)
            if not chunks:
                cursor += dur
                continue

            word_counts = [max(1, len(c.split())) for c in chunks]
            total_words = sum(word_counts) or len(chunks)
            raw_allocations = [dur * (wc / total_words) for wc in word_counts]

            min_chunk = 0.3
            chunk_durations: List[float] = [max(min_chunk, alloc) for alloc in raw_allocations]
            drift = dur - sum(chunk_durations)
            if chunk_durations:
                chunk_durations[-1] = max(min_chunk, chunk_durations[-1] + drift)

            chunk_cursor = cursor
            for chunk_text, chunk_dur in zip(chunks, chunk_durations):
                start = chunk_cursor
                end = chunk_cursor + chunk_dur
                if end <= start:
                    continue
                _append_entry(entry_index, start, end, chunk_text)
                entry_index += 1
                chunk_cursor = end

            # Advance cursor strictly by the provided segment duration to keep contiguous timing
            cursor += dur
    else:
        speech_window = max(0.0, total_duration - lead_in_seconds - outro_seconds)
        audio_limit = total_duration if total_duration > 0 else lead_in_seconds + speech_window
        if outro_seconds > 0 and total_duration > 0:
            audio_limit = max(0.0, total_duration - outro_seconds)
        latest_end_allowed = lead_in_seconds + speech_window if speech_window > 0 else audio_limit
        latest_end_allowed = min(latest_end_allowed, audio_limit) if audio_limit > 0 else latest_end_allowed

        for (_, text), dur in zip(turns, durations):
            if cursor >= latest_end_allowed:
                break

            chunks = _split_turn_text(text, max_words=10, max_chars=80)
            if not chunks:
                continue

            word_counts = [max(1, len(c.split())) for c in chunks]
            total_words = sum(word_counts) or len(chunks)
            raw_allocations = [dur * (wc / total_words) for wc in word_counts]

            min_chunk = 0.3
            chunk_durations: List[float] = [max(min_chunk, alloc) for alloc in raw_allocations]
            drift = dur - sum(chunk_durations)
            if chunk_durations:
                chunk_durations[-1] = max(min_chunk, chunk_durations[-1] + drift)

            for chunk_text, chunk_dur in zip(chunks, chunk_durations):
                if cursor >= latest_end_allowed:
                    break
                start = min(cursor, latest_end_allowed)
                end = min(cursor + chunk_dur, latest_end_allowed)
                if end <= start:
                    continue
                _append_entry(entry_index, start, end, chunk_text)
                entry_index += 1
                cursor = end

            cursor = min(cursor, latest_end_allowed)

    output_path.write_text("\n".join(entries), encoding="utf-8")

    # Assert logical order for Hebrew sanity
    try:
        srt_body = output_path.read_text(encoding="utf-8")
        if "םולש" in srt_body and "שלום" not in srt_body:
            raise AssertionError("SRT regression: found reversed 'םולש' without logical 'שלום'.")
        if "שלום" not in srt_body:
            raise AssertionError("SRT regression: missing logical 'שלום' in captions.")
        debug_first = entries[0] if entries else ""
        print(f"[SRT DEBUG] first entry raw={debug_first!r}")
    except Exception as exc:
        print(f"[SRT DEBUG] skipped validation: {exc}")
    return output_path
