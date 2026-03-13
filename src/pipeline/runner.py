from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..audio import PodcastStitcher, SpeechSynthesizer, create_tts_synthesizer
from ..dialogue import DialogueGenerator
from ..metadata import MetadataIngestor, MetadataChatSession
from ..outputs import SlideDeckExporter, StoryExporter
from ..utils import (
    CostTracker,
    HistoryManager,
    RunPaths,
    Settings,
    VoiceProfileManager,
    analyze_language,
    get_logger,
    TimingContext,
    QualityChecker,
)
from ..utils.ai_quality_enhancer import AIQualityEnhancer, enhance_with_ai
from ..utils.guardian import ProductionGuardian
from ..utils.visual_metadata_builder import build_visual_metadata_locally, generate_universal_visual_metadata
from ..visuals import ManimSceneGenerator, VideoComposer, GoogleAIVisualGenerator
from moviepy.editor import AudioFileClip
from pydub import AudioSegment

try:
    from ..audio.elevenlabs_tts import ElevenLabsQuotaExceededError
except Exception:  # pragma: no cover - ElevenLabs optional in some environments
    class ElevenLabsQuotaExceededError(RuntimeError):
        """Fallback definition when ElevenLabs is not installed."""
        pass


@dataclass
class LecturePipeline:
    """
    High-level orchestration of the full lecture-to-podcast workflow.
    
    Coordinates multiple components to transform lecture transcripts into
    polished podcast episodes with dialogue, audio, and video output.
    
    Pipeline stages:
        1. Metadata ingestion (optional)
        2. Dialogue generation
        3. Speech synthesis (TTS)
        4. Audio stitching
        5. Video composition (optional)
        6. PPTX export (optional)
        7. Story export
    
    All stages are logged with timing information for performance analysis.
    """

    settings: Settings
    dry_run: bool = False
    _stage_times: Dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info("[LecturePipeline.__post_init__] Initializing pipeline components")
        
        init_start = time.time()
        
        self.cost_tracker = CostTracker()
        self.dialogue = DialogueGenerator(self.settings, self.cost_tracker)
        self.voice_manager = VoiceProfileManager(
            self.settings.voice_profile_path, self.settings.default_voice_profile
        )
        self.metadata_ingestor = MetadataIngestor(self.settings)
        self.stitcher = PodcastStitcher(self.settings, self.cost_tracker)
        self.manim = ManimSceneGenerator(self.settings)
        self.google_ai = GoogleAIVisualGenerator(self.settings)
        self.video = VideoComposer(self.settings)
        self.history = HistoryManager(self.settings.output_base_dir)
        self.deck_exporter = SlideDeckExporter()
        self.story_exporter = StoryExporter()
        self.quality_checker = QualityChecker(self.settings)
        
        init_elapsed = time.time() - init_start
        self.logger.debug("[LecturePipeline.__post_init__] Components initialized in %.3fs", init_elapsed)

    def run(
        self,
        transcript_path: Path,
        metadata_path: Path,
        output_dir: Optional[Path] = None,
        force: bool = False,
        skip_visuals: bool = False,
        voice_profile: Optional[str] = None,
        materials: Optional[List[Path]] = None,
        urls: Optional[List[str]] = None,
        export_ppt: bool = False,
        tts_provider: Optional[str] = None,
        preview: bool = False,
        command_line: Optional[str] = None,
    ) -> RunPaths:
        """
        Execute the complete podcast generation pipeline.
        
        Args:
            transcript_path: Path to transcript file
            metadata_path: Path to metadata JSON file
            output_dir: Optional custom output directory
            force: Force regeneration of all files
            skip_visuals: Skip video generation
            voice_profile: Voice profile name
            materials: Additional material files
            urls: Additional URLs for enrichment
            export_ppt: Export PPTX summary
            tts_provider: TTS provider ('azure' or 'elevenlabs')
            preview: Preview mode flag (truncate dialogue to save credits)
            command_line: Full CLI invocation for logging
            
        Returns:
            RunPaths object with paths to all generated files
        """
        pipeline_start = time.time()
        timing = TimingContext(self.logger, "Pipeline execution")
        effective_preview = preview or getattr(self.settings, "preview_mode", False)
        run_type = "PREVIEW" if effective_preview else "FULL"
        segment_durations: List[float] = []
        lead_in_seconds = 0.0
        outro_seconds = 0.0
        # Ensure settings reflect the requested preview flag for downstream consumers
        try:
            self.settings.preview_mode = effective_preview
        except Exception:
            pass
        if effective_preview:
            try:
                # Enable automatic video duration so preview stays aligned with audio
                self.settings.auto_video_duration = True
                if hasattr(self.settings, "video_duration_seconds"):
                    try:
                        delattr(self.settings, "video_duration_seconds")
                    except Exception:
                        pass
                self.logger.warning("⚠️ PREVIEW MODE: Using auto video duration (no fixed length).")
                try:
                    self.settings.image_count = 3
                    self.logger.warning("⚠️ PREVIEW MODE: Limiting image_count to 3 for preview.")
                except Exception:
                    pass
            except Exception as exc:
                self.logger.warning("⚠️ PREVIEW MODE: Failed to enable auto video duration: %s", exc)
        
        self.logger.info("[LecturePipeline.run] ========== STARTING PIPELINE ==========")
        self.logger.info("[LecturePipeline.run] transcript=%s, metadata=%s", 
                        transcript_path.name, metadata_path.name)
        self.logger.info("[LecturePipeline.run] Options: force=%s, skip_visuals=%s, export_ppt=%s, tts=%s",
                        force, skip_visuals, export_ppt, tts_provider)
        
        # Load input files
        try:
            transcript_text = transcript_path.read_text(encoding="utf-8")
            self.logger.debug("[LecturePipeline.run] Loaded transcript: %d chars", len(transcript_text))
        except Exception as e:
            self.logger.error("[LecturePipeline.run] Failed to read transcript: %s", e)
            raise
            
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.logger.debug("[LecturePipeline.run] Loaded metadata: topic=%s, date=%s", 
                             metadata.get("topic"), metadata.get("date"))
        except Exception as e:
            self.logger.error("[LecturePipeline.run] Failed to read metadata: %s", e)
            raise
        
        lang_info = analyze_language(transcript_text)
        lang_primary = lang_info.primary or "unknown"
        lang_secondary = lang_info.secondary or ""
        self.logger.info(
            "[LecturePipeline.run] Transcript language detected: primary=%s, mixed=%s (he=%.3f, en=%.3f, detector=%s)",
            lang_primary,
            lang_info.is_mixed,
            lang_info.hebrew_ratio,
            lang_info.latin_ratio,
            lang_info.detector,
        )
        try:
            if not getattr(self.settings, "target_language", ""):
                setattr(self.settings, "target_language", lang_primary)
        except Exception:
            pass
        
        materials = materials or []
        urls = urls or []
        run_base = output_dir or self.settings.output_base_dir

        # Load visual metadata if exists (check multiple locations)
        visual_metadata: Optional[Dict] = None
        visual_metadata_source: Optional[Path] = None
        visual_meta_candidates = [
            metadata_path.parent / "visual_metadata.json",
            run_base / "visual_metadata.json",
        ]
        for candidate in visual_meta_candidates:
            if candidate.exists():
                try:
                    visual_metadata = json.loads(candidate.read_text(encoding="utf-8"))
                    visual_metadata_source = candidate
                    self.logger.info(
                        "[LecturePipeline.run] Loaded visual metadata (%d images) from %s",
                        len(visual_metadata.get("images", [])),
                        candidate,
                    )
                    break
                except Exception as exc:
                    self.logger.warning(
                        "[LecturePipeline.run] Failed to load visual metadata from %s: %s",
                        candidate,
                        exc,
                    )

        visual_metadata_log: Optional[str] = None
        visual_metadata_logged = False
        
        if self._metadata_needs_seed(metadata) and not materials and not urls:
            self.logger.info("[LecturePipeline.run] Metadata needs seeding, auto-generating...")
            metadata = self._seed_metadata_from_transcript(metadata, transcript_text)
            timing.checkpoint("metadata_seeding")
            
        run_paths = RunPaths(run_base, metadata.get("date", "unknown"), metadata.get("topic", "azure"))
        run_paths.transcript_language = lang_primary
        run_paths.transcript_language_secondary = lang_secondary
        run_paths.transcript_is_mixed = bool(lang_info.is_mixed)
        run_paths.transcript_hebrew_ratio = lang_info.hebrew_ratio
        run_paths.transcript_latin_ratio = lang_info.latin_ratio
        run_paths.transcript_language_detector = lang_info.detector

        try:
            run_paths.log(
                f"Transcript language: primary={lang_primary}, mixed={lang_info.is_mixed}, "
                f"he_ratio={lang_info.hebrew_ratio:.3f}, en_ratio={lang_info.latin_ratio:.3f}, detector={lang_info.detector}"
            )
        except Exception:
            pass
        if effective_preview:
            # Use a unique filename to avoid clashes with media players locking the previous output
            run_paths.final_video_path = run_paths.run_dir / f"preview_{int(time.time())}.mp4"
            try:
                run_paths.log("⚠️ PREVIEW MODE: image_count capped at 3; auto_video_duration enabled.")
            except Exception:
                pass

        if visual_metadata is None:
            fallback_visual_path = run_paths.run_dir / "visual_metadata.json"
            if fallback_visual_path.exists():
                try:
                    visual_metadata = json.loads(fallback_visual_path.read_text(encoding="utf-8"))
                    visual_metadata_source = fallback_visual_path
                    self.logger.info(
                        "[LecturePipeline.run] Loaded visual metadata from existing run directory: %s",
                        fallback_visual_path,
                    )
                except Exception as exc:
                    self.logger.warning(
                        "[LecturePipeline.run] Failed to read visual metadata from run dir: %s",
                        exc,
                    )

        if visual_metadata:
            visual_metadata_log = (
                f"[Visual Metadata] Loaded {len(visual_metadata.get('images', []))} image prompts "
                f"from {visual_metadata_source.name if visual_metadata_source else 'memory'}"
            )
            try:
                target_visual_path = run_paths.run_dir / "visual_metadata.json"
                target_visual_path.write_text(
                    json.dumps(visual_metadata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.logger.debug(
                    "[LecturePipeline.run] Synced visual metadata to %s", target_visual_path
                )
            except Exception as exc:
                self.logger.warning(
                    "[LecturePipeline.run] Failed to persist visual metadata in run dir: %s",
                    exc,
                )
        
        self.logger.info("[LecturePipeline.run] Output directory: %s", run_paths.run_dir)
        run_paths.log(f"Run type: {run_type}")
        if command_line:
            run_paths.log(f"CLI command: {command_line}")
            self.logger.info("[LecturePipeline.run] CLI command: %s", command_line)
        
        # Log comprehensive settings summary for verification
        self._log_settings_summary(run_paths, tts_provider, voice_profile, skip_visuals, metadata)
        
        # Track state for history recording
        dialogue_file: Optional[Path] = None
        story_path: Optional[Path] = None
        pipeline_failed = False
        failure_message = ""

        run_paths.log("Pipeline execution started.")
        if self.dry_run:
            self.logger.info("[LecturePipeline.run] Dry-run enabled – skipping heavy steps.")
            run_paths.log("Dry-run mode; no artifacts generated.")
            return run_paths

        try:
            # Stage 1: Metadata ingestion (optional)
            if materials or urls:
                stage_start = time.time()
                run_paths.log(f"Starting metadata ingestion for {len(materials)} files and {len(urls)} URLs.")
                self.logger.info("[LecturePipeline.run] STAGE: Metadata ingestion (%d files, %d URLs)",
                               len(materials), len(urls))
                metadata = self.metadata_ingestor.enrich(metadata, materials, urls, run_paths)
                self._stage_times["metadata_ingestion"] = time.time() - stage_start
                timing.checkpoint("metadata_ingestion")

            # Stage 2: Dialogue generation
            stage_start = time.time()
            run_paths.log("Starting dialogue generation.")
            self.logger.info("[LecturePipeline.run] STAGE: Dialogue generation")
            dialogue_file = self.dialogue.generate(transcript_text, metadata, run_paths, force=force)
            self._stage_times["dialogue_generation"] = time.time() - stage_start
            run_paths.log("Dialogue generation completed.")
            timing.checkpoint("dialogue_generation")
            
            # Preview Mode: truncate dialogue to save credits
            if effective_preview:
                try:
                    dialogue_data = json.loads(dialogue_file.read_text(encoding="utf-8"))
                    original_len = len(dialogue_data.get("dialogue", []))
                    # Smart preview: keep 3 turns to balance length and context
                    dialogue_data["dialogue"] = dialogue_data.get("dialogue", [])[:3]
                    dialogue_file.write_text(json.dumps(dialogue_data, ensure_ascii=False, indent=2), encoding="utf-8")
                    truncated_len = len(dialogue_data["dialogue"])
                    run_paths.log(f"⚠️ PREVIEW MODE: Truncated dialogue from {original_len} to {truncated_len} entries.")
                    self.logger.warning(
                        "[LecturePipeline.run] Preview mode active: truncated dialogue from %d to %d entries",
                        original_len,
                        truncated_len,
                    )
                except Exception as exc:
                    self.logger.warning("[LecturePipeline.run] Preview mode truncation failed: %s", exc)
                    run_paths.log(f"⚠️ PREVIEW MODE: Dialogue truncation failed: {exc}")

            # Pre-flight Quality Checks (before TTS)
            stage_start = time.time()
            effective_tts = tts_provider or self.settings.default_tts_provider
            if effective_tts == "elevenlabs":
                run_paths.log("Running pre-flight quality checks...")
                self.logger.info("[LecturePipeline.run] Running pre-flight quality checks")
                
                # Estimate characters from dialogue
                estimated_chars = self._estimate_dialogue_characters(dialogue_file)
                preflight_report = self.quality_checker.run_preflight_checks(
                    estimated_characters=estimated_chars,
                    skip_quota_check=self.dry_run,
                    run_type=run_type,
                )
                
                # Log preflight results
                for check in preflight_report.preflight_checks:
                    status_icon = {"PASS": "✓", "FAIL": "✗", "WARNING": "⚠", "SKIP": "○"}.get(check.status, "?")
                    run_paths.log(f"  {status_icon} {check.name}: {check.message}")
                    self.logger.info("[Preflight] %s %s: %s", status_icon, check.name, check.message)
                
                # Block on FAIL status
                if preflight_report.overall_status == "FAIL":
                    failed_checks = [c for c in preflight_report.preflight_checks if c.status == "FAIL"]
                    fail_messages = [f"{c.name}: {c.message}" for c in failed_checks]
                    error_msg = "Pre-flight checks failed: " + "; ".join(fail_messages)
                    run_paths.log(f"ERROR: {error_msg}")
                    
                    # Save the quality report even on failure
                    preflight_report.save(run_paths.run_dir)
                    raise RuntimeError(error_msg)
                
                self._stage_times["preflight_checks"] = time.time() - stage_start
                timing.checkpoint("preflight_checks")
            
            # Stage 3: Speech synthesis (TTS)
            stage_start = time.time()
            run_paths.log("Starting speech synthesis.")
            self.logger.info("[LecturePipeline.run] STAGE: Speech synthesis (provider=%s)",
                           effective_tts)
            tts = create_tts_synthesizer(
                settings=self.settings,
                cost_tracker=self.cost_tracker,
                voice_profile=voice_profile,
                voice_manager=self.voice_manager,
                provider=tts_provider,
            )
            run_paths.log(f"Using TTS provider: {effective_tts}")

            try:
                segment_results = tts.synthesize(dialogue_file, run_paths, force=force)
            except ElevenLabsQuotaExceededError as exc:
                if effective_tts != "elevenlabs":
                    raise
                self.logger.warning("[LecturePipeline.run] ElevenLabs quota exceeded: %s", exc)
                run_paths.log("ElevenLabs quota exceeded – attempting Azure TTS fallback.")
                if not (self.settings.speech_key and self.settings.speech_region):
                    raise RuntimeError(
                        "ElevenLabs quota exceeded and Azure Speech is not configured. "
                        "Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to enable fallback."
                    ) from exc
                fallback_tts = create_tts_synthesizer(
                    settings=self.settings,
                    cost_tracker=self.cost_tracker,
                    voice_profile=voice_profile,
                    voice_manager=self.voice_manager,
                    provider="azure",
                )
                segment_results = fallback_tts.synthesize(dialogue_file, run_paths, force=force)
                effective_tts = "azure"
                run_paths.log("Azure Neural TTS fallback succeeded.")
                self.logger.info("[LecturePipeline.run] Fallback to Azure Neural TTS completed successfully.")

            # Normalize TTS outputs into paths + durations
            segment_paths: List[Path] = []
            segment_durations: List[float] = []

            def _probe_duration(path: Path) -> float:
                try:
                    return float(AudioSegment.from_file(path).duration_seconds)
                except Exception as exc:
                    self.logger.warning("Failed to read duration for segment %s: %s", path.name, exc)
                    return 0.0

            for item in segment_results:
                path_val = item
                duration_val = None
                if isinstance(item, dict):
                    path_val = item.get("path")
                    duration_val = item.get("duration")

                path_obj = Path(path_val) if path_val is not None else None
                if path_obj is None:
                    self.logger.warning("Skipping invalid TTS segment entry: %s", item)
                    continue

                segment_paths.append(path_obj)
                duration: float
                if isinstance(duration_val, (int, float)) and float(duration_val) > 0:
                    duration = float(duration_val)
                else:
                    duration = _probe_duration(path_obj)
                segment_durations.append(duration)

            # Measure intro/outro durations used in stitching (speech window excludes these)
            try:
                intro_clip = self.stitcher._load_music(self.settings.intro_music, duration_ms=3000)
                outro_clip = self.stitcher._load_music(self.settings.outro_music, duration_ms=2000)
                lead_in_seconds = float(intro_clip.duration_seconds)
                outro_seconds = float(outro_clip.duration_seconds)
            except Exception as exc:
                self.logger.warning("Failed to probe intro/outro durations: %s", exc)
                lead_in_seconds = 0.0
                outro_seconds = 0.0
            self._stage_times["speech_synthesis"] = time.time() - stage_start
            run_paths.log("Speech synthesis completed.")
            timing.checkpoint("speech_synthesis")
            
            # Stage 4: Audio stitching
            stage_start = time.time()
            run_paths.log("🔊 Stitching Audio...")
            self.logger.info("[LecturePipeline.run] STAGE: Audio stitching (%d segments)", len(segment_paths))
            self.stitcher.build(segment_paths, metadata, run_paths)
            self._stage_times["audio_stitching"] = time.time() - stage_start
            run_paths.log("Audio stitching complete.")
            timing.checkpoint("audio_stitching")

            dialogue_json = json.loads(dialogue_file.read_text(encoding="utf-8"))
            expected_segments = len(dialogue_json.get("dialogue", [])) if isinstance(dialogue_json, dict) else 0

            # Align visual metadata with requested image_count (CLI overrides).
            try:
                desired_image_count = int(getattr(self.settings, "image_count", 5) or 5)
            except (TypeError, ValueError):
                desired_image_count = 5
            current_image_count = len(visual_metadata.get("images", [])) if visual_metadata else 0
            needs_refresh = visual_metadata is not None and current_image_count != desired_image_count

            if needs_refresh:
                self.logger.info(
                    "[LecturePipeline.run] Refreshing visual metadata to match image_count (%d -> %d).",
                    current_image_count,
                    desired_image_count,
                )
                try:
                    refreshed_meta = self._auto_generate_visual_metadata(
                        metadata=metadata,
                        dialogue_json=dialogue_json,
                        transcript_text=transcript_text,
                        run_paths=run_paths,
                    )
                except Exception as refresh_exc:
                    refreshed_meta = None
                    self.logger.warning(
                        "[LecturePipeline.run] Visual metadata refresh failed: %s",
                        refresh_exc,
                        exc_info=True,
                    )

                if refreshed_meta and refreshed_meta.get("images"):
                    visual_metadata = self._merge_visual_metadata(
                        existing=visual_metadata or {},
                        regenerated=refreshed_meta,
                        desired_count=desired_image_count,
                    )

                    target_visual_path = run_paths.run_dir / "visual_metadata.json"
                    try:
                        target_visual_path.write_text(
                            json.dumps(visual_metadata, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        run_paths.log(
                            f"[Visual Metadata] Refreshed to {desired_image_count} prompt(s) per CLI request."
                        )
                        self.logger.info(
                            "[LecturePipeline.run] Visual metadata refreshed and saved to %s",
                            target_visual_path,
                        )
                    except Exception as persist_exc:
                        self.logger.warning(
                            "[LecturePipeline.run] Failed to persist refreshed visual metadata: %s",
                            persist_exc,
                            exc_info=True,
                        )
            
            requested_images = self._requested_image_count()
            if visual_metadata and visual_metadata.get("images"):
                try:
                    refreshed = self._refresh_visual_metadata_if_needed(
                        visual_metadata=visual_metadata,
                        requested_images=requested_images,
                        metadata=metadata,
                        dialogue_json=dialogue_json,
                        transcript_text=transcript_text,
                        run_paths=run_paths,
                    )
                    if refreshed is not None:
                        visual_metadata = refreshed
                        visual_metadata_source = run_paths.run_dir / "visual_metadata.json"
                        visual_metadata_log = (
                            f"[Visual Metadata] Synced to {requested_images} prompt(s) after CLI change."
                        )
                        try:
                            visual_metadata_source.write_text(
                                json.dumps(visual_metadata, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                        except Exception as exc:
                            self.logger.warning(
                                "[LecturePipeline.run] Failed to write refreshed visual metadata: %s",
                                exc,
                            )
                except Exception as exc:
                    self.logger.warning(
                        "[LecturePipeline.run] Visual metadata refresh skipped due to error: %s",
                        exc,
                    )

            if not visual_metadata or not visual_metadata.get("images"):
                run_paths.log("Generating visual metadata from transcript and dialogue.")
                visual_meta_start = time.time()
                auto_visual_metadata = self._auto_generate_visual_metadata(
                    metadata=metadata,
                    dialogue_json=dialogue_json,
                    transcript_text=transcript_text,
                    run_paths=run_paths,
                )
                if auto_visual_metadata:
                    visual_metadata = auto_visual_metadata
                    try:
                        target_visual_path = run_paths.run_dir / "visual_metadata.json"
                        target_visual_path.write_text(
                            json.dumps(visual_metadata, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        visual_metadata_source = target_visual_path
                        visual_metadata_log = (
                            f"[Visual Metadata] Auto-generated {len(visual_metadata.get('images', []))} "
                            "image prompts from transcript/story context"
                        )
                        run_paths.log("Visual metadata generation completed.")
                        self.logger.info(
                            "[LecturePipeline.run] Auto-generated visual metadata saved to %s",
                            target_visual_path,
                        )
                        self._stage_times["visual_metadata_generation"] = time.time() - visual_meta_start
                        timing.checkpoint("visual_metadata_generation")
                    except Exception as exc:
                        self.logger.warning(
                            "[LecturePipeline.run] Failed to persist auto-generated visual metadata: %s",
                            exc,
                        )
                        run_paths.log(f"Visual metadata generation failed: {exc}")
                        self._stage_times["visual_metadata_generation"] = time.time() - visual_meta_start
                        timing.checkpoint("visual_metadata_generation")
                else:
                    self.logger.warning(
                        "[LecturePipeline.run] Auto visual metadata generation failed; "
                        "continuing with generic prompts."
                    )

            if not visual_metadata_logged:
                requires_custom_visuals = (
                    self.settings.enable_visuals
                    and not skip_visuals
                    and getattr(self.settings, "visual_generator", "manim")
                    in ("imagen", "imagen_manim", "hybrid", "veo")
                )
                if visual_metadata_log:
                    run_paths.log(visual_metadata_log)
                elif requires_custom_visuals:
                    run_paths.log("[Visual Metadata] Not found – pipeline will rely on generic prompts.")
                visual_metadata_logged = True
            
            # Stage 5: Video composition (optional)
            execution_assets: List[Path] = []
            if self.settings.enable_visuals and not skip_visuals:
                stage_start = time.time()
                run_paths.log("Starting video composition.")
                self.logger.info("[LecturePipeline.run] STAGE: Video composition")
                
                # Generate visuals based on configured visual_generator setting
                visual_generator = getattr(self.settings, 'visual_generator', 'manim')
                scene_paths: List[Path] = []
                use_manim = visual_generator in ("manim", "imagen_manim", "hybrid")
                use_google_ai = visual_generator in ("imagen", "imagen_manim", "veo", "hybrid")
                use_veo = visual_generator in ("veo", "hybrid")
                
                self.logger.debug("[LecturePipeline.run] Visual settings: generator=%s, manim=%s, google_ai=%s, veo=%s",
                                 visual_generator, use_manim, use_google_ai, use_veo)
                visual_summary = (
                    f"[Visual Settings] generator={visual_generator} | manim={use_manim} | "
                    f"google_ai={use_google_ai} | veo={use_veo}"
                )
                run_paths.log(visual_summary)
                self.logger.info(visual_summary)
                optimized_assets: List[Path] = []

                if use_google_ai:
                    google_status = self.google_ai.status_summary()
                    run_paths.log(f"[Google Imagen] {google_status}")
                    if not self.google_ai.is_available:
                        msg = (
                            "Google Imagen/VEO selected but service is unavailable. "
                            "הפעילו מפתח/חיבור ונסו שוב."
                        )
                        run_paths.log(f"❌ {msg}")
                        self.logger.error("[LecturePipeline.run] %s", msg)
                        raise RuntimeError(msg)
                
                ai_visuals: List[Path] = []
                if use_google_ai:
                    # Generate AI-powered visuals using Google Imagen/VEO
                    image_count = getattr(self.settings, 'image_count', 5)
                    self.logger.info("[LecturePipeline.run] Google AI visuals: image_count=%d (type: %s)", 
                                   image_count, type(image_count).__name__)
                    run_paths.log(f"[Visual Settings] image_count from settings: {image_count}")
                    
                    try:
                        image_count = int(image_count)
                    except (ValueError, TypeError):
                        self.logger.warning("[LecturePipeline.run] Invalid image_count: %s, defaulting to 5", image_count)
                        image_count = 5
                    
                    if use_veo:
                        run_paths.log(f"Generating AI visuals: {image_count} images + up to 3 video clips.")
                    else:
                        run_paths.log(f"Generating AI visuals: {image_count} images.")
                    
                    try:
                        ai_visuals = self.google_ai.build_visuals_for_dialogue(
                            dialogue_json, metadata, run_paths,
                            generate_images=True,
                            generate_videos=use_veo,
                            image_count=image_count,
                            visual_metadata=visual_metadata,
                        )
                        if ai_visuals:
                            image_files = [p for p in ai_visuals if p.suffix.lower() in ('.png', '.jpg', '.jpeg')]
                            video_files = [p for p in ai_visuals if p.suffix.lower() in ('.mp4', '.mov', '.webm')]
                            run_paths.log(f"Generated {len(image_files)} images + {len(video_files)} videos.")
                            self.logger.info("[LecturePipeline.run] Google AI: %d images, %d videos", 
                                           len(image_files), len(video_files))
                            video_meta = (visual_metadata or {}).get("video", {}) if use_veo else {}
                            video_seconds = int(video_meta.get("duration", 0) or 0)
                            visual_costs = self.google_ai.estimate_cost(
                                num_images=len(image_files),
                                num_videos=len(video_files),
                                video_seconds=video_seconds,
                            )
                            self.cost_tracker.add_visual_cost(visual_costs.get("total_cost", 0.0))
                            run_paths.log(
                                f"[Cost] Visuals: ${visual_costs.get('total_cost', 0.0):.2f} "
                                f"(images={len(image_files)}, videos={len(video_files)})"
                            )
                        else:
                            msg = "Selected Google Imagen/VEO returned no visual assets."
                            run_paths.log(f"❌ {msg}")
                            self.logger.error("[LecturePipeline.run] %s", msg)
                            raise RuntimeError(msg)
                    except Exception as exc:
                        msg = f"Selected Google Imagen/VEO failed: {exc}"
                        self.logger.warning("[LecturePipeline.run] %s\n%s", msg, traceback.format_exc())
                        run_paths.log(f"❌ {msg}")
                        raise RuntimeError(msg)
                
                if use_manim:
                    run_paths.log("🎬 Rendering Video (Manim)...")
                    try:
                        scene_paths = self.manim.build_scenes(dialogue_json, run_paths)
                    except Exception as manim_exc:
                        scene_paths = []
                        self.logger.warning("[LecturePipeline.run] Manim generation failed: %s", manim_exc, exc_info=True)
                        run_paths.log(f"❌ Manim generation failed: {manim_exc}")
                        raise RuntimeError(f"Manim generation failed: {manim_exc}")
                    if not scene_paths:
                        msg = "Selected Manim generator produced no scenes."
                        self.logger.error("[LecturePipeline.run] %s", msg)
                        run_paths.log(f"❌ {msg}")
                        raise RuntimeError(msg)
                
                # Guardian: validate assets and auto-heal before composition
                guardian_qc = QualityChecker(self.settings)
                guardian_report = guardian_qc.run_postprocess_checks(
                    run_dir=run_paths.run_dir,
                    expected_segments=expected_segments,
                    run_type=run_type,
                )
                guardian = ProductionGuardian(run_paths, self.settings, guardian_report)
                # Collect all discovered assets so guardian can merge them (images + manim clips)
                base_assets: List[Path] = []
                base_assets.extend(ai_visuals)
                base_assets.extend(scene_paths)
                try:
                    self.logger.info(
                        "[LecturePipeline] Visual assets pre-guardian: %d images, %d videos",
                        len([p for p in base_assets if p.suffix.lower() in ('.png', '.jpg', '.jpeg')]),
                        len([p for p in base_assets if p.suffix.lower() in ('.mp4', '.mov', '.webm')]),
                    )
                except Exception:
                    pass

                guardian_result = guardian.optimize_and_fix(
                    metadata=metadata,
                    visual_metadata=visual_metadata,
                    extra_assets=base_assets,
                )

                optimized_assets = guardian_result.manifest
                if not optimized_assets:
                    msg = "No validated visual assets available after guardian checks."
                    self.logger.error("[LecturePipeline.run] %s", msg)
                    run_paths.log(f"❌ {msg}")
                    raise RuntimeError(msg)
                run_paths.log(f"Visual assets prepared for composition: {len(optimized_assets)} item(s).")
                try:
                    self.logger.info(
                        "[LecturePipeline] Visual assets post-guardian: %d images, %d videos",
                        len([p for p in optimized_assets if p.suffix.lower() in ('.png', '.jpg', '.jpeg')]),
                        len([p for p in optimized_assets if p.suffix.lower() in ('.mp4', '.mov', '.webm')]),
                    )
                except Exception:
                    pass
                if guardian_result.adaptive_timeline:
                    run_paths.log("ProductionGuardian: Adaptive timeline enabled (stretching available visuals).")
                if guardian_result.audio_normalized:
                    run_paths.log("ProductionGuardian: Audio normalized to safe loudness.")
                execution_assets = optimized_assets

                # Compose final video
                try:
                    try:
                        self.video.compose(
                            dialogue_json,
                            metadata,
                            run_paths,
                            asset_paths=optimized_assets,
                            adaptive_timeline=guardian_result.adaptive_timeline,
                            apply_ken_burns=guardian_result.ken_burns,
                            segment_durations=segment_durations,
                            lead_in_seconds=lead_in_seconds,
                            outro_seconds=outro_seconds,
                        )
                    except TypeError:
                        # Backward compatibility with simplified composers (e.g., tests)
                        self.video.compose(
                            dialogue_json,
                            metadata,
                            run_paths,
                            segment_durations=segment_durations,
                            lead_in_seconds=lead_in_seconds,
                            outro_seconds=outro_seconds,
                        )
                    if not run_paths.final_video_path.exists():
                        raise RuntimeError(
                            f"VideoComposer completed without creating file: {run_paths.final_video_path}"
                        )
                    run_paths.log(f"Video composition completed: {run_paths.final_video_path}")
                    self.logger.info("[LecturePipeline.run] Video composition successful: %s", 
                                   run_paths.final_video_path.name)
                except Exception as exc:
                    self.logger.error(
                        "[LecturePipeline.run] Video composition failed: %s\n%s",
                        exc,
                        traceback.format_exc(),
                    )
                    run_paths.log(f"❌ Video composition failed: {exc}")
                    raise
                        run_paths.log(f"Fallback slide rendering failed: {fallback_exc}")
                        raise
                    
                self._stage_times["video_composition"] = time.time() - stage_start
                timing.checkpoint("video_composition")
            else:
                self.logger.info("[LecturePipeline.run] Visual generation skipped")
            
            # Post-processing Quality Checks
            stage_start = time.time()
            run_paths.log("Running post-processing quality checks...")
            self.logger.info("[LecturePipeline.run] Running post-processing quality checks")

            postprocess_report = self.quality_checker.run_postprocess_checks(
                run_dir=run_paths.run_dir,
                expected_segments=expected_segments,
                run_type=run_type,
                expected_assets=execution_assets or None,
            )
            
            # Log postprocess results
            for check in postprocess_report.postprocess_checks:
                status_icon = {"PASS": "✓", "FAIL": "✗", "WARNING": "⚠", "SKIP": "○"}.get(check.status, "?")
                run_paths.log(f"  {status_icon} {check.name}: {check.message}")
                if check.status in ("FAIL", "WARNING"):
                    self.logger.warning("[PostProcess] %s %s: %s", status_icon, check.name, check.message)
                else:
                    self.logger.info("[PostProcess] %s %s: %s", status_icon, check.name, check.message)
            
            # Save the quality report
            report_path = postprocess_report.save(run_paths.run_dir)
            run_paths.log(f"Quality report saved: {report_path.name}")
            self.logger.info("[LecturePipeline.run] Quality report saved: %s", report_path)
            
            # Log summary
            summary = self.quality_checker.get_summary()
            for line in summary.split("\n"):
                run_paths.log(line)
            
            self._stage_times["postprocess_checks"] = time.time() - stage_start
            timing.checkpoint("postprocess_checks")
            
            # AI-Powered Quality Enhancement (optional)
            if getattr(self.settings, "enable_ai_quality_enhancement", False):
                try:
                    ai_stage_start = time.time()
                    run_paths.log("Running AI-powered quality enhancement analysis...")
                    self.logger.info("[LecturePipeline.run] Running AI quality enhancement")
                    
                    enhancement_plan = enhance_with_ai(
                        quality_report=postprocess_report,
                        run_dir=run_paths.run_dir,
                        settings=self.settings,
                        metadata=metadata,
                        dialogue=dialogue_json,
                        apply_auto_fixes=getattr(self.settings, "auto_apply_ai_fixes", False),
                    )
                    
                    if enhancement_plan.suggestions:
                        run_paths.log(f"AI generated {len(enhancement_plan.suggestions)} improvement suggestions")
                        for i, suggestion in enumerate(enhancement_plan.suggestions[:5], 1):  # Top 5
                            run_paths.log(f"  {i}. [{suggestion.severity.upper()}] {suggestion.issue}")
                            run_paths.log(f"     Action: {suggestion.action}")
                        
                        if enhancement_plan.auto_fixes:
                            run_paths.log(f"Applied {len(enhancement_plan.auto_fixes)} automatic fixes")
                    
                    self._stage_times["ai_quality_enhancement"] = time.time() - ai_stage_start
                    timing.checkpoint("ai_quality_enhancement")
                except Exception as enh_exc:
                    self.logger.warning("[LecturePipeline.run] AI quality enhancement failed: %s", enh_exc)
                    run_paths.log(f"AI quality enhancement skipped: {enh_exc}")

            # Stage 6: PPTX export (optional)
            if export_ppt:
                stage_start = time.time()
                ppt_path = run_paths.run_dir / "summary.pptx"
                self.logger.info("[LecturePipeline.run] STAGE: PPTX export")
                self.deck_exporter.export(
                    metadata,
                    dialogue_json,
                    ppt_path,
                    visual_metadata=visual_metadata,
                    visuals_dir=run_paths.visuals_dir,
                )
                self._stage_times["pptx_export"] = time.time() - stage_start
                run_paths.log(f"Slide deck exported to {ppt_path}")
                timing.checkpoint("pptx_export")

            # Stage 7: Story export
            stage_start = time.time()
            story_path = self.story_exporter.export(metadata, dialogue_json, run_paths.run_dir)
            self._stage_times["story_export"] = time.time() - stage_start
            timing.checkpoint("story_export")
            
            run_paths.log("Pipeline execution finished.")
            
            # Log final timing summary
            total_time = time.time() - pipeline_start
            timing.finish()
            
            # Log comprehensive output summary
            self._log_output_summary(run_paths, dialogue_file, story_path, metadata, total_time)
            
        except Exception as exc:
            pipeline_failed = True
            failure_message = str(exc)
            total_time = time.time() - pipeline_start
            self.logger.error("[LecturePipeline.run] ========== PIPELINE FAILED ==========")
            self.logger.error("[LecturePipeline.run] Failed after %.1fs: %s", total_time, failure_message)
            self.logger.error("[LecturePipeline.run] Traceback:\n%s", traceback.format_exc())
            run_paths.log(f"Pipeline execution failed: {failure_message}")
            raise
            
        finally:
            # Always record history, even on failure
            costs = self._log_costs(run_paths)
            self._record_history(
                run_paths,
                metadata,
                dialogue_file,
                export_ppt=export_ppt,
                story_path=story_path,
                costs=costs,
                failed=pipeline_failed,
                failure_message=failure_message,
            )
            
        return run_paths

    def _log_costs(self, run_paths: RunPaths) -> Dict:
        costs = self.cost_tracker.as_dict()
        cost_line = json.dumps(costs)
        self.logger.info("Usage summary: %s", cost_line)
        run_paths.log(f"Cost summary: {cost_line}")

        if costs["tts_characters"] >= 0.8 * self.settings.monthly_tts_character_limit:
            warning = "Approaching monthly TTS character limit."
            self.logger.warning(warning)
            run_paths.log(warning)

        if costs["total_cost_usd"] >= 0.8 * self.settings.monthly_openai_cost_limit:
            warning = "Approaching monthly OpenAI cost limit."
            self.logger.warning(warning)
            run_paths.log(warning)
        return costs

    def _record_history(
        self,
        run_paths: RunPaths,
        metadata: Dict,
        dialogue_file: Optional[Path],
        export_ppt: bool,
        story_path: Optional[Path],
        costs: Dict,
        failed: bool = False,
        failure_message: str = "",
    ) -> None:
        entry = {
            "topic": metadata.get("topic"),
            "date": metadata.get("date"),
            "run_dir": str(run_paths.run_dir),
            "dialogue": str(dialogue_file) if dialogue_file else "",
            "final_audio": str(run_paths.final_audio_path) if run_paths.final_audio_path.exists() else "",
            "final_video": str(run_paths.final_video_path) if run_paths.final_video_path.exists() else "",
            "labs": metadata.get("labs", []),
            "summary": metadata.get("summary", ""),
            "key_concepts": metadata.get("key_concepts", []),
            "reading_list": metadata.get("reading_list", []),
            "slide_deck": str(run_paths.run_dir / "summary.pptx") if export_ppt else "",
            "costs": costs,
            "story": str(story_path) if story_path else "",
            "failed": failed,
            "failure_message": failure_message if failed else "",
        }
        self.history.record(entry)

    def _auto_generate_visual_metadata(
        self,
        metadata: Dict,
        dialogue_json: Dict,
        transcript_text: str,
        run_paths: RunPaths,
    ) -> Optional[Dict]:
        """
        Build detailed visual metadata when no curated file was provided.

        Attempts a story-driven builder first, then falls back to universal prompts.
        """
        target_images = getattr(self.settings, "image_count", 10) or 10
        try:
            target_images = int(target_images)
        except (TypeError, ValueError):
            target_images = 10
        target_images = max(1, target_images)

        try:
            visual_metadata = build_visual_metadata_locally(
                metadata=metadata,
                dialogue_json=dialogue_json or {},
                transcript_text=transcript_text or "",
                image_count=target_images,
                include_video=True,
            )
            if visual_metadata.get("images"):
                self.logger.info(
                    "[LecturePipeline] Local visual metadata generated (%d images).",
                    len(visual_metadata.get("images", [])),
                )
            else:
                self.logger.warning("[LecturePipeline] Local visual metadata is empty.")
            return visual_metadata
        except Exception as exc:
            self.logger.error(
                "[LecturePipeline] Local visual metadata generation failed: %s",
                exc,
            )
            return generate_universal_visual_metadata(
                metadata,
                transcript_length=len(transcript_text or ""),
                image_count=target_images,
                include_video=True,
            )

    def _metadata_needs_seed(self, metadata: Dict) -> bool:
        essential = ("topic", "summary", "key_concepts")
        return not any(metadata.get(field) for field in essential)

    def _requested_image_count(self) -> int:
        """
        Normalize the requested image_count from settings.
        """
        try:
            return max(1, int(getattr(self.settings, "image_count", 5)))
        except Exception:
            return 5

    def _merge_visual_metadata(
        self,
        existing: Dict,
        regenerated: Dict,
        desired_count: int,
    ) -> Dict:
        """
        Append or truncate images to desired_count without overwriting curated fields.
        """
        merged = dict(existing)
        base_images = list(existing.get("images", [])) if existing else []
        new_images = regenerated.get("images", []) if regenerated else []

        combined: List[Dict] = []
        for img in base_images:
            combined.append(img)
            if len(combined) >= desired_count:
                break

        next_index = len(combined) + 1
        for img in new_images:
            if len(combined) >= desired_count:
                break
            clone = dict(img)
            clone.setdefault("index", next_index)
            combined.append(clone)
            next_index += 1

        merged["images"] = combined[:desired_count]

        for key in ("video", "background", "general_notes"):
            if key not in merged and regenerated.get(key) is not None:
                merged[key] = regenerated.get(key)

        return merged

    def _refresh_visual_metadata_if_needed(
        self,
        visual_metadata: Dict,
        requested_images: int,
        metadata: Dict,
        dialogue_json: Dict,
        transcript_text: str,
        run_paths: RunPaths,
    ) -> Optional[Dict]:
        """
        If the existing visual_metadata.json has fewer (or more) prompts than requested,
        regenerate to match the current CLI/settings image_count.
        """
        existing_images = visual_metadata.get("images", []) or []
        if len(existing_images) == requested_images:
            return None

        self.logger.info(
            "[LecturePipeline] Refreshing visual_metadata prompts to match image_count (%d -> %d).",
            len(existing_images),
            requested_images,
        )

        try:
            regenerated = build_visual_metadata_locally(
                metadata=metadata,
                dialogue_json=dialogue_json or {},
                transcript_text=transcript_text or "",
                image_count=requested_images,
                include_video=True,
            )
        except Exception as exc:
            self.logger.warning(
                "[LecturePipeline] Local visual metadata refresh failed (%s); using universal builder.",
                exc,
            )
            regenerated = generate_universal_visual_metadata(
                metadata,
                transcript_length=len(transcript_text or ""),
                image_count=requested_images,
                include_video=True,
            )

        new_images = regenerated.get("images", []) if regenerated else []
        if not new_images:
            self.logger.warning("[LecturePipeline] Regenerated visual metadata is empty; keeping existing prompts.")
            return None

        refreshed = self._merge_visual_metadata(
            existing=visual_metadata,
            regenerated={"images": new_images, **{k: v for k, v in (regenerated or {}).items() if k != "images"}},
            desired_count=requested_images,
        )

        target_path = run_paths.run_dir / "visual_metadata.json"
        try:
            target_path.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2), encoding="utf-8")
            self.logger.info("[LecturePipeline] Synced refreshed visual_metadata to %s", target_path)
        except Exception as exc:
            self.logger.warning(
                "[LecturePipeline] Failed to persist refreshed visual metadata: %s",
                exc,
            )

        return refreshed

    def _seed_metadata_from_transcript(self, metadata: Dict, transcript_text: str) -> Dict:
        self.logger.info("Metadata incomplete – attempting to auto-seed from transcript.")
        try:
            session = MetadataChatSession(self.settings)
            session.import_metadata(metadata)
            session.bootstrap_from_transcript(transcript_text, force=True)
            enriched = session.export_metadata()
            self.logger.info("Metadata was auto-seeded successfully.")
            return enriched
        except Exception as exc:
            self.logger.warning("Auto metadata seed failed: %s", exc)
            return metadata

    def _log_output_summary(
        self,
        run_paths: RunPaths,
        dialogue_file: Optional[Path],
        story_path: Optional[Path],
        metadata: Dict,
        total_time: float,
    ) -> None:
        """
        Log comprehensive output summary with actual file information.
        """
        self.logger.info("=" * 60)
        self.logger.info("        PIPELINE OUTPUT SUMMARY")
        self.logger.info("=" * 60)
        self.logger.info("")
        
        # Timing summary
        self.logger.info("TIMING:")
        self.logger.info("  Total time: %.1fs (%.1f minutes)", total_time, total_time / 60)
        for stage, elapsed in self._stage_times.items():
            pct = (elapsed / total_time) * 100 if total_time > 0 else 0
            self.logger.info("    %s: %.1fs (%.1f%%)", stage, elapsed, pct)
        
        self.logger.info("")
        self.logger.info("GENERATED FILES:")
        
        # Audio output
        if run_paths.final_audio_path.exists():
            audio_size = run_paths.final_audio_path.stat().st_size / (1024 * 1024)
            self.logger.info("  Audio: %s (%.2f MB)", run_paths.final_audio_path.name, audio_size)
            run_paths.log(f"Output audio: {run_paths.final_audio_path.name} ({audio_size:.2f} MB)")
        else:
            self.logger.warning("  Audio: NOT FOUND")
        
        target_lang = getattr(self.settings, "target_language", "")
        self.logger.info(
            "  Language: transcript=%s (mixed=%s he=%.3f en=%.3f detector=%s) target=%s",
            run_paths.transcript_language or "unknown",
            run_paths.transcript_is_mixed,
            run_paths.transcript_hebrew_ratio,
            run_paths.transcript_latin_ratio,
            run_paths.transcript_language_detector or "n/a",
            target_lang or "auto/detected",
        )
        try:
            run_paths.log(
                f"Language summary -> transcript={run_paths.transcript_language or 'unknown'} "
                f"(mixed={run_paths.transcript_is_mixed}, he={run_paths.transcript_hebrew_ratio:.3f}, "
                f"en={run_paths.transcript_latin_ratio:.3f}, detector={run_paths.transcript_language_detector or 'n/a'}) "
                f"target={target_lang or 'auto/detected'}"
            )
        except Exception:
            pass
        
        # Video output
        if run_paths.final_video_path.exists():
            video_size = run_paths.final_video_path.stat().st_size / (1024 * 1024)
            self.logger.info("  Video: %s (%.2f MB)", run_paths.final_video_path.name, video_size)
            run_paths.log(f"Output video: {run_paths.final_video_path.name} ({video_size:.2f} MB)")
        else:
            self.logger.info("  Video: Not generated (skip_visuals or disabled)")
        
        # Dialogue file
        if dialogue_file and dialogue_file.exists():
            self.logger.info("  Dialogue: %s", dialogue_file.name)
        
        # Story file
        if story_path and story_path.exists():
            self.logger.info("  Story: %s", story_path.name)
        
        # Count visual files
        visual_files = list(run_paths.visuals_dir.glob("*")) if run_paths.visuals_dir.exists() else []
        image_files = [f for f in visual_files if f.suffix.lower() in ('.png', '.jpg', '.jpeg')]
        video_clips = [f for f in visual_files if f.suffix.lower() in ('.mp4', '.mov', '.webm')]
        
        self.logger.info("  Visuals: %d images, %d video clips", len(image_files), len(video_clips))
        for img in image_files[:5]:  # Show first 5
            self.logger.info("    - %s", img.name)
        if len(image_files) > 5:
            self.logger.info("    ... and %d more", len(image_files) - 5)
        
        run_paths.log(f"Output visuals: {len(image_files)} images, {len(video_clips)} video clips")
        
        # Audio segments
        audio_segments = list(run_paths.audio_dir.glob("*.wav")) if run_paths.audio_dir.exists() else []
        self.logger.info("  Audio segments: %d", len(audio_segments))
        
        self.logger.info("")
        self.logger.info("OUTPUT DIRECTORY:")
        self.logger.info("  %s", run_paths.run_dir)
        
        self.logger.info("")
        self.logger.info("METADATA USED:")
        self.logger.info("  Topic: %s", metadata.get('topic', 'N/A'))
        key_concepts = metadata.get('key_concepts', [])
        self.logger.info("  Key concepts: %d", len(key_concepts))
        
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("        PIPELINE COMPLETED SUCCESSFULLY")
        self.logger.info("=" * 60)

    def _log_settings_summary(
        self,
        run_paths: RunPaths,
        tts_provider: Optional[str],
        voice_profile: Optional[str],
        skip_visuals: bool,
        metadata: Dict,
    ) -> None:
        """
        Log comprehensive settings summary for verification.
        
        This helps verify that all user-selected options are being applied
        in the final output.
        """
        # Visual settings
        visual_generator = getattr(self.settings, 'visual_generator', 'manim')
        imagen_model = getattr(self.settings, 'imagen_model', 'imagen-4.0-generate-001')
        veo_model = getattr(self.settings, 'veo_model', 'veo-2.0-generate-001')
        image_count = getattr(self.settings, 'image_count', 5)
        auto_duration = getattr(self.settings, 'auto_video_duration', True)
        video_duration = getattr(self.settings, 'video_duration_seconds', 300)
        
        # TTS settings
        effective_tts = tts_provider or self.settings.default_tts_provider
        effective_profile = voice_profile or self.settings.default_voice_profile
        voice_overrides = getattr(self.settings, 'elevenlabs_voice_overrides', {})
        
        # Metadata info
        topic = metadata.get('topic', 'Not set')
        key_concepts = metadata.get('key_concepts', [])
        
        # Build summary
        summary_lines = [
            "=" * 50,
            "        PIPELINE SETTINGS SUMMARY",
            "=" * 50,
            "",
            "VISUAL SETTINGS:",
            f"  Visual Generator: {visual_generator}",
            f"  Imagen Model: {imagen_model}",
            f"  VEO Model: {veo_model}",
            f"  Image Count: {image_count}",
            f"  Auto Duration: {auto_duration}",
            f"  Video Duration: {video_duration}s" if not auto_duration else "  Video Duration: (auto)",
            f"  Skip Visuals: {skip_visuals}",
            "",
            "TTS SETTINGS:",
            f"  Provider: {effective_tts}",
            f"  Voice Profile: {effective_profile}",
        ]
        
        # Add voice overrides if using ElevenLabs
        if effective_tts == "elevenlabs" and voice_overrides:
            summary_lines.append("  Voice Overrides:")
            for speaker, config in voice_overrides.items():
                voice_id = config.get('voice_id', 'N/A')
                # Show first 12 chars of voice_id for brevity
                voice_id_short = voice_id[:12] + "..." if len(voice_id) > 12 else voice_id
                summary_lines.append(f"    - {speaker}: {voice_id_short}")
        elif effective_tts == "elevenlabs":
            summary_lines.append("  Voice Overrides: (none - using profile defaults)")
        
        summary_lines.extend([
            "",
            "METADATA:",
            f"  Topic: {topic}",
            f"  Key Concepts: {len(key_concepts)} defined",
        ])
        
        if key_concepts:
            for concept in key_concepts[:5]:
                summary_lines.append(f"    - {concept}")
            if len(key_concepts) > 5:
                summary_lines.append(f"    ... and {len(key_concepts) - 5} more")
        
        summary_lines.extend([
            "",
            "=" * 50,
        ])
        
        # Log to both logger and run_paths
        summary_text = "\n".join(summary_lines)
        self.logger.info("[LecturePipeline] Settings Summary:\n%s", summary_text)
        run_paths.log("Settings Summary:")
        for line in summary_lines:
            run_paths.log(line)

    def _estimate_dialogue_characters(self, dialogue_file: Optional[Path]) -> int:
        """
        Estimate total character count from dialogue file for TTS quota checking.
        
        Args:
            dialogue_file: Path to the dialogue JSON file
            
        Returns:
            Estimated total characters to synthesize
        """
        if not dialogue_file or not dialogue_file.exists():
            return 0
        
        try:
            data = json.loads(dialogue_file.read_text(encoding="utf-8"))
            entries = data.get("dialogue", [])
            total_chars = sum(len(entry.get("text", "")) for entry in entries)
            self.logger.debug("[LecturePipeline] Estimated %d characters from %d dialogue entries",
                            total_chars, len(entries))
            return total_chars
        except Exception as exc:
            self.logger.warning("[LecturePipeline] Could not estimate characters: %s", exc)
            return 0

    def run_quality_check_only(
        self,
        run_dir: Optional[Path] = None,
        check_quota: bool = True,
    ) -> "QualityChecker":
        """
        Run quality checks without executing the pipeline.
        
        Useful for validating configuration or checking existing outputs.
        
        Args:
            run_dir: Optional path to existing output directory for post-processing checks
            check_quota: Whether to check ElevenLabs quota
            
        Returns:
            QualityChecker instance with populated report
        """
        self.logger.info("[LecturePipeline] Running standalone quality check")
        
        # Run preflight checks
        self.quality_checker.run_preflight_checks(
            estimated_characters=0,
            skip_quota_check=not check_quota,
        )
        
        # Run postprocess checks if run_dir provided
        if run_dir and run_dir.exists():
            self.quality_checker.run_postprocess_checks(run_dir=run_dir)
        
        return self.quality_checker


def classify_content_type(metadata: Dict) -> str:
    """Classify content type for appropriate visual styling."""
    topic = metadata.get("topic", "").lower()
    concepts = [c.lower() for c in metadata.get("key_concepts", [])]

    # Technical/Programming content
    if any(word in topic for word in ["programming", "code", "python", "javascript", "algorithm"]):
        return "technical_programming"

    # Networking/Infrastructure content
    if any(word in " ".join(concepts) for word in ["network", "server", "cloud", "azure", "aws", "infrastructure"]):
        return "technical_networking"

    # Business/Management content
    if any(word in topic for word in ["business", "management", "leadership", "strategy"]):
        return "business_professional"

    # Science/Medical content
    if any(word in topic for word in ["science", "medical", "health", "biology"]):
        return "science_medical"

    # Creative/Artistic content
    if any(word in topic for word in ["art", "music", "design", "creative"]):
        return "creative_artistic"

    # Educational/General content
    return "educational_general"


def generate_universal_prompt(concept: str, content_type: str, topic: str) -> str:
    """Generate universal AI prompt that works for any educational content."""

    # Base prompt structure
    base_prompts = {
        "technical_programming": f"Clean code editor interface showing {concept}, syntax highlighting, professional development environment, dark theme, high contrast, technical diagram overlay",
        "technical_networking": f"Professional network infrastructure diagram illustrating {concept}, clean technical drawing, server racks, data flow arrows, modern data center aesthetic, blue and green color scheme",
        "business_professional": f"Professional business presentation slide about {concept}, clean corporate design, charts and graphs, modern office environment, trustworthy and professional appearance",
        "science_medical": f"Scientific illustration of {concept}, detailed anatomical or molecular diagram, laboratory equipment, professional medical aesthetic, clean and precise",
        "creative_artistic": f"Creative artistic representation of {concept}, modern digital art style, vibrant colors, innovative composition, artistic freedom with professional quality",
        "educational_general": f"Educational infographic explaining {concept}, clean modern design, icons and diagrams, easy to understand, professional educational style"
    }

    prompt = base_prompts.get(content_type, base_prompts["educational_general"])

    # Add quality enhancements
    quality_additions = ", highly detailed, professional quality, 4K resolution, educational context, clear and understandable"

    return f"{prompt}{quality_additions}"


def determine_visual_style(content_type: str) -> str:
    """Determine appropriate visual style based on content type."""
    styles = {
        "technical_programming": "clean technical diagram, code interface, modern UI design",
        "technical_networking": "professional network diagram, infrastructure visualization, technical illustration",
        "business_professional": "corporate presentation, business infographic, professional chart design",
        "science_medical": "scientific illustration, medical diagram, laboratory photography",
        "creative_artistic": "modern digital art, creative illustration, artistic composition",
        "educational_general": "educational infographic, clean diagram, instructional design"
    }
    return styles.get(content_type, "educational infographic, professional design")


def determine_emotional_mood(concept: str) -> str:
    """Determine emotional mood based on concept content."""
    concept_lower = concept.lower()

    if any(word in concept_lower for word in ["challenge", "difficulty", "problem", "issue"]):
        return "focused and determined"
    elif any(word in concept_lower for word in ["success", "achievement", "growth"]):
        return "motivated and positive"
    elif any(word in concept_lower for word in ["security", "protection", "safety"]):
        return "trustworthy and reliable"
    elif any(word in concept_lower for word in ["innovation", "creative", "new"]):
        return "innovative and dynamic"
    else:
        return "educational and informative"


def determine_color_palette(content_type: str) -> str:
    """Determine appropriate color palette based on content type."""
    palettes = {
        "technical_programming": "dark theme with syntax highlighting colors, blue and green accents",
        "technical_networking": "professional blue and gray, network cable colors, data flow blues",
        "business_professional": "corporate blues and grays, professional gold accents, trustworthy colors",
        "science_medical": "clean whites and blues, medical greens, laboratory color schemes",
        "creative_artistic": "vibrant and expressive colors, artistic freedom, modern palettes",
        "educational_general": "clean educational colors, high contrast for readability, professional blues"
    }
    return palettes.get(content_type, "professional educational colors, high contrast")


def determine_content_aesthetics(content_type: str) -> str:
    """Determine overall aesthetic approach based on content type."""
    aesthetics = {
        "technical_programming": "אסתטיקה טכנית נקייה, ממשקי משתמש מודרניים, קוד ודיאגרמות טכניות",
        "technical_networking": "אסתטיקה מקצועית טכנית, דיאגרמות רשת, צבעים כחולים ואפורים",
        "business_professional": "אסתטיקה קורפורטיבית, מצגות עסקיות, צבעים אמינים ומקצועיים",
        "science_medical": "אסתטיקה מדעית מדויקת, איורים מקצועיים, צבעים נקיים ומרפאיים",
        "creative_artistic": "אסתטיקה יצירתית ומודרנית, צבעים חיים, ביטוי אמנותי",
        "educational_general": "אסתטיקה חינוכית ברורה, עיצוב נגיש, צבעים מקצועיים"
    }
    return aesthetics.get(content_type, "אסתטיקה חינוכית מקצועית, עיצוב ברור ונגיש")
