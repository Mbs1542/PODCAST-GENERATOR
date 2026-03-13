from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import List, Optional
import os
import sys

import typer

from src.pipeline import LecturePipeline
from src.metadata import MetadataChatSession
from src.utils import Settings, get_logger, analyze_language


def _ensure_utf8_stdio() -> None:
    """Force UTF-8 console encoding to avoid Windows cp1252 crashes."""
    try:
        if not os.environ.get("PYTHONIOENCODING"):
            os.environ["PYTHONIOENCODING"] = "utf-8"
        for stream_name in ("stdout", "stderr"):
            stream = getattr(sys, stream_name, None)
            if stream and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Best-effort only; fall back silently if the console does not support it.
        pass


_ensure_utf8_stdio()

app = typer.Typer(add_completion=False, help="Generate Azure בפשטות podcast assets.")


@app.command()
def run(
    transcript: Path = typer.Argument(..., exists=True, readable=True, help="Path to lecture transcript (Hebrew text)."),
    metadata: Path = typer.Argument(..., exists=True, readable=True, help="JSON file with topic/date/key_concepts."),
    output_dir: Path = typer.Option(None, "--output-dir", "-o", help="Override output base directory."),
    include_visuals: Optional[bool] = typer.Option(
        None,
        "--include-visuals/--skip-visuals",
        help="Override whether to render Manim + video assets.",
        show_default=False,
    ),
    skip_cache: bool = typer.Option(False, "--skip-cache", help="Ignore cached dialogue generations."),
    force: bool = typer.Option(False, "--force", help="Regenerate dialogue/audio even if artifacts exist."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Log actions without calling external services."),
    voice_profile: Optional[str] = typer.Option(None, "--voice-profile", "-vp", help="Override voice profile name."),
    materials: List[Path] = typer.Option(
        None,
        "--material",
        "-m",
        exists=True,
        readable=True,
        help="Supporting file (PDF/DOCX/PPTX/TXT). Can be provided multiple times.",
    ),
    urls: List[str] = typer.Option(None, "--url", "-u", help="Supporting URL to ingest.", show_default=False),
    export_ppt: bool = typer.Option(False, "--export-ppt/--no-export-ppt", help="Create PowerPoint summary slide deck."),
    tts_provider: Optional[str] = typer.Option(
        None,
        "--tts-provider",
        "-tts",
        help="TTS provider to use: 'azure' or 'elevenlabs'. Defaults to settings.",
    ),
    visual_generator: Optional[str] = typer.Option(
        None,
        "--visual-generator",
        "-vg",
        help="Visual generator to use: 'manim', 'imagen', 'veo', etc.",
    ),
    image_count: Optional[int] = typer.Option(
        None,
        "--image-count",
        help="Number of images to generate.",
    ),
    video_duration: Optional[float] = typer.Option(
        None,
        "--video-duration",
        help="Target video duration in seconds.",
    ),
    generate_visual_metadata: bool = typer.Option(
        False,
        "--generate-visual-metadata",
        help="Generate detailed visual metadata before pipeline execution.",
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Run in preview mode (first 5 sentences only).",
    ),
    ai_enhance: bool = typer.Option(
        False,
        "--ai-enhance/--no-ai-enhance",
        help="Enable AI quality enhancement (experimental, default off).",
    ),
    ai_auto_fix: bool = typer.Option(
        False,
        "--ai-auto-fix/--no-ai-auto-fix",
        help="Allow AI to auto-apply safe fixes (requires --ai-enhance).",
    ),
    target_language: Optional[str] = typer.Option(
        None,
        "--target-language",
        help="Preferred output language for dialogue/TTS/subtitles (he/en/auto). Default is auto-detect.",
    ),
):
    """Entry point for the lecture-to-podcast pipeline."""
    logger = get_logger("CLI")
    settings = Settings.load()
    transcript_text = transcript.read_text(encoding="utf-8")
    lang_info = analyze_language(transcript_text)
    
    # Override settings from CLI args
    if skip_cache:
        settings = replace(settings, cache_dialogues=False)
    if include_visuals is not None:
        settings = replace(settings, enable_visuals=include_visuals)
    if visual_generator:
        settings = replace(settings, visual_generator=visual_generator)
    if tts_provider:
        settings = replace(settings, default_tts_provider=tts_provider)
    if image_count is not None:
        settings = replace(settings, image_count=image_count)
    if video_duration is not None:
        settings = replace(settings, video_duration_seconds=video_duration, auto_video_duration=False)
    if preview:
        settings = replace(settings, preview_mode=True)
    if ai_enhance:
        settings = replace(settings, enable_ai_quality_enhancement=True)
    if ai_auto_fix:
        settings = replace(settings, auto_apply_ai_fixes=True, enable_ai_quality_enhancement=True)

    # Choose target language (prompt if mixed/English and not provided)
    detected_default = lang_info.primary if lang_info.primary in ("he", "en") else "he"
    chosen_language = target_language or ""
    if chosen_language.lower() == "auto":
        chosen_language = detected_default
    if not chosen_language:
        if lang_info.is_mixed or detected_default == "en":
            prompt_text = (
                f"Transcript detected as {lang_info.primary or 'unknown'} (mixed={lang_info.is_mixed}). "
                "Choose target output language [he/en]"
            )
            chosen_language = typer.prompt(prompt_text, default=detected_default).strip().lower()
        else:
            chosen_language = detected_default
    chosen_language = chosen_language.lower()
    settings = replace(settings, target_language=chosen_language)
    logger.info(
        "Transcript language detected: primary=%s mixed=%s (he=%.3f, en=%.3f) -> target_language=%s",
        lang_info.primary,
        lang_info.is_mixed,
        lang_info.hebrew_ratio,
        lang_info.latin_ratio,
        chosen_language,
    )

    logger.info(
        "Starting pipeline with visuals=%s, generator=%s, export_ppt=%s, tts=%s, preview=%s, image_count=%s, video_duration=%s",
        settings.enable_visuals,
        settings.visual_generator,
        export_ppt,
        tts_provider or settings.default_tts_provider,
        settings.preview_mode,
        getattr(settings, "image_count", None),
        getattr(settings, "video_duration_seconds", None) if not getattr(settings, "auto_video_duration", True) else "auto",
    )
    logger.info("CLI command: %s", " ".join(sys.argv))
    
    # Generate visual metadata if requested
    if generate_visual_metadata:
        logger.info("Generating visual metadata...")
        try:
            # Read transcript
            transcript_text = transcript.read_text(encoding="utf-8")
            metadata_dict = json.loads(metadata.read_text(encoding="utf-8"))
            
            # Initialize chat session and import basic metadata
            chat_session = MetadataChatSession(settings)
            chat_session.import_metadata(metadata_dict)
            
            # Generate visual metadata
            visual_meta = chat_session.generate_visual_metadata(
                transcript_text=transcript_text,
                image_count=settings.image_count if hasattr(settings, 'image_count') else 10,
                preferred_model="gemini",
            )
            
            # Save visual metadata next to metadata file
            visual_meta_path = metadata.parent / "visual_metadata.json"
            visual_meta_path.write_text(
                json.dumps(visual_meta, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            logger.info(f"Visual metadata saved to {visual_meta_path}")
            logger.info(f"Generated {len(visual_meta.get('images', []))} image prompts")
            
        except Exception as exc:
            logger.error(f"Visual metadata generation failed: {exc}")
            raise typer.Exit(1)

    pipeline = LecturePipeline(settings=settings, dry_run=dry_run)
    pipeline.run(
        transcript_path=transcript,
        metadata_path=metadata,
        output_dir=output_dir,
        force=force,
        skip_visuals=not settings.enable_visuals,
        voice_profile=voice_profile,
        materials=materials,
        urls=urls,
        export_ppt=export_ppt,
        tts_provider=tts_provider,
        preview=preview or settings.preview_mode,
        command_line=" ".join(sys.argv),
    )
    logger.info("Processing complete.")


if __name__ == "__main__":
    app()

