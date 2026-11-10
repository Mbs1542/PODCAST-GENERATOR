"""The fixed exam-quality evaluation set: the same (up to) five lessons, measured the same way, over time.

    python -m scripts.exam_eval --init           # pick up to 5 analysed lessons for the set (saved on this machine)
    python -m scripts.exam_eval --estimate       # what a run would cost
    python -m scripts.exam_eval --yes            # run it and append to the history
    python -m scripts.exam_eval --show           # the history, newest last

The set and history live in <outputs>/exam_eval/ (config.json, history.jsonl). The release gate
(python -m scripts.gate) shows the latest run and fails when it regressed against the previous comparable run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.exam_eval import (DEFAULT_OPTIONS, MAX_LECTURES, append_history, read_history,  # noqa: E402
                                  regressions, run_eval)
from src.version import __version__  # noqa: E402


def _context():
    from src.web.projects import ProjectStore
    from src.web.settings_store import SettingsStore

    settings, error = SettingsStore(ROOT).load_settings()
    outputs = Path(settings.output_base_dir) if settings else ROOT / "outputs"
    outputs = outputs if outputs.is_absolute() else ROOT / outputs
    return settings, error, ProjectStore(outputs), outputs / "exam_eval"


def _ready(projects) -> List[Any]:
    out = []
    for project in projects.list():
        engine = projects.engine_dir(project)
        if (engine / "01_clean.json").exists() and (engine / "02_profile.json").exists():
            out.append(project)
    return out


def _table(runs: List[Dict[str, Any]]) -> str:
    shown = ("keep_rate", "first_pass", "dropped", "warnings_per_q", "length_cue", "answer_balance", "grounded", "cost_per_question")
    lines = ["date                 version  lessons " + " ".join(f"{m[:14]:>14}" for m in shown)]
    for run in runs:
        avg = run.get("average", {})
        lines.append(f"{run['date'][:19]:20} {run.get('app_version', ''):8} {len(run.get('lessons', [])):7} "
                     + " ".join(f"{avg.get(m, 0):>14}" for m in shown))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--init", action="store_true", help="choose the lessons of the set (up to 5 analysed lessons)")
    parser.add_argument("--projects", default="", help="comma-separated project ids for --init (default: the newest)")
    parser.add_argument("--estimate", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--yes", action="store_true", help="run without asking")
    args = parser.parse_args()

    settings, error, projects, folder = _context()
    config_path, history_path = folder / "config.json", folder / "history.jsonl"

    if args.init:
        ready = _ready(projects)
        chosen = [p.strip() for p in args.projects.split(",") if p.strip()] or [p.id for p in ready][:MAX_LECTURES]
        known = {p.id for p in ready}
        missing = [p for p in chosen if p not in known]
        if missing or not chosen:
            print(f"Not analysed or not found: {', '.join(missing) or '(no analysed lessons)'}")
            return 1
        folder.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"projects": chosen[:MAX_LECTURES], "options": DEFAULT_OPTIONS}, indent=2), encoding="utf-8")
        print(f"Evaluation set: {len(chosen[:MAX_LECTURES])} lesson(s) saved to {config_path}")
        if len(chosen) < MAX_LECTURES:
            print(f"(Up to {MAX_LECTURES} are recommended; analyse more lessons and run --init again to widen the set.)")
        return 0

    history = read_history(history_path)
    if args.show:
        print(_table(history) if history else "No runs yet.")
        return 0

    if not config_path.exists():
        print("No evaluation set yet: run  python -m scripts.exam_eval --init")
        return 1
    config = json.loads(config_path.read_text(encoding="utf-8"))
    from src.web.exams import ExamError, load_lectures

    try:
        lessons = [(pid, load_lectures(projects, [pid])) for pid in config["projects"]]
    except ExamError as exc:
        print(f"A lesson of the set cannot be loaded: {exc}\nChoose the set again: python -m scripts.exam_eval --init")
        return 1
    from src.utils.pricing import estimate_exam

    options = {**DEFAULT_OPTIONS, **config.get("options", {})}
    model = getattr(settings, "gemini_text_model", "") if settings else ""
    low = high = 0.0
    for _, lectures in lessons:
        e = estimate_exam(options["count"], min(options["count"], len(lectures[0].profile.get("concepts", [])) or 1),
                          sum(len(s.get("text", "")) for s in lectures[0].sections), 1, writer_model=model or "gemini-3.8-flash")
        low, high = low + e["low"], high + e["high"]
    print(f"{len(lessons)} lesson(s), {options['count']} questions each, model {model or '?'}: about ${low:.2f}-${high:.2f}")
    if args.estimate:
        return 0
    if settings is None:
        print(f"Settings are not complete: {error}")
        return 1
    if not args.yes and input("Run now? [y/N] ").strip().lower() not in ("y", "yes"):
        return 0

    from src.engine.llm import build_cloud_llm
    from src.utils.costs import CostTracker
    from src.web.credits import google_spend_usd

    def factory():
        costs = CostTracker()
        return build_cloud_llm(settings, costs), lambda: google_spend_usd(costs)

    spent_total = []

    def counted_factory():
        llm, spent = factory()
        spent_total.append(spent)
        return llm, spent

    try:
        run = run_eval(lessons, counted_factory, folder, options, app_version=__version__, model=model)
    finally:  # what the run cost goes to the app's credit ledger
        from src.web.credits import CreditsLedger

        CreditsLedger(folder.parent / "private").record("google", sum(s() for s in spent_total), "exam_eval", "סט הערכת מבחנים")
    worse = regressions(run, history)
    run["regressions"] = worse
    append_history(history_path, run)
    print()
    print(_table(history[-4:] + [run]))
    print()
    if worse:
        print("REGRESSED against the previous comparable run:\n  " + "\n  ".join(worse))
        return 2
    print("No regression against the previous comparable run." if history else "First run: this is the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
