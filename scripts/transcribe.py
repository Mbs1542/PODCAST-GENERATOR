"""Transcribe Hebrew lecture videos to SRT + text.

    python -m scripts.transcribe lecture.mp4                # writes lecture.srt and lecture.txt next to it
    python -m scripts.transcribe lecture.mp4 --script       # then build the podcast script from it
    python -m scripts.transcribe https://youtu.be/...       # download the audio first (yt-dlp)
    python -m scripts.transcribe --download-model           # fetch the model ahead of time
    python -m scripts.transcribe                            # no arguments: pick files in a window
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

import typer

from src.transcribe import (
    DEFAULT_MODEL,
    HebrewTranscriber,
    TranscribeError,
    download_model,
    download_url,
    transcribe_file,
)


def _ensure_utf8_stdio() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_ensure_utf8_stdio()
app = typer.Typer(add_completion=False, help="Hebrew video/audio -> SRT + text (ivrit.ai Whisper).")


def _pick_files() -> List[str]:
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    chosen = filedialog.askopenfilenames(
        title="בחר סרטוני הרצאות לתמלול",
        filetypes=[("Video / audio", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.mp3 *.wav *.m4a *.aac *.flac *.ogg"), ("All", "*.*")],
    )
    root.destroy()
    return list(chosen)


@app.command()
def run(
    inputs: Optional[List[str]] = typer.Argument(None, help="Video/audio files or URLs. None: choose in a window."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder (default: next to each file)."),
    device: str = typer.Option("auto", "--device", help="auto, cuda or cpu."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Model id or local path."),
    language: str = typer.Option("he", "--language", help="Spoken language code (he, en, ...)."),
    script: bool = typer.Option(False, "--script", help="Build the podcast script from each transcript afterwards."),
    download_only: bool = typer.Option(False, "--download-model", help="Only download the model, then exit."),
) -> None:
    if download_only:
        typer.echo(f"Downloading {model} (about 1.6 GB, once)...")
        typer.echo(f"Saved to {download_model(model)}")
        return

    interactive = not inputs
    if interactive:
        inputs = _pick_files()
        if not inputs:
            typer.echo("No files chosen.")
            return

    transcriber = HebrewTranscriber(model=model, device=device, log=typer.echo, language=language)
    failures = 0
    for item in inputs:
        try:
            if item.startswith(("http://", "https://")):
                folder = out or Path(tempfile.mkdtemp(prefix="lecture_"))
                typer.echo(f"Downloading {item} ...")
                media = download_url(item, folder)
            else:
                media = Path(item)
            typer.echo(f"\nTranscribing {media.name} ...")
            srt_path, txt_path = transcribe_file(media, out, transcriber, log=typer.echo)
            typer.echo(f"  -> {srt_path}\n  -> {txt_path}")
            if script:
                subprocess.run([sys.executable, "-m", "scripts.build_script", str(srt_path)], check=False)
        except TranscribeError as exc:
            failures += 1
            typer.echo(f"  !! {exc}", err=True)

    if interactive:
        input("\nPress Enter to close...")
    if failures:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
