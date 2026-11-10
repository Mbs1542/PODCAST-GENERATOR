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


class _Reply:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class _FakeEleven:
    def __init__(self, add_status=200):
        self.add_status, self.calls = add_status, []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        if url.endswith("/subscription"):
            return _Reply(200, {"tier": "free", "voice_slots_used": 1, "voice_limit": 3})
        return _Reply(200, {"voices": [
            {"voice_id": "paidVoice123", "public_owner_id": "owner000001", "name": "Zina", "gender": "female",
             "free_users_allowed": False, "preview_url": "https://x/z.mp3"},
            {"voice_id": "danitVoice12", "public_owner_id": "owner000002", "name": "Danit", "gender": "female",
             "age": "young", "free_users_allowed": True, "is_added_by_user": True, "preview_url": "https://x/d.mp3"},
            {"voice_id": "", "public_owner_id": "owner000003", "name": "broken"},
        ]})

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        if self.add_status != 200:
            return _Reply(self.add_status, {"detail": {"status": "voice_limit_reached", "message": "limit"}})
        return _Reply(200, {"voice_id": "newAccountId1"})


def test_hebrew_library_lists_native_voices_and_free_plan_limits():
    from src.audio.dialogue_tts import list_elevenlabs_library

    fake = _FakeEleven()
    result = list_elevenlabs_library("key", http=fake)
    assert fake.calls[0][2]["language"] == "he"
    assert [v["name"] for v in result["voices"]] == ["Danit", "Zina"]  # usable first, broken entries dropped
    assert result["voices"][0]["added"] is True
    # On the free plan ElevenLabs refuses every library voice through the API (402), whatever the voice's own flag
    assert all(v["needs_paid"] for v in result["voices"]) and result["api_blocked"] is True
    assert (result["tier"], result["slots_used"], result["slots_limit"]) == ("free", 1, 3)


def test_the_free_plan_library_refusal_is_explained_in_hebrew():
    from src.audio.dialogue_tts import _elevenlabs_error

    reply = _Reply(402, {"detail": {"status": "payment_required",
                                    "message": "Free users cannot use library voices via the API. Please upgrade your subscription."}})
    message = _elevenlabs_error(reply)
    assert "במנוי החינמי" in message and "Gemini" in message


def test_adding_a_library_voice_returns_its_account_id_and_explains_a_full_account():
    import pytest

    from src.audio.dialogue_tts import DialogueTTSError, add_elevenlabs_library_voice

    fake = _FakeEleven()
    assert add_elevenlabs_library_voice("key", "owner000002", "danitVoice12", "Danit", http=fake) == "newAccountId1"
    assert fake.calls[-1][1].endswith("/voices/add/owner000002/danitVoice12") and fake.calls[-1][2] == {"new_name": "Danit"}
    with pytest.raises(DialogueTTSError, match="מזהה"):
        add_elevenlabs_library_voice("key", "../../etc", "danitVoice12", "x", http=fake)  # no path tricks
    with pytest.raises(DialogueTTSError, match="המרבי"):
        add_elevenlabs_library_voice("key", "owner000002", "danitVoice12", "Danit", http=_FakeEleven(add_status=400))
