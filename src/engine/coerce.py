"""Helpers for coercing loosely-typed model output into predictable values."""

from __future__ import annotations

from typing import Any, List


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [text(item) for item in value if text(item)]


def clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default
