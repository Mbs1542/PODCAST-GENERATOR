"""
M.B.S Studio - Quality Verification System
==========================================

Comprehensive pre-flight and post-processing quality checks
for the podcast generation pipeline.

Features:
    - ElevenLabs quota verification
    - Voice ID and Hebrew support validation
    - Audio segment quality checks
    - Video output validation
    - Quality report generation

Author: M.B.S Studio
Version: 1.0.0
"""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from pydub import AudioSegment

from .logging import get_logger

# Voice IDs that support Hebrew with eleven_v3
HEBREW_SUPPORTED_VOICES = {
    "21m00Tcm4TlvDq8ikWAM",  # Rachel - female, warm
    "AZnzlk1XvdvUeBnXmlld",  # Domi - female, energetic
    "EXAVITQu4vr4xnSDxMaL",  # Bella - female, soft
    "ErXwobaYiN019PkySvjV",  # Antoni - male, friendly
    "pNInz6obpgDQGcFmaJgB",  # Adam - male, deep
}

# Per ElevenLabs' model docs only the v3/v4 generation reads Hebrew. eleven_v4 is served by the
# Text to Dialogue API (both hosts together); single-line synthesis uses eleven_v3.
HEBREW_RECOMMENDED_MODEL = "eleven_v3"
HEBREW_COMPATIBLE_MODELS = {"eleven_v3", "eleven_v4"}

# Models without Hebrew (Turbo/Flash v2.5 are also deprecated); text would be read with the wrong
# pronunciation or refused with language_code='he'
HEBREW_INCOMPATIBLE_MODELS = {
    "eleven_multilingual_v2", "eleven_multilingual_v1", "eleven_monolingual_v1",
    "eleven_turbo_v2", "eleven_turbo_v2_5", "eleven_flash_v2", "eleven_flash_v2_5",
}


@dataclass
class CheckResult:
    """Result of a single quality check."""
    
    name: str
    status: str  # "PASS", "FAIL", "WARNING", "SKIP"
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class QualityReport:
    """Complete quality report for a pipeline run."""
    
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    run_type: str = "FULL"
    overall_status: str = "PENDING"
    preflight_checks: List[CheckResult] = field(default_factory=list)
    postprocess_checks: List[CheckResult] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    
    def add_preflight(self, check: CheckResult) -> None:
        self.preflight_checks.append(check)
        self._update_overall_status()
    
    def add_postprocess(self, check: CheckResult) -> None:
        self.postprocess_checks.append(check)
        self._update_overall_status()
    
    def _update_overall_status(self) -> None:
        all_checks = self.preflight_checks + self.postprocess_checks
        if any(c.status == "FAIL" for c in all_checks):
            self.overall_status = "FAIL"
        elif any(c.status == "WARNING" for c in all_checks):
            self.overall_status = "WARNING"
        elif all(c.status in ("PASS", "SKIP") for c in all_checks):
            self.overall_status = "PASS"
        else:
            self.overall_status = "PENDING"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "run_type": self.run_type,
            "overall_status": self.overall_status,
            "preflight": {c.name: c.to_dict() for c in self.preflight_checks},
            "postprocess": {c.name: c.to_dict() for c in self.postprocess_checks},
            "recommendations": self.recommendations,
        }
    
    def save(self, output_path: Path) -> Path:
        """Save report to JSON file."""
        report_file = output_path / "quality_report.json"
        report_file.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return report_file


class QualityChecker:
    """
    Quality verification system for the M.B.S Studio pipeline.
    
    Provides pre-flight checks before TTS synthesis and post-processing
    checks after video composition to ensure output quality.
    """
    
    # Minimum file size thresholds
    MIN_AUDIO_SEGMENT_SIZE_KB = 5
    MIN_FINAL_AUDIO_SIZE_KB = 50
    MIN_VIDEO_SIZE_KB = 250  # default; preview uses dynamic threshold
    
    # Expected video specs
    EXPECTED_VIDEO_WIDTH = 1920
    EXPECTED_VIDEO_HEIGHT = 1080
    EXPECTED_AUDIO_SAMPLE_RATE = 44100
    
    def __init__(self, settings: Any) -> None:
        """
        Initialize the quality checker.
        
        Args:
            settings: Application Settings object
        """
        self.settings = settings
        self.logger = get_logger(self.__class__.__name__)
        self.report = QualityReport()
        self._elevenlabs_client = None
    
    # =========================================================================
    # PRE-FLIGHT CHECKS
    # =========================================================================
    
    def run_preflight_checks(
        self,
        estimated_characters: int = 0,
        skip_quota_check: bool = False,
        run_type: str = "FULL",
    ) -> QualityReport:
        """
        Run all pre-flight checks before pipeline execution.
        
        Args:
            estimated_characters: Estimated total characters to synthesize
            skip_quota_check: Skip ElevenLabs API quota check (for dry-run)
            run_type: "PREVIEW" or "FULL" for reporting
            
        Returns:
            QualityReport with all check results
        """
        self.logger.info("[QualityChecker] Running pre-flight checks...")
        self.report = QualityReport(run_type=run_type)
        
        # Check 1: API Keys
        self.report.add_preflight(self._check_api_keys())
        
        # Check 2: Visual engine configuration (Imagen/VEO/Manim)
        self.report.add_preflight(self._check_visual_engine())
        
        # Check 3: Voice Configuration
        self.report.add_preflight(self._check_voice_configuration())
        
        # Check 4: Hebrew Support
        self.report.add_preflight(self._check_hebrew_support())
        
        # Check 5: Model Compatibility
        self.report.add_preflight(self._check_model_compatibility())
        
        # Check 6: ElevenLabs Quota (optional)
        if not skip_quota_check and self.settings.default_tts_provider == "elevenlabs":
            self.report.add_preflight(
                self._check_elevenlabs_quota(estimated_characters)
            )
        else:
            self.report.add_preflight(CheckResult(
                name="quota_check",
                status="SKIP",
                message="Quota check skipped (dry-run or Azure TTS)",
            ))
        
        self.logger.info(
            "[QualityChecker] Pre-flight checks complete: %s",
            self.report.overall_status
        )
        return self.report

    def _check_visual_engine(self) -> CheckResult:
        """Log and validate visual engine selection (manim / google_ai / hybrid)."""
        visual_generator = getattr(self.settings, "visual_generator", "manim")
        enable_visuals = bool(getattr(self.settings, "enable_visuals", True))
        gemini_key = bool(getattr(self.settings, "gemini_api_key", ""))
        imagen_model = getattr(self.settings, "imagen_model", "")
        veo_model = getattr(self.settings, "veo_model", "")
        
        details = {
            "visual_generator": visual_generator,
            "enable_visuals": enable_visuals,
            "imagen_model": imagen_model,
            "veo_model": veo_model,
            "gemini_key_present": gemini_key,
        }
        
        # Quick log for traceability
        self.logger.info(
            "[QualityChecker] Visual engine: %s | enable_visuals=%s | imagen=%s | veo=%s | gemini_key=%s",
            visual_generator,
            enable_visuals,
            imagen_model or "unset",
            veo_model or "unset",
            "yes" if gemini_key else "no",
        )
        
        if not enable_visuals:
            return CheckResult(
                name="visual_engine",
                status="SKIP",
                message="Visual generation disabled (ENABLE_VISUALS=False)",
                details=details,
            )
        
        if visual_generator in {"google_ai", "hybrid"}:
            if not gemini_key:
                return CheckResult(
                    name="visual_engine",
                    status="FAIL",
                    message="Google visuals selected but GEMINI_API_KEY is missing",
                    details=details,
                )
            missing_models = []
            if not imagen_model:
                missing_models.append("IMAGEN_MODEL")
            if visual_generator == "hybrid" and not veo_model:
                missing_models.append("VEO_MODEL")
            if missing_models:
                return CheckResult(
                    name="visual_engine",
                    status="FAIL",
                    message=f"Missing Google AI model settings: {', '.join(missing_models)}",
                    details=details,
                )
            return CheckResult(
                name="visual_engine",
                status="PASS",
                message=f"Google visuals enabled ({visual_generator})",
                details=details,
            )
        
        # Default: manim-only path
        return CheckResult(
            name="visual_engine",
            status="PASS",
            message=f"Using '{visual_generator}' visual generator (no Google AI required)",
            details=details,
        )
    
    def _check_api_keys(self) -> CheckResult:
        """Verify required API keys are configured."""
        missing = []
        
        if self.settings.default_tts_provider == "elevenlabs":
            if not self.settings.elevenlabs_api_key:
                missing.append("ELEVENLABS_API_KEY")
        elif self.settings.default_tts_provider == "azure":
            if not self.settings.speech_key:
                missing.append("AZURE_SPEECH_KEY")
            if not self.settings.speech_region:
                missing.append("AZURE_SPEECH_REGION")
        
        if not self.settings.openai_api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        
        if missing:
            return CheckResult(
                name="api_keys",
                status="FAIL",
                message=f"Missing API keys: {', '.join(missing)}",
                details={"missing_keys": missing},
            )
        
        return CheckResult(
            name="api_keys",
            status="PASS",
            message="All required API keys are configured",
            details={"tts_provider": self.settings.default_tts_provider},
        )
    
    def _check_voice_configuration(self) -> CheckResult:
        """Verify voice configuration is valid."""
        if self.settings.default_tts_provider != "elevenlabs":
            return CheckResult(
                name="voice_configuration",
                status="SKIP",
                message="Voice configuration check skipped (not using ElevenLabs)",
            )
        
        overrides = getattr(self.settings, "elevenlabs_voice_overrides", {}) or {}
        
        if not overrides:
            return CheckResult(
                name="voice_configuration",
                status="WARNING",
                message="No voice overrides configured, using profile defaults",
                details={"overrides": {}},
            )
        
        configured = {}
        for speaker, config in overrides.items():
            voice_id = config.get("voice_id", "")
            model = config.get("model", HEBREW_RECOMMENDED_MODEL)
            configured[speaker] = {
                "voice_id": voice_id[:12] + "..." if len(voice_id) > 12 else voice_id,
                "model": model,
            }
        
        return CheckResult(
            name="voice_configuration",
            status="PASS",
            message=f"Voice overrides configured for {len(configured)} speaker(s)",
            details={"configured_speakers": configured},
        )
    
    def _check_hebrew_support(self) -> CheckResult:
        """Verify selected voices support Hebrew."""
        if self.settings.default_tts_provider != "elevenlabs":
            return CheckResult(
                name="hebrew_support",
                status="SKIP",
                message="Hebrew support check skipped (not using ElevenLabs)",
            )
        
        overrides = getattr(self.settings, "elevenlabs_voice_overrides", {}) or {}
        
        if not overrides:
            return CheckResult(
                name="hebrew_support",
                status="WARNING",
                message="No voice overrides to check for Hebrew support",
            )
        
        unsupported = []
        supported = []
        
        for speaker, config in overrides.items():
            voice_id = config.get("voice_id", "")
            if voice_id in HEBREW_SUPPORTED_VOICES:
                supported.append(speaker)
            else:
                unsupported.append(speaker)
        
        if unsupported:
            self.report.recommendations.append(
                f"Consider using Hebrew-supported voices for: {', '.join(unsupported)}. "
                "Recommended: Rachel (21m00Tcm4TlvDq8ikWAM) or Antoni (ErXwobaYiN019PkySvjV)"
            )
            return CheckResult(
                name="hebrew_support",
                status="WARNING",
                message=f"Some voices may not fully support Hebrew: {', '.join(unsupported)}",
                details={
                    "supported": supported,
                    "unsupported": unsupported,
                    "known_hebrew_voices": list(HEBREW_SUPPORTED_VOICES),
                },
            )
        
        return CheckResult(
            name="hebrew_support",
            status="PASS",
            message=f"All {len(supported)} configured voice(s) support Hebrew",
            details={"supported_speakers": supported},
        )
    
    def _check_model_compatibility(self) -> CheckResult:
        """Verify TTS model is compatible with Hebrew."""
        if self.settings.default_tts_provider != "elevenlabs":
            return CheckResult(
                name="model_compatibility",
                status="SKIP",
                message="Model compatibility check skipped (not using ElevenLabs)",
            )
        
        overrides = getattr(self.settings, "elevenlabs_voice_overrides", {}) or {}
        model_entries: List[Tuple[str, str, str]] = []
        source = "voice_overrides"
        
        if overrides:
            for speaker, config in overrides.items():
                model = config.get("model", HEBREW_RECOMMENDED_MODEL)
                model_entries.append((speaker, model, "override"))
        else:
            model_entries = self._load_profile_voice_models()
            source = "voice_profile"
        
        if not model_entries:
            return CheckResult(
                name="model_compatibility",
                status="WARNING",
                message="No ElevenLabs voices configured; using built-in defaults",
                details={"source": source},
            )
        
        incompatible = [
            (speaker, model) for speaker, model, _ in model_entries if model in HEBREW_INCOMPATIBLE_MODELS
        ]
        if incompatible:
            offending = incompatible[0]
            self.report.recommendations.append(
                f"Model '{offending[1]}' does NOT support Hebrew. "
                f"Change to '{HEBREW_RECOMMENDED_MODEL}' in voice settings or voice profile."
            )
            return CheckResult(
                name="model_compatibility",
                status="FAIL",
                message=f"Hebrew-incompatible model detected: {offending[1]}",
                details={
                    "incompatible": incompatible,
                    "source": source,
                    "recommended_model": HEBREW_RECOMMENDED_MODEL,
                    "compatible_models": list(HEBREW_COMPATIBLE_MODELS),
                },
            )
        
        unknown = [
            (speaker, model) for speaker, model, _ in model_entries
            if model not in HEBREW_COMPATIBLE_MODELS and model not in HEBREW_INCOMPATIBLE_MODELS
        ]
        if unknown:
            return CheckResult(
                name="model_compatibility",
                status="WARNING",
                message=f"Unknown model(s): {[m for _, m in unknown]}. May not support Hebrew.",
                details={
                    "unknown": unknown,
                    "source": source,
                    "compatible_models": list(HEBREW_COMPATIBLE_MODELS),
                },
            )
        
        compatible = [(speaker, model) for speaker, model, _ in model_entries]
        return CheckResult(
            name="model_compatibility",
            status="PASS",
            message=f"All {len(compatible)} {source.replace('_', ' ')} model(s) support Hebrew",
            details={"models": dict(compatible), "source": source},
        )

    def _load_profile_voice_models(self) -> List[Tuple[str, str, str]]:
        """Return (speaker, model, profile_name) tuples from the active voice profile."""
        voice_path = Path(getattr(self.settings, "voice_profile_path", ""))
        if not voice_path.exists():
            return []
        
        try:
            data = json.loads(voice_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("[QualityChecker] Failed to read voice profile %s: %s", voice_path, exc)
            return []
        
        default_profile = getattr(self.settings, "default_voice_profile", "") or data.get("default_profile")
        profiles = data.get("profiles", {})
        if default_profile not in profiles and profiles:
            default_profile = next(iter(profiles))
        profile = profiles.get(default_profile, {})
        voices = profile.get("voices", {})
        
        models: List[Tuple[str, str, str]] = []
        for speaker, payload in voices.items():
            if not isinstance(payload, dict):
                continue
            eleven_cfg = payload.get("elevenlabs")
            if isinstance(eleven_cfg, dict) and eleven_cfg.get("voice_id"):
                model = eleven_cfg.get("model", HEBREW_RECOMMENDED_MODEL)
                models.append((speaker, model, default_profile))
        return models
    
    def _check_elevenlabs_quota(self, estimated_characters: int) -> CheckResult:
        """Check ElevenLabs API quota."""
        try:
            from elevenlabs.client import ElevenLabs

            if not self.settings.elevenlabs_api_key:
                return CheckResult(
                    name="quota_check",
                    status="FAIL",
                    message="ElevenLabs API key not configured",
                )

            client = ElevenLabs(api_key=self.settings.elevenlabs_api_key)

            # Get subscription info - handling potential SDK changes
            subscription = None
            try:
                subscription = client.user.get_subscription()
            except AttributeError:
                # Try alternative methods for different SDK versions
                try:
                    subscription = client.user.subscription()
                except AttributeError:
                    try:
                        # Some SDK versions might have it directly on client
                        subscription = client.get_subscription()
                    except AttributeError:
                        # Final fallback - try to make a test request to check if quota is exceeded
                        # This will give us a more definitive answer
                        pass

            if subscription is None:
                # If we can't get subscription info, try a test request to check quota
                try:
                    # Make a minimal test request to see if we get quota_exceeded
                    test_text = "test"
                    test_voice = "21m00Tcm4TlvDq8ikWAM"  # Rachel - known Hebrew voice

                    # This will fail with quota_exceeded if quota is exceeded
                    client.generate(
                        text=test_text,
                        voice=test_voice,
                        model=HEBREW_RECOMMENDED_MODEL
                    )

                    # If we get here, quota is OK
                    return CheckResult(
                        name="quota_check",
                        status="PASS",
                        message="Quota OK (verified via test request)",
                        details={"verification_method": "test_request"},
                    )

                except Exception as test_exc:
                    error_str = str(test_exc).lower()
                    if "quota_exceeded" in error_str or "quota" in error_str:
                        self.report.recommendations.append(
                            "ElevenLabs quota exceeded. Please top up your account or wait for quota reset."
                        )
                        return CheckResult(
                            name="quota_check",
                            status="FAIL",
                            message="ElevenLabs quota exceeded",
                            details={"verification_method": "test_request_failed"},
                        )
                    else:
                        # Some other error, assume quota might be OK but couldn't verify
                        return CheckResult(
                            name="quota_check",
                            status="WARNING",
                            message="Could not verify quota (test request failed)",
                            details={"verification_method": "test_request_error", "error": str(test_exc)[:100]},
                        )

            # If we got subscription info, proceed normally
            character_limit = getattr(subscription, "character_limit", 0)
            character_count = getattr(subscription, "character_count", 0)
            remaining = character_limit - character_count

            details = {
                "character_limit": character_limit,
                "characters_used": character_count,
                "characters_remaining": remaining,
                "estimated_required": estimated_characters,
                "tier": getattr(subscription, "tier", "unknown"),
            }

            if estimated_characters > 0 and remaining < estimated_characters:
                self.report.recommendations.append(
                    f"Insufficient ElevenLabs quota. Need {estimated_characters} chars, "
                    f"have {remaining}. Consider upgrading or waiting for quota reset."
                )
                return CheckResult(
                    name="quota_check",
                    status="FAIL",
                    message=f"Insufficient quota: {remaining} remaining, need ~{estimated_characters}",
                    details=details,
                )

            if remaining < 1000:
                return CheckResult(
                    name="quota_check",
                    status="WARNING",
                    message=f"Low quota: only {remaining} characters remaining",
                    details=details,
                )

            return CheckResult(
                name="quota_check",
                status="PASS",
                message=f"Quota OK: {remaining} characters remaining",
                details=details,
            )

        except ImportError:
            return CheckResult(
                name="quota_check",
                status="SKIP",
                message="ElevenLabs SDK not installed",
            )
        except Exception as exc:
            self.logger.warning("[QualityChecker] Quota check failed: %s", exc)
            # If we can't check quota, err on the side of caution for expensive operations
            return CheckResult(
                name="quota_check",
                status="FAIL",
                message="Could not verify quota - blocking TTS to prevent unexpected charges",
                details={"error": str(exc)[:100]},
            )
    
    # =========================================================================
    # POST-PROCESSING CHECKS
    # =========================================================================
    
    def run_postprocess_checks(
        self,
        run_dir: Path,
        expected_segments: int = 0,
        run_type: str = "FULL",
        expected_assets: Optional[List[Path]] = None,
    ) -> QualityReport:
        """
        Run all post-processing checks after pipeline execution.
        
        Args:
            run_dir: Path to the pipeline output directory
            expected_segments: Expected number of audio segments
            run_type: "PREVIEW" or "FULL" for reporting
            
        Returns:
            Updated QualityReport with post-processing results
        """
        self.logger.info("[QualityChecker] Running post-process checks on %s...", run_dir)
        # Preserve existing report when called after preflight; ensure run_type set
        self.report.run_type = run_type
        
        # Check 1: Audio Segments
        audio_dir = run_dir / "audio_segments"
        self.report.add_postprocess(
            self._check_audio_segments(audio_dir, expected_segments)
        )
        
        # Check 2: Final Audio
        final_audio = self._find_final_audio(run_dir)
        self.report.add_postprocess(self._check_final_audio(final_audio))
        
        # Check 2b: Preview duration safety
        if run_type.upper() == "PREVIEW":
            self.report.add_postprocess(self._check_preview_duration(final_audio))
        
        # Check 3: Final Video
        final_video = self._find_final_video(run_dir)
        self.report.add_postprocess(self._check_final_video(final_video))
        
        # Check 4: Dialogue Completeness
        dialogue_file = run_dir / "dialogue.json"
        self.report.add_postprocess(
            self._check_dialogue_completeness(dialogue_file, audio_dir)
        )
        
        # Check 5: Output Integrity
        self.report.add_postprocess(self._check_output_integrity(run_dir))

        # Check 6: Visual Content Quality
        self.report.add_postprocess(self._check_visual_content(run_dir, expected_assets=expected_assets))

        # Check 7: Visual Metadata Quality
        self.report.add_postprocess(self._check_visual_metadata_quality(run_dir))

        self.logger.info(
            "[QualityChecker] Post-process checks complete: %s",
            self.report.overall_status
        )
        return self.report
    
    def _check_audio_segments(
        self,
        audio_dir: Path,
        expected_count: int = 0,
    ) -> CheckResult:
        """Verify audio segments were generated correctly."""
        if not audio_dir.exists():
            return CheckResult(
                name="audio_segments",
                status="FAIL",
                message="Audio segments directory not found",
                details={"path": str(audio_dir)},
            )
        
        wav_files = list(audio_dir.glob("*.wav"))
        
        if not wav_files:
            return CheckResult(
                name="audio_segments",
                status="FAIL",
                message="No audio segments found",
                details={"path": str(audio_dir)},
            )
        
        total_size_kb = 0
        small_files = []
        segment_details = []
        
        for wav_file in sorted(wav_files):
            size_kb = wav_file.stat().st_size / 1024
            total_size_kb += size_kb
            
            if size_kb < self.MIN_AUDIO_SEGMENT_SIZE_KB:
                small_files.append(wav_file.name)
            
            # Try to get duration
            try:
                audio = AudioSegment.from_wav(wav_file)
                duration_sec = len(audio) / 1000
                segment_details.append({
                    "name": wav_file.name,
                    "size_kb": round(size_kb, 1),
                    "duration_sec": round(duration_sec, 2),
                })
            except Exception:
                segment_details.append({
                    "name": wav_file.name,
                    "size_kb": round(size_kb, 1),
                    "duration_sec": None,
                })
        
        details = {
            "count": len(wav_files),
            "expected_count": expected_count,
            "total_size_kb": round(total_size_kb, 1),
            "segments": segment_details[:10],  # First 10 for brevity
        }
        
        if expected_count > 0 and len(wav_files) < expected_count:
            return CheckResult(
                name="audio_segments",
                status="WARNING",
                message=f"Missing segments: found {len(wav_files)}, expected {expected_count}",
                details=details,
            )
        
        if small_files:
            self.report.recommendations.append(
                f"Small audio files detected ({len(small_files)}), possible TTS issues"
            )
            return CheckResult(
                name="audio_segments",
                status="WARNING",
                message=f"{len(small_files)} segment(s) are suspiciously small",
                details={**details, "small_files": small_files},
            )
        
        return CheckResult(
            name="audio_segments",
            status="PASS",
            message=f"{len(wav_files)} audio segments OK ({round(total_size_kb/1024, 2)} MB)",
            details=details,
        )
    
    def _check_final_audio(self, audio_path: Optional[Path]) -> CheckResult:
        """Verify final stitched audio file."""
        if not audio_path or not audio_path.exists():
            return CheckResult(
                name="final_audio",
                status="FAIL",
                message="Final audio file not found",
            )
        
        size_kb = audio_path.stat().st_size / 1024
        
        if size_kb < self.MIN_FINAL_AUDIO_SIZE_KB:
            return CheckResult(
                name="final_audio",
                status="FAIL",
                message=f"Final audio too small: {round(size_kb, 1)} KB",
                details={"path": str(audio_path), "size_kb": round(size_kb, 1)},
            )
        
        # Get audio details
        try:
            audio = AudioSegment.from_mp3(audio_path)
            duration_sec = len(audio) / 1000
            sample_rate = audio.frame_rate
            channels = audio.channels
            
            details = {
                "path": str(audio_path),
                "size_mb": round(size_kb / 1024, 2),
                "duration_sec": round(duration_sec, 2),
                "duration_min": round(duration_sec / 60, 2),
                "sample_rate": sample_rate,
                "channels": channels,
            }
            
            if sample_rate != self.EXPECTED_AUDIO_SAMPLE_RATE:
                return CheckResult(
                    name="final_audio",
                    status="WARNING",
                    message=f"Unexpected sample rate: {sample_rate}Hz",
                    details=details,
                )
            
            return CheckResult(
                name="final_audio",
                status="PASS",
                message=f"Final audio OK: {round(duration_sec/60, 1)} min, {round(size_kb/1024, 2)} MB",
                details=details,
            )
            
        except Exception as exc:
            return CheckResult(
                name="final_audio",
                status="WARNING",
                message=f"Could not analyze audio: {str(exc)[:100]}",
                details={"path": str(audio_path), "size_kb": round(size_kb, 1)},
            )
    
    def _check_preview_duration(self, audio_path: Optional[Path]) -> CheckResult:
        """Ensure preview runs stay short to save credits."""
        if not audio_path or not audio_path.exists():
            return CheckResult(
                name="preview_duration",
                status="SKIP",
                message="Preview duration check skipped: final audio missing",
            )
        
        try:
            audio = AudioSegment.from_mp3(audio_path)
            duration_sec = len(audio) / 1000
            details = {
                "path": str(audio_path),
                "duration_sec": round(duration_sec, 2),
                "duration_min": round(duration_sec / 60, 2),
            }
            if duration_sec > 60:
                return CheckResult(
                    name="preview_duration",
                    status="WARNING",
                    message=f"Preview output exceeds 60s ({round(duration_sec/60, 2)} min)",
                    details=details,
                )
            return CheckResult(
                name="preview_duration",
                status="PASS",
                message=f"Preview duration OK: {round(duration_sec, 1)}s",
                details=details,
            )
        except Exception as exc:
            return CheckResult(
                name="preview_duration",
                status="WARNING",
                message=f"Could not verify preview duration: {str(exc)[:100]}",
                details={"path": str(audio_path)},
            )
    
    def _check_final_video(self, video_path: Optional[Path]) -> CheckResult:
        """Verify final video file."""
        if not video_path or not video_path.exists():
            return CheckResult(
                name="final_video",
                status="SKIP",
                message="Final video not found (may have been skipped)",
            )
        
        size_kb = video_path.stat().st_size / 1024
        threshold_kb = 200 if self.report.run_type.upper() == "PREVIEW" else 1000
        if size_kb < threshold_kb:
            return CheckResult(
                name="final_video",
                status="FAIL",
                message=f"Video file too small for {self.report.run_type}: {round(size_kb, 1)} KB (min {threshold_kb} KB)",
                details={"path": str(video_path), "size_kb": round(size_kb, 1), "threshold_kb": threshold_kb},
            )
        
        # Try to get video details using moviepy
        try:
            from moviepy.editor import VideoFileClip
            
            clip = VideoFileClip(str(video_path))
            
            details = {
                "path": str(video_path),
                "size_mb": round(size_kb / 1024, 2),
                "duration_sec": round(clip.duration, 2),
                "width": clip.w,
                "height": clip.h,
                "fps": clip.fps,
                "has_audio": clip.audio is not None,
            }
            
            clip.close()
            
            # Check resolution
            if clip.w != self.EXPECTED_VIDEO_WIDTH or clip.h != self.EXPECTED_VIDEO_HEIGHT:
                return CheckResult(
                    name="final_video",
                    status="WARNING",
                    message=f"Unexpected resolution: {clip.w}x{clip.h}",
                    details=details,
                )
            
            # Check audio track
            if not details["has_audio"]:
                self.report.recommendations.append(
                    "Video is missing audio track! Check video composition."
                )
                return CheckResult(
                    name="final_video",
                    status="FAIL",
                    message="Video has no audio track",
                    details=details,
                )
            
            return CheckResult(
                name="final_video",
                status="PASS",
                message=f"Video OK: {clip.w}x{clip.h}, {round(clip.duration/60, 1)} min",
                details=details,
            )
            
        except Exception as exc:
            self.logger.warning("[QualityChecker] Video analysis failed: %s", exc)
            return CheckResult(
                name="final_video",
                status="WARNING",
                message=f"Could not analyze video: {str(exc)[:100]}",
                details={"path": str(video_path), "size_mb": round(size_kb/1024, 2)},
            )
    
    def _check_dialogue_completeness(
        self,
        dialogue_path: Path,
        audio_dir: Path,
    ) -> CheckResult:
        """Verify all dialogue entries have corresponding audio."""
        if not dialogue_path.exists():
            return CheckResult(
                name="dialogue_completeness",
                status="FAIL",
                message="Dialogue file not found",
            )
        
        try:
            dialogue = json.loads(dialogue_path.read_text(encoding="utf-8"))
            entries = dialogue.get("dialogue", [])
            
            if not entries:
                return CheckResult(
                    name="dialogue_completeness",
                    status="FAIL",
                    message="Dialogue file is empty",
                )
            
            # Count speakers
            speaker_counts = {}
            for entry in entries:
                speaker = entry.get("speaker", "Unknown")
                speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
            
            # Check audio files exist
            expected_count = len(entries)
            actual_files = list(audio_dir.glob("*.wav")) if audio_dir.exists() else []
            actual_count = len(actual_files)
            
            details = {
                "total_entries": expected_count,
                "audio_files_found": actual_count,
                "speakers": speaker_counts,
            }
            
            if actual_count < expected_count:
                missing = expected_count - actual_count
                return CheckResult(
                    name="dialogue_completeness",
                    status="WARNING",
                    message=f"Missing {missing} audio file(s) for dialogue entries",
                    details=details,
                )
            
            return CheckResult(
                name="dialogue_completeness",
                status="PASS",
                message=f"All {expected_count} dialogue entries have audio",
                details=details,
            )
            
        except Exception as exc:
            return CheckResult(
                name="dialogue_completeness",
                status="WARNING",
                message=f"Could not verify dialogue: {str(exc)[:100]}",
            )
    
    def _check_output_integrity(self, run_dir: Path) -> CheckResult:
        """Check overall output directory structure."""
        required_files = [
            "dialogue.json",
            "processing_log.txt",
        ]
        
        optional_files = [
            "metadata.json",
            "story.json",
            "story.md",
        ]
        
        missing_required = []
        found_optional = []
        
        for filename in required_files:
            if not (run_dir / filename).exists():
                missing_required.append(filename)
        
        for filename in optional_files:
            if (run_dir / filename).exists():
                found_optional.append(filename)
        
        # Count visual assets
        visuals_dir = run_dir / "visuals"
        visual_count = 0
        if visuals_dir.exists():
            visual_count = len(list(visuals_dir.glob("*")))
        
        details = {
            "run_dir": str(run_dir),
            "missing_required": missing_required,
            "found_optional": found_optional,
            "visual_assets": visual_count,
        }
        
        if missing_required:
            return CheckResult(
                name="output_integrity",
                status="FAIL",
                message=f"Missing required files: {', '.join(missing_required)}",
                details=details,
            )
        
        return CheckResult(
            name="output_integrity",
            status="PASS",
            message=f"Output structure OK ({visual_count} visual assets)",
            details=details,
        )

    def _check_visual_content(self, run_dir: Path, metadata: Optional[Dict] = None, expected_assets: Optional[List[Path]] = None) -> CheckResult:
        """Comprehensive visual content validation scoped to current run assets."""
        visuals_dir = run_dir / "visuals"
        issues: List[str] = []
        details: Dict[str, Any] = {}

        # Use expected assets if provided to avoid legacy/previous-run noise
        if expected_assets is not None:
            image_files = [p for p in expected_assets if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
            video_files = [p for p in expected_assets if p.suffix.lower() in {".mp4", ".webm", ".mov"}]
        else:
            if not visuals_dir.exists():
                return CheckResult(
                    name="visual_content",
                    status="WARNING",
                    message="Visuals directory not found - no visual assets generated",
                    details={"visuals_dir_exists": False}
                )
            image_files = list(visuals_dir.glob("*.png")) + list(visuals_dir.glob("*.jpg")) + list(visuals_dir.glob("*.jpeg"))
            video_files = list(visuals_dir.glob("*.mp4")) + list(visuals_dir.glob("*.webm")) + list(visuals_dir.glob("*.mov"))

        details.update({
            "images_count": len(image_files),
            "videos_count": len(video_files),
            "total_visuals": len(image_files) + len(video_files)
        })
        # Track requested visuals from settings for sanity comparison
        try:
            requested_images = int(getattr(self.settings, "image_count", 0) or 0)
        except Exception:
            requested_images = 0
        details["requested_images"] = requested_images

        # Check for visual metadata
        visual_metadata_path = run_dir / "visual_metadata.json"
        has_visual_metadata = visual_metadata_path.exists()
        details["has_visual_metadata"] = has_visual_metadata

        if not has_visual_metadata:
            issues.append("No visual_metadata.json found - using generic AI generation")

        image_issues, image_details = self._check_image_quality(image_files)
        if image_issues:
            issues.extend(image_issues)
        details.update(image_details)

        # Explicitly flag missing visuals when images were requested
        if requested_images > 0 and len(image_files) == 0:
            issues.append(f"Requested {requested_images} image(s) but 0 were generated")

        # Check video file
        final_video = run_dir / "lecture_2025-12-03_summary.mp4"  # Generic name check
        if not final_video.exists():
            # Try to find any mp4 file
            mp4_files = list(run_dir.glob("*.mp4"))
            if mp4_files:
                final_video = mp4_files[0]
            else:
                issues.append("No final video file found")

        if final_video.exists():
            video_issues, video_details = self._analyze_video_file(final_video)
            issues.extend(video_issues)
            details.update(video_details)

        # Determine overall status
        if issues:
            status = "FAIL" if any("corrupted" in issue or "cannot" in issue.lower() for issue in issues) else "WARNING"
            message = f"Visual content issues: {len(issues)} problem(s) found"
        else:
            status = "PASS"
            message = f"Visual content OK: {len(image_files)} images, {len(video_files)} videos"

        return CheckResult(
            name="visual_content",
            status=status,
            message=message,
            details={**details, "issues": issues}
        )

    def _check_visual_metadata_quality(self, run_dir: Path) -> CheckResult:
        """Validate visual metadata quality and completeness."""
        visual_metadata_path = run_dir / "visual_metadata.json"

        if not visual_metadata_path.exists():
            return CheckResult(
                name="visual_metadata_quality",
                status="WARNING",
                message="No visual_metadata.json found - using generic prompts",
                details={"recommendation": "Create visual_metadata.json with custom prompts, styles, and colors for better results"}
            )

        try:
            with open(visual_metadata_path, 'r', encoding='utf-8') as f:
                visual_metadata = json.load(f)

            issues = []
            details = {}

            # Check images array
            images = visual_metadata.get("images", [])
            details["images_count"] = len(images)

            if not images:
                issues.append("No images defined in visual metadata")

            # Validate each image entry
            missing_prompts = 0
            missing_styles = 0
            short_prompts = 0
            for i, img in enumerate(images):
                if not img.get("prompt"):
                    missing_prompts += 1
                else:
                    prompt_len = len(str(img.get("prompt", "")).strip())
                    if prompt_len < 10:
                        short_prompts += 1
                if not img.get("style"):
                    missing_styles += 1

            if missing_prompts > 0:
                issues.append(f"{missing_prompts} images missing prompts")
            if missing_styles > 0:
                issues.append(f"{missing_styles} images missing style information")
            if short_prompts > 0:
                issues.append(f"{short_prompts} prompt(s) look too short for quality visuals (<10 chars)")

            # Check for Hebrew content in prompts
            hebrew_images = 0
            for img in images:
                prompt = img.get("prompt", "")
                if any(ord(c) > 127 for c in prompt):  # Contains non-ASCII (likely Hebrew)
                    hebrew_images += 1

            details["hebrew_content_ratio"] = hebrew_images / len(images) if images else 0

            # Check video metadata
            video_meta = visual_metadata.get("video", {})
            has_video_prompt = bool(video_meta.get("prompt"))
            details["has_video_metadata"] = has_video_prompt

            # Check general notes
            general_notes = visual_metadata.get("general_notes", {})
            has_aesthetics = bool(general_notes.get("aesthetics"))
            details["has_aesthetics_guide"] = has_aesthetics

            if not has_aesthetics:
                issues.append("Missing aesthetics guide in general_notes")

            # Overall assessment
            if issues:
                status = "WARNING"
                message = f"Visual metadata quality issues: {len(issues)} concern(s)"
            else:
                status = "PASS"
                message = f"Visual metadata quality OK: {len(images)} images with complete prompts"

            return CheckResult(
                name="visual_metadata_quality",
                status=status,
                message=message,
                details={**details, "issues": issues}
            )

        except Exception as e:
            return CheckResult(
                name="visual_metadata_quality",
                status="FAIL",
                message=f"Cannot parse visual_metadata.json: {str(e)[:100]}",
            )

    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _find_final_audio(self, run_dir: Path) -> Optional[Path]:
        """Find the final stitched audio file."""
        patterns = ["lecture_*.mp3", "final_*.mp3", "*.mp3"]
        for pattern in patterns:
            files = list(run_dir.glob(pattern))
            if files:
                return sorted(files)[-1]  # Return most recent
        return None
    
    def _find_final_video(self, run_dir: Path) -> Optional[Path]:
        """Find the final video file."""
        patterns = ["lecture_*.mp4", "final_*.mp4", "*.mp4"]
        for pattern in patterns:
            files = list(run_dir.glob(pattern))
            if files:
                return sorted(files)[-1]  # Return most recent
        return None

    def _load_cv2(self):
        try:
            import cv2  # type: ignore
        except ImportError as exc:
            message = "OpenCV (cv2) is required for quality validation. Install opencv-python."
            self.logger.critical(message)
            raise RuntimeError(message) from exc
        return cv2

    def _check_image_quality(self, image_files: List[Path]) -> Tuple[List[str], Dict[str, Any]]:
        """Validate generated images for size, resolution, and readability."""
        issues: List[str] = []
        details: Dict[str, Any] = {
            "corrupted_images": [],
            "small_images": [],
            "images_analyzed": len(image_files),
        }

        if not image_files:
            return issues, details

        cv2 = self._load_cv2()
        from PIL import Image  # Import lazily to keep startup fast

        for img_path in image_files:
            try:
                size_kb = img_path.stat().st_size / 1024
                if size_kb < 100:
                    details["corrupted_images"].append(f"{img_path.name} ({size_kb:.1f}KB)")

                if cv2 is not None:
                    try:
                        stream = np.fromfile(str(img_path), dtype=np.uint8)
                        img = cv2.imdecode(stream, cv2.IMREAD_COLOR)
                    except Exception as exc:
                        details["corrupted_images"].append(
                            f"{img_path.name} (cv2 read error: {str(exc)[:40]})"
                        )
                        continue

                    if img is None:
                        details["corrupted_images"].append(f"{img_path.name} (cv2 read failure)")
                        continue
                    height, width = img.shape[:2]
                else:
                    with Image.open(img_path) as pil_img:
                        width, height = pil_img.size

                if width < 800 or height < 600:
                    details["small_images"].append(f"{img_path.name} ({width}x{height})")
            except Exception as exc:
                details["corrupted_images"].append(f"{img_path.name} (error: {str(exc)[:40]})")

        if details["corrupted_images"]:
            issues.append(
                f"Problematic images detected: {', '.join(details['corrupted_images'][:3])}"
            )
        if details["small_images"]:
            issues.append(f"Low-resolution images: {', '.join(details['small_images'][:3])}")

        return issues, details

    def _analyze_video_file(self, video_path: Path) -> Tuple[List[str], Dict[str, Any]]:
        """Inspect the final video for codec, duration, and visual artifacts."""
        issues: List[str] = []
        details: Dict[str, Any] = {}

        if not video_path.exists():
            issues.append("Final video file not found")
            return issues, details

        size_mb = video_path.stat().st_size / (1024 * 1024)
        details["video_size_mb"] = round(size_mb, 2)
        if size_mb < 1:
            issues.append(f"Video too small: {size_mb:.2f}MB (expected >1MB)")

        cap = None
        width = height = frame_count = 0
        fps = 0.0
        duration = 0.0
        used_moviepy_fallback = False

        try:
            cv2 = self._load_cv2()
        except Exception as exc:
            issues.append("cv2 unavailable for video validation")
            self.logger.warning("cv2 unavailable, will rely on moviepy fallback: %s", exc)
            cv2 = None

        if cv2 is not None:
            try:
                cap = cv2.VideoCapture(str(video_path))
            except Exception as exc:
                self.logger.warning("cv2.VideoCapture failed, using moviepy fallback: %s", exc)
                cap = None

            if cap is not None and cap.isOpened():
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS) or 0
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                duration = frame_count / fps if fps > 0 else 0

                details.update(
                    {
                        "video_width": width,
                        "video_height": height,
                        "video_fps": round(fps, 1) if fps else 0,
                        "video_duration_sec": round(duration, 1),
                        "video_frames": frame_count,
                    }
                )

                if duration < 30:
                    issues.append(f"Video too short: {duration:.1f}s (expected >30s)")
                if width < self.EXPECTED_VIDEO_WIDTH or height < self.EXPECTED_VIDEO_HEIGHT:
                    issues.append(
                        f"Video resolution low: {width}x{height} (expected {self.EXPECTED_VIDEO_WIDTH}x{self.EXPECTED_VIDEO_HEIGHT})"
                    )

                sample_stride = max(frame_count // 20, 1) if frame_count else 1
                blank_frames = 0
                green_frames = 0

                for idx in range(0, frame_count, sample_stride):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                    success, frame = cap.read()
                    if not success:
                        continue
                    mean_val = frame.mean()
                    if mean_val < 10:
                        blank_frames += 1
                    b_channel, g_channel, r_channel = cv2.split(frame)
                    if (
                        g_channel.mean() > 200
                        and r_channel.mean() < 100
                        and b_channel.mean() < 100
                    ):
                        green_frames += 1

                if blank_frames > 4:
                    issues.append(f"Detected {blank_frames} blank frame(s) in video")
                if green_frames > 2:
                    issues.append(f"Detected {green_frames} potential green-screen frame(s)")
            else:
                issues.append("cv2 could not open video; using moviepy fallback")
                used_moviepy_fallback = True

            if cap is not None:
                cap.release()
        else:
            used_moviepy_fallback = True

        if used_moviepy_fallback:
            details.setdefault("fallbacks", []).append("moviepy_video_probe")

        try:
            from moviepy.editor import VideoFileClip

            clip = VideoFileClip(str(video_path))
            moviepy_duration = clip.duration or 0

            if used_moviepy_fallback or not width or not height:
                width, height = clip.w, clip.h
                fps = clip.fps or fps
                duration = moviepy_duration
                details.update(
                    {
                        "video_width": width,
                        "video_height": height,
                        "video_fps": round(fps, 1) if fps else 0,
                        "video_duration_sec": round(duration, 1),
                    }
                )

            if clip.audio is None:
                issues.append("Video has no audio track")
            else:
                audio_duration = clip.audio.duration or 0
                if abs(audio_duration - duration) > 1:
                    issues.append(
                        f"Audio/video desync: video={duration:.1f}s, audio={audio_duration:.1f}s"
                    )
            clip.close()
        except Exception as exc:
            details["audio_validation_warning"] = f"Audio check skipped: {str(exc)[:60]}"

        return issues, details
    
    def get_summary(self) -> str:
        """Get a human-readable summary of the quality report."""
        lines = [
            "=" * 50,
            "    QUALITY VERIFICATION REPORT",
            "=" * 50,
            "",
            f"Overall Status: {self.report.overall_status}",
            f"Timestamp: {self.report.timestamp}",
            "",
        ]
        
        if self.report.preflight_checks:
            lines.append("PRE-FLIGHT CHECKS:")
            for check in self.report.preflight_checks:
                icon = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️", "SKIP": "⏭️"}.get(
                    check.status, "?"
                )
                lines.append(f"  {icon} {check.name}: {check.message}")
            lines.append("")
        
        if self.report.postprocess_checks:
            lines.append("POST-PROCESSING CHECKS:")
            for check in self.report.postprocess_checks:
                icon = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️", "SKIP": "⏭️"}.get(
                    check.status, "?"
                )
                lines.append(f"  {icon} {check.name}: {check.message}")
            lines.append("")
        
        if self.report.recommendations:
            lines.append("RECOMMENDATIONS:")
            for rec in self.report.recommendations:
                lines.append(f"  • {rec}")
            lines.append("")
        
        lines.append("=" * 50)
        return "\n".join(lines)


def check_elevenlabs_quota_quick(api_key: str) -> Tuple[bool, int, int, str]:
    """
    Quick quota check utility function.
    
    Args:
        api_key: ElevenLabs API key
        
    Returns:
        Tuple of (has_quota, remaining, limit, tier)
    """
    try:
        from elevenlabs.client import ElevenLabs
        
        client = ElevenLabs(api_key=api_key)
        try:
            subscription = client.user.get_subscription()
        except AttributeError:
            subscription = client.user.subscription()
        
        character_limit = getattr(subscription, "character_limit", 0)
        character_count = getattr(subscription, "character_count", 0)
        remaining = character_limit - character_count
        
        return (
            remaining > 100,
            remaining,
            character_limit,
            getattr(subscription, "tier", "unknown"),
        )
    except Exception as exc:
        return (False, 0, 0, f"Error: {exc}")


def validate_voice_hebrew_support(voice_id: str) -> bool:
    """Check if a voice ID supports Hebrew."""
    return voice_id in HEBREW_SUPPORTED_VOICES

