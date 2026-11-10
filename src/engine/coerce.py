"""Helpers for coercing loosely-typed model output into predictable values."""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def as_mapping(value: Any) -> Optional[Dict[str, Any]]:
    """An object the model returned, or one an older version saved as its Python text ("{'question': ...}")."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{") and value.strip().endswith("}"):
        try:
            parsed = ast.literal_eval(value.strip())  # literals only: never runs code
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def item_text(value: Any, sep: str = " — ") -> str:
    """One list item as readable text: an object's values joined (e.g. a mistake and its correction)."""
    mapping = as_mapping(value)
    if mapping is not None:
        return sep.join(text(v) for v in mapping.values() if text(v) and not isinstance(v, (dict, list)))
    return text(value)


def str_list(value: Any) -> List[str]:
    """A list of strings; objects inside it become text, never their Python representation."""
    if not isinstance(value, list):
        return []
    return [item_text(item) for item in value if item_text(item)]


def clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default
