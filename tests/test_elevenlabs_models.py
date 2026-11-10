"""Guard: every ElevenLabs model the app recommends or falls back to for Hebrew is one that reads Hebrew.

ElevenLabs documents Hebrew only for the v3/v4 generation; Multilingual v2 and Turbo/Flash v2.5 have no
Hebrew (and v2.5 is deprecated). These defaults were spread over several files and had drifted.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.audio.dialogue_tts import ELEVENLABS_DEFAULT_MODEL
from src.audio.elevenlabs_tts import ElevenLabsSpeechSynthesizer
from src.utils.config import _NO_HEBREW_ELEVENLABS_MODELS
from src.utils.quality_checker import HEBREW_COMPATIBLE_MODELS, HEBREW_INCOMPATIBLE_MODELS, HEBREW_RECOMMENDED_MODEL
from src.web.settings_store import FIELDS_BY_KEY

SRC = Path(__file__).resolve().parents[1] / "src"
NO_HEBREW = {"eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_turbo_v2"}


def test_recommended_and_default_models_read_hebrew():
    assert HEBREW_COMPATIBLE_MODELS == {"eleven_v3", "eleven_v4"}
    assert not HEBREW_COMPATIBLE_MODELS & HEBREW_INCOMPATIBLE_MODELS
    assert NO_HEBREW <= HEBREW_INCOMPATIBLE_MODELS and NO_HEBREW <= _NO_HEBREW_ELEVENLABS_MODELS
    assert HEBREW_RECOMMENDED_MODEL in HEBREW_COMPATIBLE_MODELS
    assert ELEVENLABS_DEFAULT_MODEL in HEBREW_COMPATIBLE_MODELS
    assert ElevenLabsSpeechSynthesizer.HEBREW_SAFE_MODELS <= HEBREW_COMPATIBLE_MODELS
    assert set(FIELDS_BY_KEY["ELEVENLABS_MODEL"].choices) <= HEBREW_COMPATIBLE_MODELS


def test_old_models_appear_only_in_the_lists_that_name_them_as_unsupported():
    """No default, fallback or tip anywhere in the code may point at a model without Hebrew."""
    allowed = {"utils/quality_checker.py", "utils/config.py"}  # the "no Hebrew" lists themselves
    offenders = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"eleven_(turbo_v2(_5)?|flash_v2_5|multilingual_v2)", line) and relative not in allowed:
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_override_without_model_falls_back_to_a_hebrew_model():
    settings = MagicMock()
    settings.elevenlabs_api_key = "test"
    settings.elevenlabs_voice_overrides = {"Roee": {"voice_id": "voice123"}}
    settings.default_voice_profile = "classic"
    manager = MagicMock()
    with patch("src.audio.elevenlabs_tts.ElevenLabs"), patch("src.audio.elevenlabs_tts.detect_language", return_value="he"), \
            patch("src.audio.elevenlabs_tts.get_logger", return_value=MagicMock()):
        synth = ElevenLabsSpeechSynthesizer(settings=settings, voice_manager=manager)
        assert synth._get_voice_config("Roee", "שלום")["model"] == "eleven_v3"
