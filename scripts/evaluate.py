"""Judge the pipeline on twenty different lessons with an English critic.

    python -m scripts.evaluate --estimate          # what will run and roughly what it costs
    python -m scripts.evaluate                     # all 20 scenarios
    python -m scripts.evaluate -s 01 -s 07         # only some scenarios (id prefixes)
    python -m scripts.evaluate --judge gemini-4-argon   # a stronger critic than the writer (recommended)

Real transcripts placed at data/eval/lessons/<scenario id>.txt are used instead of generated ones.
The report lands in outputs/eval/runs/<timestamp>/report.md and is compared with the previous run.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer

from src.engine.llm import build_cloud_llm, build_engine_llms
from src.evaluation import EvalRunner, select_scenarios
from src.evaluation.runner import LECTURE_WORDS_PER_MINUTE
from src.utils import Settings


def _ensure_utf8_stdio() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_ensure_utf8_stdio()
# The SDKs log every HTTP request; with several scenarios at once that drowns the progress lines
for _name in ("httpx", "google_genai", "google_genai.models"):
    logging.getLogger(_name).setLevel(logging.WARNING)
app =typer.Typer(add_completion=False, help="Twenty-lesson evaluation with an English critic.")
ROOT = Path(__file__).resolve().parents[1]


@app.command()
def run(
    scenario: Optional[List[str]] = typer.Option(None, "--scenario", "-s", help="Scenario id prefix; repeat for several."),
    limit: int = typer.Option(0, "--limit", help="Run only the first N selected scenarios."),
    judge: str = typer.Option("", "--judge", help="Model (or Azure deployment) for the critic; default: the writing model."),
    samples: int = typer.Option(1, "--samples", help="Critic samples per scenario, averaged (2-3 reduce noise)."),
    estimate: bool = typer.Option(False, "--estimate", help="Only print the plan and a rough token estimate."),
    workers: int = typer.Option(3, "--workers", "-w", help="Scenarios run at the same time (mind the API rate limit)."),
    everything: bool = typer.Option(False, "--all", help="All twenty scenarios instead of the ten core ones."),
    sources_only: bool = typer.Option(False, "--sources-only", help="Only generate the missing source lessons."),
) -> None:
    scenarios = select_scenarios(scenario, limit, everything)
    lessons_dir = ROOT / "data" / "eval" / "lessons"
    if not scenarios:
        raise typer.BadParameter("No scenario matches.")

    real = sum(1 for s in scenarios if (lessons_dir / f"{s['id']}.txt").exists())
    lecture_words = sum(s["lecture_minutes"] * LECTURE_WORDS_PER_MINUTE for s in scenarios)
    # Rough: generating missing sources + ~3x the source through the engine + the critic reading source and script
    tokens = int(lecture_words * 2.2 * (3 + 1.5) + 25_000 * len(scenarios))
    typer.echo(f"{len(scenarios)} scenario(s); {real} with a real transcript, {len(scenarios) - real} generated once and reused.")
    for s in scenarios:
        marker = "real" if (lessons_dir / f"{s['id']}.txt").exists() else "generated"
        typer.echo(f"  {s['id']:<22} {s['format']:<18} {s['lecture_minutes']:>3} min  [{marker}]  {s['tests']}")
    typer.echo(f"Rough size of the run: ~{tokens / 1e6:.1f}M tokens (critic {samples}x per scenario).")
    if estimate:
        return

    def log(message: str) -> None:
        typer.echo(f"{datetime.now():%H:%M:%S} {message}")
        sys.stdout.flush()

    settings = Settings.load()
    runner = EvalRunner(
        settings,
        engine_llms_factory=lambda s, costs: build_engine_llms(s, costs),
        judge=build_cloud_llm(settings, model=judge),
        author=build_cloud_llm(settings),
        out_dir=settings.output_base_dir / "eval" if settings.output_base_dir.is_absolute() else ROOT / settings.output_base_dir / "eval",
        lessons_dir=lessons_dir,
        judge_samples=samples,
        log=log,
    )
    if sources_only:
        failures = runner.prepare_sources(scenarios, workers=workers)
        typer.echo("Sources ready." if not failures else "Failed: " + "; ".join(failures))
        raise typer.Exit(1 if failures else 0)
    summary = runner.run(scenarios, workers=workers)
    typer.echo("")
    typer.echo(f"Overall: {summary['overall']:.2f}/10" + (f" ({summary['overall_delta']:+.2f} vs previous run)" if summary.get("overall_delta") is not None else ""))
    weakest = sorted(summary["averages"].items(), key=lambda kv: kv[1])[:3]
    typer.echo("Weakest: " + ", ".join(f"{k} {v:.1f}" for k, v in weakest))
    typer.echo(f"Report: {summary['report_path']}")


if __name__ == "__main__":
    app()
