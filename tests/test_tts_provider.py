import json

from src.utils.config import read_saved_tts_provider, resolve_llm_provider, resolve_tts_provider


def test_writer_choice():
    assert resolve_llm_provider("auto", has_azure=False, has_gemini=True) == "gemini"
    assert resolve_llm_provider("auto", has_azure=True, has_gemini=True) == "azure"  # existing setups keep Azure
    assert resolve_llm_provider("gemini", has_azure=True, has_gemini=True) == "gemini"
    assert resolve_llm_provider("local", has_azure=False, has_gemini=False) == "local"


def test_explicit_choice_with_keys_wins():
    assert resolve_tts_provider("elevenlabs", True, True, "azure")[0] == "elevenlabs"


def test_saved_app_choice_is_used_when_env_says_auto():
    assert resolve_tts_provider("auto", True, True, "elevenlabs") == (
        "elevenlabs",
        "last choice saved in the app settings",
    )


def test_only_configured_provider_is_picked():
    assert resolve_tts_provider("auto", False, True, "")[0] == "elevenlabs"
    assert resolve_tts_provider("auto", True, False, "")[0] == "azure"


def test_choice_without_keys_switches_to_the_configured_one():
    provider, reason = resolve_tts_provider("azure", False, True, "azure")
    assert provider == "elevenlabs"
    assert "has no keys" in reason


def test_gemini_only_on_request_or_as_last_resort():
    # A Gemini key that exists for images must not take the voices over from Azure
    assert resolve_tts_provider("auto", True, False, "", has_gemini=True)[0] == "azure"
    assert resolve_tts_provider("gemini", True, False, "", has_gemini=True)[0] == "gemini"
    assert resolve_tts_provider("auto", False, False, "", has_gemini=True)[0] == "gemini"
    assert resolve_tts_provider("auto", True, True, "gemini", has_gemini=True)[0] == "gemini"  # saved in the app


def test_nothing_configured_keeps_a_valid_name():
    assert resolve_tts_provider("auto", False, False, "")[0] == "azure"


def test_read_saved_provider(tmp_path):
    config = tmp_path / "ui_config.json"
    config.write_text(json.dumps({"default_tts_provider": "ElevenLabs"}), encoding="utf-8")
    assert read_saved_tts_provider(config) == "elevenlabs"
    assert read_saved_tts_provider(tmp_path / "missing.json") == ""
