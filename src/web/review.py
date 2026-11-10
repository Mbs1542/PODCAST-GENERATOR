"""The spaced-repetition deck: every practised exam question becomes a card scheduled by FSRS-5.

  <outputs>/exams/review.json   {"cards": {"<exam id>:<question id>": {...card, exam, question, added}},
                                  "log": [{card, rating, at, due}]}

A practice attempt adds its questions with a first rating taken from the answer (wrong = again, right = good;
an open question by the student's own 0-10 score). Later reviews come from the review screen.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..engine.fsrs import AGAIN, EASY, GOOD, HARD, Card, Scheduler

MAX_LOG = 5000
# Settings the user can change (Settings > Spaced repetition), with their allowed ranges
DEFAULTS = {"retention": 0.9, "new_per_day": 20, "max_days": 365, "relearn_minutes": 10}
LIMITS = {"retention": (0.8, 0.95), "new_per_day": (0, 200), "max_days": (7, 3650), "relearn_minutes": (1, 120)}
NEW_PER_DAY = DEFAULTS["new_per_day"]


def rating_from_score(score: Optional[float]) -> Optional[int]:
    """A 0-1 score from an attempt as an FSRS rating; None when the question was skipped."""
    if score is None:
        return None
    if score < 0.5:
        return AGAIN
    if score < 0.7:
        return HARD
    if score < 0.95:
        return GOOD
    return EASY


class ReviewDeck:
    def __init__(self, exams_root: Path, scheduler: Optional[Scheduler] = None) -> None:
        self.path = Path(exams_root) / "review.json"
        self.settings_path = Path(exams_root) / "review_settings.json"
        self._fixed = scheduler
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ settings

    def settings(self) -> Dict[str, Any]:
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        return clean_settings({**DEFAULTS, **(saved if isinstance(saved, dict) else {})})

    def save_settings(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only known settings, each clamped to its range; unknown or invalid values keep the current value."""
        current = self.settings()
        merged = clean_settings({**current, **{k: v for k, v in (raw or {}).items() if k in DEFAULTS}}, fallback=current)
        with self._lock:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.settings_path.with_suffix(".tmp")
            temp.write_text(json.dumps(merged, indent=1), encoding="utf-8")
            temp.replace(self.settings_path)
        return merged

    @property
    def scheduler(self) -> Scheduler:
        if self._fixed is not None:
            return self._fixed
        s = self.settings()
        return Scheduler(retention=s["retention"], max_days=s["max_days"], relearn_minutes=s["relearn_minutes"])

    # ------------------------------------------------------------------ storage

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("cards"), dict):
                data.setdefault("log", [])
                return data
        except (OSError, ValueError):
            pass
        return {"cards": {}, "log": []}

    def _write(self, data: Dict[str, Any]) -> None:
        data["log"] = data["log"][-MAX_LOG:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(self.path)

    # ------------------------------------------------------------------ changes

    def add_exam(self, exam_id: str, result: Dict[str, Any], scores: Optional[Dict[str, Optional[float]]] = None,
                 now: Optional[datetime] = None) -> int:
        """Add the exam's questions; a question with a score gets its first review now. Returns how many were added."""
        now = now or datetime.now()
        scores = scores or {}
        added = 0
        with self._lock:
            data = self._read()
            for question in result.get("questions", []):
                card_id = f"{exam_id}:{question['id']}"
                rating = rating_from_score(scores.get(question["id"]))
                if card_id in data["cards"]:
                    if rating is not None:  # practised again from the exam page: counts as a review
                        self._apply(data, card_id, rating, now, source="practice")
                    continue
                data["cards"][card_id] = {**Card().to_dict(), "exam": exam_id, "question": question["id"],
                                          "added": now.isoformat(timespec="seconds")}
                added += 1
                if rating is not None:
                    self._apply(data, card_id, rating, now, source="practice")
            self._write(data)
        return added

    def _apply(self, data: Dict[str, Any], card_id: str, rating: int, now: datetime, source: str = "review") -> Dict[str, Any]:
        entry = data["cards"][card_id]
        first = entry.get("state") == "new"
        card = self.scheduler.review(Card.from_dict(entry), rating, now)
        entry.update(card.to_dict())
        data["log"].append({"card": card_id, "rating": rating, "at": now.isoformat(timespec="seconds"), "due": card.due,
                            "source": source, "first": first})
        return entry

    def review(self, card_id: str, rating: int, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now()
        with self._lock:
            data = self._read()
            if card_id not in data["cards"]:
                raise KeyError(card_id)
            entry = dict(self._apply(data, card_id, int(rating), now))
            self._write(data)
        return {"id": card_id, **entry}

    def remove_exam(self, exam_id: str) -> int:
        with self._lock:
            data = self._read()
            gone = [k for k, v in data["cards"].items() if v.get("exam") == exam_id]
            for key in gone:
                data["cards"].pop(key)
            if gone:
                self._write(data)
        return len(gone)

    def remove_cards(self, card_ids: List[str]) -> None:
        with self._lock:
            data = self._read()
            for key in card_ids:
                data["cards"].pop(key, None)
            self._write(data)

    # ------------------------------------------------------------------ queries

    def due(self, now: Optional[datetime] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Cards to review now: learned cards that are due (most overdue first), then a few new ones."""
        now = now or datetime.now()
        data = self._read()
        stamp = now.isoformat(timespec="seconds")
        # new cards first seen on the review screen today count against the daily limit (practice does not)
        new_today = len({e["card"] for e in data["log"] if e["at"][:10] == stamp[:10] and e.get("first") and e.get("source") == "review"})
        seen = [{"id": k, **v} for k, v in data["cards"].items() if v.get("state") != "new" and v.get("due", "") <= stamp]
        seen.sort(key=lambda c: c["due"])
        fresh = [{"id": k, **v} for k, v in data["cards"].items() if v.get("state") == "new"]
        fresh.sort(key=lambda c: c.get("added", ""))
        return (seen + fresh[:max(0, self.settings()["new_per_day"] - new_today)])[:limit]

    def preview(self, card_id: str, now: Optional[datetime] = None) -> Dict[int, str]:
        now = now or datetime.now()
        entry = self._read()["cards"].get(card_id)
        if entry is None:
            raise KeyError(card_id)
        return self.scheduler.preview(Card.from_dict(entry), now)

    def stats(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now()
        data = self._read()
        cards = data["cards"]
        stamp = now.isoformat(timespec="seconds")
        end_of_day = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat(timespec="seconds")
        learned = [Card.from_dict(v) for v in cards.values() if v.get("state") != "new"]
        recall = [r for r in (self.scheduler.retrievability(c, now) for c in learned) if r is not None]
        upcoming = sorted(v["due"] for v in cards.values() if v.get("state") != "new" and v.get("due", "") > stamp)
        reviewed_today = [e for e in data["log"] if e["at"][:10] == stamp[:10]]
        by_exam: Dict[str, int] = {}
        for v in cards.values():
            by_exam[v.get("exam", "")] = by_exam.get(v.get("exam", ""), 0) + 1
        return {
            "total": len(cards),
            "new": sum(1 for v in cards.values() if v.get("state") == "new"),
            "due_now": len(self.due(now, limit=10_000)),
            "due_today": sum(1 for v in cards.values() if v.get("state") != "new" and v.get("due", "") < end_of_day),
            "learned": sum(1 for c in learned if c.state == "review"),
            "average_recall": round(sum(recall) / len(recall), 3) if recall else None,
            "next_due": upcoming[0] if upcoming else None,
            "reviewed_today": len(reviewed_today),
            "correct_today": sum(1 for e in reviewed_today if e["rating"] >= HARD),
            "by_exam": by_exam,
        }

    def card(self, card_id: str) -> Optional[Dict[str, Any]]:
        entry = self._read()["cards"].get(card_id)
        return {"id": card_id, **entry} if entry else None


def clean_settings(raw: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = fallback or DEFAULTS
    out = {}
    for key, (low, high) in LIMITS.items():
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            value = float(fallback[key])
        if value != value:  # NaN
            value = float(fallback[key])
        value = max(low, min(high, value))
        out[key] = round(value, 2) if key == "retention" else int(round(value))
    return out


__all__ = ["ReviewDeck", "clean_settings", "DEFAULTS", "LIMITS", "rating_from_score", "AGAIN", "HARD", "GOOD", "EASY"]
