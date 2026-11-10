"""
M.B.S Studio - ElevenLabs TTS Synthesizer
==========================================

Text-to-speech synthesis using ElevenLabs API.
Provides natural Hebrew voices as an alternative to Azure Neural TTS.

Author: M.B.S Studio
Version: 1.0.0
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
import re

from pydub import AudioSegment, effects

try:
    from elevenlabs.client import ElevenLabs
    from elevenlabs.core.api_error import ApiError
except ImportError:  # pragma: no cover - handled at runtime
    ElevenLabs = None
    ApiError = Exception

from ..utils import (
    CostTracker,
    RunPaths,
    Settings,
    VoiceProfileManager,
    detect_language,
    get_logger,
)


class ElevenLabsQuotaExceededError(RuntimeError):
    """Raised when ElevenLabs reports that the account quota has been exhausted."""


class ElevenLabsSpeechSynthesizer:
    """Convert dialogue JSON to individual speaker audio segments using ElevenLabs API."""

    # Default ElevenLabs voice IDs for Hebrew speakers
    # These are multilingual voices that support Hebrew (IL) and English (US)
    # IMPORTANT: Only voices flagged with `hebrew_support=True` should be used for Hebrew narration
    VOICE_MAP = {
        "Roee": {
            "voice_id": "pNInz6obpgDQGcFmaJgB",  # Adam - deep male voice
            "model": "eleven_v3",
            "hebrew_support": True,
        },
        "Noa": {
            "voice_id": "21m00Tcm4TlvDq8ikWAM",  # Rachel - warm female voice
            "model": "eleven_v3",
            "hebrew_support": True,
        },
    }

    # Reference: vetted ElevenLabs voices for Hebrew content
    # ✅ Hebrew + English (recommended):
    #   - Rachel (21m00Tcm4TlvDq8ikWAM) – female, warm and reliable
    #   - Antoni (ErXwobaYiN019PkySvjV) – male, young and friendly
    #   - Domi (AZnzlk1XvdvUeBnXmlld) – female, energetic
    #   - Bella (EXAVITQu4vr4xnSDxMaL) – female, soft narration
    #   - Adam (pNInz6obpgDQGcFmaJgB) – male, deep and natural
    #
    # ❌ English-only (avoid for Hebrew):
    #   - Elli (MF3mGyEYCI7XYWbV9V60)
    #   - Josh (TxGEqnHWrfWFTfGW9XjX)
    #   - Arnold (VR6AewLTigWG4xSOukaG)
    #
    # Best practices for Hebrew synthesis with ElevenLabs:
    #   • Use eleven_v3 (single lines) or eleven_v4 (Text to Dialogue); older models have no Hebrew
    #   • Split long paragraphs into <800 characters per request
    #   • Apply SSML phoneme tags for tricky pronunciations
    #   • Keep `stability≈0.5`, `similarity_boost≈0.75`, `use_speaker_boost=True`

    # Voice settings for natural Hebrew speech
    DEFAULT_VOICE_SETTINGS = {
        "stability": 0.5,
        "similarity_boost": 0.75,
        "style": 0.0,
        "use_speaker_boost": True,
    }

    # Per ElevenLabs' model docs only the v3/v4 generation reads Hebrew; Multilingual v2,
    # Flash v2.5 and Turbo v2.5 do not list it. eleven_v4 is served only by the Text to
    # Dialogue API (see dialogue_tts.py), so single-line synthesis uses eleven_v3.
    HEBREW_SAFE_MODELS = {"eleven_v3"}
    HEBREW_PREFERRED_MODEL = "eleven_v3"

    def __init__(
        self,
        settings: Settings,
        cost_tracker: Optional[CostTracker] = None,
        voice_profile: Optional[str] = None,
        voice_manager: Optional[VoiceProfileManager] = None,
    ) -> None:
        """
        Initialize the ElevenLabs speech synthesizer.

        Args:
            settings: Application settings containing ElevenLabs API key
            cost_tracker: Optional cost tracker for monitoring usage
            voice_profile: Name of the voice profile to use
            voice_manager: Voice profile manager instance
        """
        self.settings = settings
        self.logger = get_logger(self.__class__.__name__)
        self.cost_tracker = cost_tracker or CostTracker()
        self.voice_manager = voice_manager or VoiceProfileManager(
            settings.voice_profile_path, settings.default_voice_profile
        )
        self.voice_profile_name = voice_profile or settings.default_voice_profile
        self.voice_profile = self.voice_manager.get(self.voice_profile_name)
        self.voice_overrides: Dict[str, Dict[str, str]] = getattr(
            self.settings, "elevenlabs_voice_overrides", {}
        ) or {}

        # Log voice overrides status
        if self.voice_overrides:
            self.logger.info("ElevenLabs voice overrides loaded: %s", 
                           {k: v.get('voice_id', 'N/A')[:8] for k, v in self.voice_overrides.items()})
        else:
            self.logger.info("No ElevenLabs voice overrides configured, using profile defaults")
        
        # Validate API key
        if not settings.elevenlabs_api_key:
            raise ValueError(
                "ElevenLabs API key not configured. "
                "Set ELEVENLABS_API_KEY environment variable."
            )
        
        # Initialize ElevenLabs client
        if ElevenLabs is None:
            raise ImportError(
                "ElevenLabs SDK not installed. "
                "Install with: pip install elevenlabs"
            )

        self.client = ElevenLabs(api_key=settings.elevenlabs_api_key)

    @staticmethod
    def normalize_hebrew_text(text: str) -> str:
        """
        Wrap Latin tokens to preserve Hebrew reading order for ElevenLabs.

        - Detect Hebrew presence; if none, return original text.
        - Wrap any token containing Latin letters/digits with LTR isolate so the
          multilingual model keeps Hebrew flow intact (helps with CCNA terms).
        """
        if not text:
            return text

        contains_hebrew = any("\u0590" <= c <= "\u05FF" for c in text)
        if not contains_hebrew:
            return text

        tokens = re.split(r"(\s+)", text)
        normalized: List[str] = []
        for tok in tokens:
            if not tok or tok.isspace():
                normalized.append(tok)
                continue
            if re.search(r"[A-Za-z0-9]", tok):
                # LTR isolate markers keep Latin tokens embedded without reversing
                normalized.append(f"\u2066{tok}\u2069")
            else:
                normalized.append(tok)
        return "".join(normalized)

    def synthesize(
        self, dialogue_path: Path, run_paths: RunPaths, force: bool = False
    ) -> List[Dict[str, object]]:
        """
        Synthesize all dialogue entries to audio files.

        Args:
            dialogue_path: Path to dialogue JSON file
            run_paths: Run paths for output directories
            force: Whether to overwrite existing files

        Returns:
            List of dicts with generated audio segments:
            {"path": Path, "duration": float}
        """
        run_paths.log("Starting speech synthesis (ElevenLabs).")
        self.logger.info("[ElevenLabsTTS.synthesize] ========== TTS SYNTHESIS START ==========")
        
        data = json.loads(dialogue_path.read_text(encoding="utf-8"))
        segments: List[Dict[str, object]] = []
        
        # Track statistics for logging
        total_characters = 0
        speaker_stats: Dict[str, Dict[str, object]] = {}
        
        # Log voice overrides at start
        if self.voice_overrides:
            self.logger.info("[ElevenLabsTTS.synthesize] Voice Overrides Active:")
            for speaker, config in self.voice_overrides.items():
                voice_id = config.get('voice_id', 'N/A')
                self.logger.info("[ElevenLabsTTS.synthesize]   %s -> %s", speaker, voice_id[:16] + "...")
            run_paths.log(f"ElevenLabs voice overrides: {list(self.voice_overrides.keys())}")
        else:
            self.logger.info("[ElevenLabsTTS.synthesize] No voice overrides - using profile defaults")
            run_paths.log("ElevenLabs using profile default voices")
        
        for idx, entry in enumerate(data["dialogue"], start=1):
            speaker = entry["speaker"]
            text = entry["text"]
            target = run_paths.audio_dir / f"{idx:04d}_{speaker.lower()}.wav"
            
            if target.exists() and not force:
                self.logger.debug("Skipping existing segment: %s", target.name)
                segments.append({"path": target, "duration": self._probe_duration(target)})
                continue
            
            # Get voice config for logging
            voice_config = self._get_voice_config(speaker, text)
            voice_id = voice_config["voice_id"]
            
            # Track stats
            total_characters += len(text)
            if speaker not in speaker_stats:
                speaker_stats[speaker] = {
                    "voice_id": voice_id,
                    "segments": 0,
                    "characters": 0
                }
            speaker_stats[speaker]["segments"] += 1
            speaker_stats[speaker]["characters"] += len(text)
            
            self._synthesize_entry(entry, target)
            segments.append({"path": target, "duration": self._probe_duration(target)})
        
        # Log synthesis summary
        self.logger.info("[ElevenLabsTTS.synthesize] ========== TTS SYNTHESIS SUMMARY ==========")
        self.logger.info("[ElevenLabsTTS.synthesize] Total segments: %d", len(segments))
        self.logger.info("[ElevenLabsTTS.synthesize] Total characters: %d", total_characters)
        
        for speaker, stats in speaker_stats.items():
            self.logger.info(
                "[ElevenLabsTTS.synthesize] Speaker %s: voice=%s, segments=%d, chars=%d",
                speaker, stats["voice_id"][:16] + "...", stats["segments"], stats["characters"]
            )
            run_paths.log(f"ElevenLabs {speaker}: {stats['segments']} segments, {stats['characters']} chars, voice={stats['voice_id'][:12]}...")
        
        run_paths.log(f"Speech synthesis completed (ElevenLabs): {len(segments)} segments, {total_characters} characters.")
        return segments

    def _synthesize_entry(self, entry: Dict[str, str], target: Path) -> None:
        """
        Synthesize a single dialogue entry.

        Args:
            entry: Dialogue entry with 'speaker' and 'text' keys
            target: Output file path
        """
        speaker = entry["speaker"]
        text = entry["text"]

        # Get voice configuration
        voice_config = self._get_voice_config(speaker, text)
        voice_id = voice_config["voice_id"]
        hebrew_chars = sum(1 for c in text if "\u0590" <= c <= "\u05FF")
        latin_chars = sum(1 for c in text if ("A" <= c <= "Z") or ("a" <= c <= "z"))
        is_hebrew = hebrew_chars > 0 or detect_language(text) == "he"

        normalized_text = self.normalize_hebrew_text(text) if is_hebrew else text

        # Hebrew needs a model that reads it; keep a configured one only if it does
        requested_model = voice_config.get("model", self.HEBREW_PREFERRED_MODEL)
        if is_hebrew:
            model_id = requested_model if requested_model in self.HEBREW_SAFE_MODELS else self.HEBREW_PREFERRED_MODEL
        else:
            model_id = requested_model

        self.logger.debug(
            "Synthesizing for %s using voice %s, model %s (hebrew=%s, he_chars=%d, en_chars=%d)",
            speaker,
            voice_id,
            model_id,
            is_hebrew,
            hebrew_chars,
            latin_chars,
        )

        try:
            # Get voice settings
            voice_settings = self._get_voice_settings(voice_config)
            
            def _convert(current_model: str):
                """Call ElevenLabs convert with the given model."""
                convert_kwargs = {
                    "voice_id": voice_id,
                    "text": normalized_text,
                    "model_id": current_model,
                    "voice_settings": {
                        "stability": voice_settings["stability"],
                        "similarity_boost": voice_settings["similarity_boost"],
                        "style": voice_settings.get("style", 0.0),
                        "use_speaker_boost": voice_settings.get("use_speaker_boost", True),
                    },
                }
                return self.client.text_to_speech.convert(**convert_kwargs)

            try:
                audio_generator = _convert(model_id)
            except ApiError as exc:
                # Graceful fallback for language/model mismatch
                if self._is_quota_error(exc):
                    raise ElevenLabsQuotaExceededError("ElevenLabs quota exceeded") from exc
                detail = getattr(exc, "body", {}) or {}
                detail_info = detail.get("detail", {})
                status = detail_info.get("status", "").lower()
                message = str(detail_info.get("message", "")).lower()
                if "unsupported_language" in status or "unsupported language" in message:
                    fallback_model = self.HEBREW_PREFERRED_MODEL
                    # A voice override may name an older model that has no Hebrew; retry on v3
                    if model_id != fallback_model:
                        self.logger.warning(
                            "ElevenLabs reported unsupported_language for model '%s'. Retrying with '%s'.",
                            model_id,
                            fallback_model,
                        )
                        audio_generator = _convert(fallback_model)
                        model_id = fallback_model
                    else:
                        raise
                else:
                    raise
            
            # Collect audio bytes from generator
            audio_bytes = b"".join(audio_generator)
            
            # Save to temporary MP3 file (ElevenLabs returns MP3)
            temp_mp3 = target.with_suffix(".mp3")
            temp_mp3.write_bytes(audio_bytes)
            
            # Convert to WAV with quality preservation
            segment = AudioSegment.from_mp3(temp_mp3)
            
            # Normalize with headroom to preserve clarity (especially for Hebrew)
            # Use target_dBFS=-3.0 instead of default -1.0 to avoid over-compression
            segment = effects.normalize(segment, headroom=0.5)
            
            # Ensure consistent sample rate (44100 Hz for quality)
            if segment.frame_rate != 44100:
                segment = segment.set_frame_rate(44100)
            
            # Add silence padding at the end
            silence = AudioSegment.silent(duration=500, frame_rate=44100)
            (segment + silence).export(target, format="wav", parameters=["-ar", "44100"])
            
            # Clean up temp file
            temp_mp3.unlink()
            
            # Track costs
            self.cost_tracker.add_elevenlabs_characters(len(text))
            self.logger.debug("Rendered segment %s for %s", target.name, speaker)
            
        except ElevenLabsQuotaExceededError:
            raise
        except ApiError as exc:
            self.logger.error("ElevenLabs API error for %s: %s", speaker, exc)
            raise RuntimeError(f"ElevenLabs TTS failed for {speaker}: {exc}") from exc
        except Exception as exc:
            self.logger.error("ElevenLabs TTS failed for %s: %s", speaker, exc)
            raise RuntimeError(f"ElevenLabs TTS failed for {speaker}: {exc}")

    def _get_voice_config(self, speaker: str, text: str) -> Dict[str, str]:
        """
        Get voice configuration for a speaker.

        Args:
            speaker: Speaker name (e.g., 'Roee', 'Noa')
            text: Text to synthesize (for language detection)

        Returns:
            Dict with voice_id and model keys
        """
        language = detect_language(text)

        override = self.voice_overrides.get(speaker)
        if override:
            voice_id = override.get("voice_id")
            if voice_id:
                model = override.get("model") or self.HEBREW_PREFERRED_MODEL
                self.logger.info("Using ElevenLabs override for %s (%s)", speaker, voice_id[:8])
                result = {
                    "voice_id": voice_id,
                    "model": model,
                    **({"settings": override.get("settings")} if override.get("settings") else {}),
                }
                return result
            self.logger.warning(
                "Invalid ElevenLabs override for %s (missing voice_id). Falling back to profile.", speaker
            )
        
        # Voices chosen in the settings screen (ELEVENLABS_VOICE_ROEE / _NOA)
        configured = getattr(self.settings, f"elevenlabs_voice_{speaker.lower()}", "")
        if isinstance(configured, str) and configured.strip():
            return {"voice_id": configured, "model": self.HEBREW_PREFERRED_MODEL}

        # Then the voice profile
        profile_voice = self.voice_profile.get_elevenlabs_voice(speaker, language)
        if profile_voice:
            return profile_voice
        
        # Fall back to default voice map
        return self.VOICE_MAP.get(speaker, self.VOICE_MAP["Roee"])

    @staticmethod
    def _is_quota_error(exc: ApiError) -> bool:
        """Check whether an ElevenLabs ApiError corresponds to quota exhaustion."""
        if not isinstance(exc, ApiError):
            return False
        detail = getattr(exc, "body", {}) or {}
        detail_info = detail.get("detail", {})
        status = detail_info.get("status")
        message = str(detail_info.get("message", "")).lower()
        return status == "quota_exceeded" or "quota" in message


    def _get_voice_settings(self, voice_config: Dict) -> Dict:
        """
        Get voice settings for synthesis.

        Args:
            voice_config: Voice configuration dict

        Returns:
            Voice settings dict for ElevenLabs API
        """
        settings = self.DEFAULT_VOICE_SETTINGS.copy()
        
        # Allow per-voice overrides from config
        if "settings" in voice_config:
            settings.update(voice_config["settings"])
        
        return settings

    @staticmethod
    def _probe_duration(path: Path) -> float:
        """Best-effort duration reader for a rendered segment."""
        try:
            return float(AudioSegment.from_file(path).duration_seconds)
        except Exception:
            return 0.0

    def list_available_voices(self) -> List[Dict]:
        """
        List available voices from ElevenLabs account.

        Returns:
            List of voice dictionaries with id, name, and labels
        """
        try:
            response = self.client.voices.get_all()
            voices = response.voices if hasattr(response, 'voices') else response
            return [
                {
                    "voice_id": v.voice_id,
                    "name": v.name,
                    "labels": getattr(v, 'labels', {}),
                    "preview_url": getattr(v, 'preview_url', ''),
                }
                for v in voices
            ]
        except Exception as exc:
            self.logger.error("Failed to list ElevenLabs voices: %s", exc)
            return []