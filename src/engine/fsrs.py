"""FSRS-5 spaced repetition: when to show each exam question again.

This is the Free Spaced Repetition Scheduler, version 5 (open-spaced-repetition, MIT), with its published
default parameters. Each card has a stability S (days until recall drops to 90%) and a difficulty D (1-10).
A review with a rating from 1 to 4 updates both; the next review is set for the day recall is expected to
fall to the target retention (90% by default).

Implemented here (about a hundred lines) rather than added as a dependency, so the installer stays the same
on every machine. Deterministic: no interval fuzz, so schedules can be tested exactly.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Sequence

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
RATINGS = (AGAIN, HARD, GOOD, EASY)

# FSRS-5 default parameters (w0..w18)
DEFAULT_W = (0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192, 1.01925,
             1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621)
DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1  # = 19/81, so that recall is exactly 90% after S days


@dataclass
class Card:
    stability: float = 0.0
    difficulty: float = 0.0
    due: str = ""                  # ISO time; "" = new, due now
    last_review: str = ""
    reps: int = 0
    lapses: int = 0
    state: str = "new"             # new | learning | review | relearning

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Card":
        fields = cls.__dataclass_fields__
        return cls(**{k: raw[k] for k in fields if k in raw})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class Scheduler:
    def __init__(self, w: Sequence[float] = DEFAULT_W, retention: float = 0.9, max_days: int = 365,
                 relearn_minutes: int = 10) -> None:
        if len(w) != 19:
            raise ValueError("FSRS-5 needs 19 parameters")
        self.w = tuple(float(x) for x in w)
        self.retention = _clamp(retention, 0.7, 0.97)
        self.max_days = max_days
        self.relearn = timedelta(minutes=relearn_minutes)

    # ------------------------------------------------------------------ the model

    def retrievability(self, card: Card, now: datetime) -> Optional[float]:
        """The chance of recalling the card now, or None for a card never reviewed."""
        if card.state == "new" or not card.last_review or card.stability <= 0:
            return None
        days = max(0.0, (now - datetime.fromisoformat(card.last_review)).total_seconds() / 86400)
        return (1 + FACTOR * days / card.stability) ** DECAY

    def _init_difficulty(self, rating: int) -> float:
        return self.w[4] - math.exp(self.w[5] * (rating - 1)) + 1

    def _next_difficulty(self, d: float, rating: int) -> float:
        delta = -self.w[6] * (rating - 3)
        damped = d + delta * (10 - d) / 9  # linear damping: changes shrink near the top of the scale
        reverted = self.w[7] * self._init_difficulty(EASY) + (1 - self.w[7]) * damped  # mean reversion
        return _clamp(reverted, 1.0, 10.0)

    def _recall_stability(self, d: float, s: float, r: float, rating: int) -> float:
        hard = self.w[15] if rating == HARD else 1.0
        easy = self.w[16] if rating == EASY else 1.0
        return s * (math.exp(self.w[8]) * (11 - d) * s ** -self.w[9] * (math.exp(self.w[10] * (1 - r)) - 1) * hard * easy + 1)

    def _forget_stability(self, d: float, s: float, r: float) -> float:
        new = self.w[11] * d ** -self.w[12] * ((s + 1) ** self.w[13] - 1) * math.exp(self.w[14] * (1 - r))
        return min(new, s)

    def _short_term_stability(self, s: float, rating: int) -> float:
        return s * math.exp(self.w[17] * (rating - 3 + self.w[18]))

    def interval_days(self, stability: float) -> int:
        days = stability / FACTOR * (self.retention ** (1 / DECAY) - 1)
        return int(_clamp(round(days), 1, self.max_days))

    # ------------------------------------------------------------------ a review

    def review(self, card: Card, rating: int, now: datetime) -> Card:
        if rating not in RATINGS:
            raise ValueError("rating must be 1 (again), 2 (hard), 3 (good) or 4 (easy)")
        new = Card(**card.to_dict())
        if card.state == "new" or card.stability <= 0:
            new.stability = self.w[rating - 1]
            new.difficulty = _clamp(self._init_difficulty(rating), 1.0, 10.0)
        else:
            days = (now - datetime.fromisoformat(card.last_review)).total_seconds() / 86400 if card.last_review else 0
            if days < 1:  # a second look on the same day
                new.stability = self._short_term_stability(card.stability, rating)
            else:
                r = self.retrievability(card, now) or 0.0
                new.stability = (self._forget_stability(card.difficulty, card.stability, r) if rating == AGAIN
                                 else self._recall_stability(card.difficulty, card.stability, r, rating))
            new.difficulty = self._next_difficulty(card.difficulty, rating)
        new.stability = max(0.01, new.stability)
        new.reps = card.reps + 1
        new.last_review = now.isoformat(timespec="seconds")
        if rating == AGAIN:
            new.lapses = card.lapses + (1 if card.state == "review" else 0)
            new.state = "relearning" if card.state in ("review", "relearning") else "learning"
            new.due = (now + self.relearn).isoformat(timespec="seconds")
        else:
            new.state = "review"
            new.due = (now + timedelta(days=self.interval_days(new.stability))).isoformat(timespec="seconds")
        return new

    def preview(self, card: Card, now: datetime) -> Dict[int, str]:
        """When the card would come back after each rating (for the rating buttons)."""
        out = {}
        for rating in RATINGS:
            due = datetime.fromisoformat(self.review(card, rating, now).due)
            out[rating] = due.isoformat(timespec="seconds")
        return out
