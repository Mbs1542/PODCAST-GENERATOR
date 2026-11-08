"""Evaluation: twenty test lessons, an English critic with anchored scores, and a run report."""

from .rubric import CRITIC_SYSTEM, DIMENSION_KEYS, DIMENSIONS, weighted_overall
from .runner import EvalRunner, render_report, select_scenarios
from .scenarios import SCENARIOS

__all__ = [
    "CRITIC_SYSTEM",
    "DIMENSIONS",
    "DIMENSION_KEYS",
    "EvalRunner",
    "SCENARIOS",
    "render_report",
    "select_scenarios",
    "weighted_overall",
]
