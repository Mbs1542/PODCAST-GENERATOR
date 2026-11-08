"""Stage 4b: one editorial pass over the whole episode.

Segments are written one at a time, so the finished episode tends to repeat the same
reactions and openers, and the seams between segments can restart or re-greet. An
editor that sees the whole script fixes that with small, targeted edits.

The editor may only reword or drop lines, never add new ones, and every edit is
checked in code before it is applied: same speaker, no new numbers, no big growth,
only short lines may be dropped. Edits that fail a check are skipped, so the
worst case is the unpolished script.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .coerce import text
from .llm import LLMClient
from .prompts import POLISH_SYSTEM, polish_user_prompt
from .writer import Turn, normalize_turns

MAX_EDIT_SHARE = 0.25         # at most a quarter of the lines change
MAX_DELETE_WORDS = 14         # only short lines (reactions, filler) may be dropped
MAX_GROWTH_WORDS = 12         # a replacement may grow a little, not become a new explanation
_DIGITS_RE = re.compile(r"\d+")


def _numbers(value: str) -> set:
    return set(_DIGITS_RE.findall(value))


def check_edit(original: Turn, edit: Dict[str, Any]) -> Tuple[bool, str]:
    """Is this edit safe to apply? Returns (ok, reason when not)."""
    action = text(edit.get("action")).lower()
    old_words = len(original["text"].split())
    if action == "delete":
        if old_words > MAX_DELETE_WORDS:
            return False, "only short lines may be dropped"
        return True, ""
    if action != "replace":
        return False, f"unknown action {action!r}"
    new = " ".join(text(edit.get("text")).split())
    if not new:
        return False, "empty replacement"
    if len(new.split()) > old_words + MAX_GROWTH_WORDS:
        return False, "replacement grows too much"
    if not _numbers(new) <= _numbers(original["text"]):
        return False, "replacement introduces a number"
    return True, ""


def apply_edits(segments: List[Dict[str, Any]], edits: List[Dict[str, Any]], log: Callable[[str], None]) -> Tuple[List[Dict[str, Any]], int]:
    """Apply checked edits to the segments. Line numbers are 1-based across the whole episode."""
    index: List[Tuple[int, int]] = [(s, t) for s, seg in enumerate(segments) for t in range(len(seg["turns"]))]
    budget = max(3, int(len(index) * MAX_EDIT_SHARE))
    new_segments = [{**seg, "turns": [dict(t) for t in seg["turns"]]} for seg in segments]
    deleted = set()
    applied = 0
    seen = set()
    for edit in edits:
        if applied >= budget:
            log(f"Polish: edit budget of {budget} lines reached")
            break
        try:
            line = int(edit.get("line"))
        except (TypeError, ValueError):
            continue
        if not 1 <= line <= len(index) or line in seen:
            continue
        s, t = index[line - 1]
        original = segments[s]["turns"][t]
        ok, reason = check_edit(original, edit)
        if not ok:
            log(f"Polish: skipped line {line} ({reason})")
            continue
        seen.add(line)
        if text(edit.get("action")).lower() == "delete":
            # A segment never loses its last lines entirely
            if sum(1 for tt in range(len(segments[s]["turns"])) if (s, tt) not in deleted) <= 2:
                continue
            deleted.add((s, t))
        else:
            target = new_segments[s]["turns"][t]
            target["text"] = " ".join(text(edit.get("text")).split())
            delivery = normalize_turns({"turns": [{"speaker": original["speaker"], "text": "x",
                                                   "delivery": edit.get("delivery", original.get("delivery", ""))}]})
            if delivery and delivery[0].get("delivery"):
                target["delivery"] = delivery[0]["delivery"]
            elif "delivery" in edit:
                target.pop("delivery", None)
        applied += 1
    for s, seg in enumerate(new_segments):
        seg["turns"] = [turn for t, turn in enumerate(seg["turns"]) if (s, t) not in deleted]
    return new_segments, applied


class EpisodePolisher:
    def __init__(self, llm: LLMClient, language: str, log: Optional[Callable[[str], None]] = None) -> None:
        self.llm = llm
        self.language = language
        self.log = log or (lambda _m: None)

    def run(self, segments: List[Dict[str, Any]], plan: Dict[str, Any]) -> Dict[str, Any]:
        lines: List[str] = []
        number = 0
        for seg in segments:
            lines.append(f"\n## {seg['title']}")
            for turn in seg["turns"]:
                number += 1
                delivery = f" ({turn['delivery']})" if turn.get("delivery") else ""
                lines.append(f"[{number}] {turn['speaker']}{delivery}: {turn['text']}")
        try:
            reply = self.llm.chat_json(
                "polish", POLISH_SYSTEM, polish_user_prompt("\n".join(lines), plan, self.language),
                max_tokens=min(12000, 600 + number * 60), temperature=0.3,
            )
        except Exception as exc:  # polishing is an improvement, never a reason to lose the script
            self.log(f"Polish skipped: {exc}")
            return {"segments": segments, "edits": 0, "notes": []}
        edits = [e for e in reply.get("edits") or [] if isinstance(e, dict)]
        polished, applied = apply_edits(segments, edits, self.log)
        notes = [text(n) for n in reply.get("notes") or [] if text(n)][:5]
        self.log(f"Stage 4b: polished the episode ({applied} of {number} lines edited)")
        return {"segments": polished, "edits": applied, "notes": notes}
