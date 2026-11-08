"""Build a podcast script from a transcript with script engine v2 (no audio, no video).

Examples:
    python -m scripts.build_script data/sample_transcript.txt
    python -m scripts.build_script lecture.srt --plan-only        # stop after the plan, review 03_plan.json
    python -m scripts.build_script lecture.srt                    # continue from the (edited) plan
    python -m scripts.build_script lecture.srt --format exam_prep --minutes 12
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

from src.engine import ScriptEngine
from src.engine.prompts import FORMATS
from src.transcribe import is_media, transcribe_file
from src.utils import CostTracker, Settings


def _ensure_utf8_stdio() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_ensure_utf8_stdio()
app = typer.Typer(add_completion=False, help="Transcript -> verified podcast script (stages 1-5).")


@app.command()
def run(
    transcript: Path = typer.Argument(..., exists=True, readable=True, help="Transcript (.txt, .srt, .vtt) or a lecture video/audio file."),
    metadata: Optional[Path] = typer.Option(None, "--metadata", "-m", exists=True, help="Optional metadata JSON (topic, key_concepts) used as hints."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder. Default: outputs/scripts/<transcript name>."),
    podcast_format: Optional[str] = typer.Option(None, "--format", "-f", help=f"auto or one of: {', '.join(FORMATS)}."),
    minutes: Optional[float] = typer.Option(None, "--minutes", help="Target length in minutes (default: ~30% of the lecture)."),
    plan_only: bool = typer.Option(False, "--plan-only", help="Stop after the episode plan so it can be reviewed or edited."),
    force: bool = typer.Option(False, "--force", help="Recompute every stage instead of reusing saved results."),
    no_local: bool = typer.Option(False, "--no-local", help="Do not use the local Ollama model even if it is running."),
) -> None:
    settings = Settings.load()
    if podcast_format:
        if podcast_format != "auto" and podcast_format not in FORMATS:
            raise typer.BadParameter(f"Unknown format '{podcast_format}'. Use auto or one of: {', '.join(FORMATS)}")
        settings.podcast_format = podcast_format
    if minutes:
        settings.podcast_target_minutes = minutes
    if no_local:
        settings.local_llm_mode = "off"

    work_dir = out or settings.output_base_dir / "scripts" / transcript.stem
    if is_media(transcript):
        typer.echo(f"Transcribing {transcript.name} first...")
        transcript, _ = transcribe_file(transcript, work_dir, log=typer.echo)
    meta = json.loads(metadata.read_text(encoding="utf-8")) if metadata else {}
    costs = CostTracker()
    engine = ScriptEngine(settings, costs, log=lambda message: typer.echo(f"  {message}"))
    result = engine.run(
        transcript.read_text(encoding="utf-8"),
        meta,
        work_dir / "engine",
        force=force,
        stop_after="plan" if plan_only else None,
    )

    if plan_only:
        typer.echo(f"\nPlan: {work_dir / 'engine' / '03_plan.json'}")
        typer.echo("Edit it if you like, then run again without --plan-only to write the script.")
        return

    dialogue_path = work_dir / "dialogue.json"
    dialogue_path.write_text(json.dumps(result.dialogue, ensure_ascii=False, indent=2), encoding="utf-8")
    report = result.report
    typer.echo("")
    typer.echo(f"Script:   {dialogue_path}")
    typer.echo(f"Report:   {work_dir / 'engine' / '05_report.json'}")
    typer.echo(f"Format:   {report['format']}")
    typer.echo(f"Length:   ~{report['estimated_minutes']} min (target {report['target_minutes']:g}), {report['total_words']} words")
    typer.echo(f"Coverage: {report['core_concept_coverage']:.0%} of core concepts")
    if report["core_concepts_missing"]:
        typer.echo(f"Missing:  {', '.join(report['core_concepts_missing'])}")
    typer.echo(f"Grounding: {report['grounding_checked_segments']} segments checked by {report['checker']}, "
               f"{report['unsupported_high']} unsupported claim(s) left")
    typer.echo(f"Open issues in: {', '.join(report['segments_with_open_issues']) or 'none'}")
    typer.echo(f"LLM cost: ${costs.openai_cost:.3f}")


if __name__ == "__main__":
    app()
