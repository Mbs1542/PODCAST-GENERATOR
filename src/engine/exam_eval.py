"""A fixed evaluation set for exam quality, measured the same way every time and kept as a history.

The same lessons (up to five), the same options and the same seed are run through the whole exam engine
with fresh caches. Each run appends one line to history.jsonl with per-lesson metrics and their average,
so a change to prompts, rules or model can be compared with the runs before it. The release gate shows the
latest run and fails when it regressed.

Metrics (all from the engine's own records, no extra model calls):
  keep_rate          questions in the final exam / requested
  first_pass         questions that passed the first check / written
  repaired, dropped  counts from the repair stage
  warnings_per_q     warnings left on kept questions (soft issues the checker noted)
  relabelled         questions whose level was corrected by the blind checker (D1)
  length_cue         share of multiple-choice items whose correct option is strictly the longest
                     (a classic cue; about 1/options is chance)
  answer_balance     (most used position - least used) / multiple-choice items (0 = perfectly even)
  grounded           share of questions whose every source points at a real lecture section
  cost_usd, seconds, cost_per_question
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .exam import EXAM_VERSION, ExamBuilder, ExamOptions, Lecture

MAX_LECTURES = 5
DEFAULT_OPTIONS = {"count": 10, "difficulty": "mixed", "mcq_share": 0.8, "language": "he", "num_options": 4,
                   "style": "final", "seed": 7}
METRICS = ("keep_rate", "first_pass", "repaired", "dropped", "warnings_per_q", "relabelled", "length_cue",
           "answer_balance", "grounded", "cost_usd", "seconds", "cost_per_question")
# A run regressed when a metric moved this far in the wrong direction from the previous comparable run
REGRESSION = {"keep_rate": -0.10, "first_pass": -0.15, "grounded": -0.05,
              "length_cue": 0.15, "answer_balance": 0.20, "cost_per_question": 0.5}  # cost: relative (+50%)


def metrics(result: Dict[str, Any], cost_usd: float = 0.0, seconds: float = 0.0) -> Dict[str, float]:
    stats = result.get("stats", {})
    questions = result.get("questions", [])
    mcq = [q for q in questions if q.get("type") == "mcq"]
    final = max(1, len(questions))

    def longest_is_correct(q: Dict[str, Any]) -> bool:
        lengths = sorted(((len(o.get("text") or ""), bool(o.get("correct"))) for o in q.get("options", [])), reverse=True)
        return len(lengths) > 1 and bool(lengths[0][1]) and lengths[0][0] > lengths[1][0]

    positions = list((stats.get("answer_positions") or {}).values())
    return {
        "keep_rate": round(len(questions) / max(1, stats.get("requested", 0)), 3),
        "first_pass": round(stats.get("passed_first_check", 0) / max(1, stats.get("written", 0)), 3),
        "repaired": stats.get("repaired", 0),
        "dropped": stats.get("dropped", 0),
        "warnings_per_q": round(sum(1 for q in questions for f in q.get("flags", []) if f.get("severity") == "warn") / final, 3),
        "relabelled": sum(1 for q in questions if any(f.get("rule") == "D1" for f in q.get("flags", []))),
        "length_cue": round(sum(longest_is_correct(q) for q in mcq) / len(mcq), 3) if mcq else 0.0,
        "answer_balance": round((max(positions) - min(positions)) / len(mcq), 3) if mcq and positions else 0.0,
        "grounded": round(sum(1 for q in questions if q.get("sources") and all(s.get("time") is not None for s in q["sources"]))
                          / final, 3),
        "cost_usd": round(cost_usd, 4),
        "seconds": round(seconds, 1),
        "cost_per_question": round(cost_usd / final, 4),
    }


def average(rows: Sequence[Dict[str, float]]) -> Dict[str, float]:
    return {m: round(sum(r.get(m, 0) for r in rows) / len(rows), 4) for m in METRICS} if rows else {}


def fingerprint(projects: Sequence[str], options: Dict[str, Any]) -> str:
    """Runs are comparable only on the same lessons with the same options."""
    return json.dumps({"projects": list(projects), "options": options}, sort_keys=True, ensure_ascii=False)


def run_eval(lessons: Sequence[Tuple[str, List[Lecture]]], llm_factory: Callable[[], Tuple[Any, Callable[[], float]]],
             work_dir: Path, options: Optional[Dict[str, Any]] = None, log: Callable[[str], None] = print,
             app_version: str = "", model: str = "") -> Dict[str, Any]:
    """Run each lesson through the engine with fresh caches. `llm_factory` returns (client, spent_usd())."""
    options = {**DEFAULT_OPTIONS, **(options or {})}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows = []
    for project_id, lectures in list(lessons)[:MAX_LECTURES]:
        llm, spent = llm_factory()
        started = time.time()
        log(f"eval: {lectures[0].title}")
        builder = ExamBuilder(llm, Path(work_dir) / "runs" / stamp / project_id, log=lambda m: None)
        result = builder.run(lectures, ExamOptions.from_dict(options))
        row = metrics(result, spent(), time.time() - started)
        rows.append({"project": project_id, "title": lectures[0].title, **row})
        log(f"eval:   keep {row['keep_rate']:.0%} · first pass {row['first_pass']:.0%} · ${row['cost_usd']:.3f}")
    return {"date": datetime.now().isoformat(timespec="seconds"), "app_version": app_version, "exam_version": EXAM_VERSION,
            "model": model, "fingerprint": fingerprint([r["project"] for r in rows], options), "options": options,
            "lessons": rows, "average": average(rows)}


def append_history(path: Path, run: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, ensure_ascii=False) + "\n")


def read_history(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    runs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            runs.append(json.loads(line))
        except ValueError:
            continue
    return runs


def regressions(current: Dict[str, Any], history: Sequence[Dict[str, Any]]) -> List[str]:
    """What got worse than the last comparable run (same lessons and options), in words."""
    previous = next((r for r in reversed(history) if r.get("date") != current.get("date")
                     and r.get("fingerprint") == current.get("fingerprint")), None)
    if previous is None:
        return []
    now, before = current.get("average", {}), previous.get("average", {})
    found = []
    for metric, limit in REGRESSION.items():
        if metric not in now or metric not in before:
            continue
        if metric == "cost_per_question":
            if before[metric] > 0 and now[metric] > before[metric] * (1 + limit):
                found.append(f"{metric}: {before[metric]} -> {now[metric]} (+{now[metric] / before[metric] - 1:.0%})")
        elif (limit < 0 and now[metric] - before[metric] < limit) or (limit > 0 and now[metric] - before[metric] > limit):
            found.append(f"{metric}: {before[metric]} -> {now[metric]}")
    return found


__all__ = ["run_eval", "metrics", "average", "append_history", "read_history", "regressions", "DEFAULT_OPTIONS",
           "MAX_LECTURES"]
