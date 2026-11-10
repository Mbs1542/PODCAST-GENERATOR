"""
Tests for ElevenLabs voice override handling.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _mock_settings(overrides):
    from src.utils import Settings

    settings = MagicMock(spec=Settings)
    settings.elevenlabs_api_key = "test-key"
    settings.voice_profile_path = Path("config/voice_profiles.json")
    settings.default_voice_profile = "classic"
    settings.elevenlabs_voice_overrides = overrides
    return settings


class DummyVoiceProfile:
    def __init__(self, fallback_voice):
        self._fallback = fallback_voice

    def get_elevenlabs_voice(self, speaker, language):
        return self._fallback


@pytest.mark.parametrize(
    "override,expected_voice",
    [
        ({"voice_id": "override123", "model": "eleven_v3"}, "override123"),
    ],
)
def test_voice_override_preferred(override, expected_voice):
    from src.audio.elevenlabs_tts import ElevenLabsSpeechSynthesizer

    settings = _mock_settings({"Roee": override})
    fallback = {"voice_id": "profileVoice", "model": "eleven_v3"}
    voice_manager = MagicMock()
    voice_manager.get.return_value = DummyVoiceProfile(fallback)

    with patch("src.audio.elevenlabs_tts.ElevenLabs"), patch(
        "src.audio.elevenlabs_tts.detect_language", return_value="he"
    ), patch("src.audio.elevenlabs_tts.get_logger", return_value=MagicMock()) as mock_logger:
        synth = ElevenLabsSpeechSynthesizer(settings=settings, voice_manager=voice_manager)
        config = synth._get_voice_config("Roee", "שלום")

    assert config["voice_id"] == expected_voice
    logger = mock_logger.return_value
    logger.info.assert_called()


def test_invalid_override_logs_warning():
    from src.audio.elevenlabs_tts import ElevenLabsSpeechSynthesizer

    settings = _mock_settings({"Roee": {"model": "eleven_v3"}})
    fallback = {"voice_id": "profileVoice", "model": "eleven_v3"}
    voice_manager = MagicMock()
    voice_manager.get.return_value = DummyVoiceProfile(fallback)

    with patch("src.audio.elevenlabs_tts.ElevenLabs"), patch(
        "src.audio.elevenlabs_tts.detect_language", return_value="he"
    ), patch("src.audio.elevenlabs_tts.get_logger", return_value=MagicMock()) as mock_logger:
        synth = ElevenLabsSpeechSynthesizer(settings=settings, voice_manager=voice_manager)
        config = synth._get_voice_config("Roee", "שלום")

    assert config["voice_id"] == "profileVoice"
    logger = mock_logger.return_value
    logger.warning.assert_called()

