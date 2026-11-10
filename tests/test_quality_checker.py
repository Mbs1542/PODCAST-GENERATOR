from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.utils.quality_checker import QualityChecker


def _build_settings(tmp_path: Path, *, overrides=None, profile=None):
    """Create a lightweight settings object for the quality checker."""
    voice_profile_path = tmp_path / "voice_profiles.json"
    profile_payload = profile or {
        "default_profile": "classic",
        "profiles": {
            "classic": {
                "voices": {}
            }
        },
    }
    voice_profile_path.write_text(
        json.dumps(profile_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SimpleNamespace(
        default_tts_provider="elevenlabs",
        elevenlabs_api_key="test-key",
        speech_key="azure-key",
        speech_region="eastus",
        openai_api_key="azure-openai",
        voice_profile_path=voice_profile_path,
        default_voice_profile=profile_payload.get("default_profile", "classic"),
        elevenlabs_voice_overrides=overrides or {},
    )


def test_model_check_fails_for_incompatible_override(tmp_path: Path):
    """Overrides that reference multilingual v2 should fail pre-flight."""
    settings = _build_settings(
        tmp_path,
        overrides={
            "Noa": {"voice_id": "21m00Tcm4TlvDq8ikWAM", "model": "eleven_multilingual_v2"}
        },
    )
    checker = QualityChecker(settings)

    result = checker._check_model_compatibility()

    assert result.status == "FAIL"
    assert "eleven_multilingual_v2" in result.message


def test_model_check_reads_voice_profile_models(tmp_path: Path):
    """When no overrides exist, the active voice profile should be validated."""
    profile_payload = {
        "default_profile": "classic",
        "profiles": {
            "classic": {
                "voices": {
                    "Noa": {
                        "elevenlabs": {
                            "voice_id": "21m00Tcm4TlvDq8ikWAM",
                            "model": "eleven_multilingual_v2",
                        }
                    }
                }
            }
        },
    }
    settings = _build_settings(tmp_path, profile=profile_payload)
    checker = QualityChecker(settings)

    result = checker._check_model_compatibility()

    assert result.status == "FAIL"
    assert "eleven_multilingual_v2" in result.message


def test_model_check_passes_with_safe_profile(tmp_path: Path):
    """Profiles that already use Hebrew-safe models should pass."""
    profile_payload = {
        "default_profile": "classic",
        "profiles": {
            "classic": {
                "voices": {
                    "Noa": {
                        "elevenlabs": {
                            "voice_id": "21m00Tcm4TlvDq8ikWAM",
                            "model": "eleven_v3",
                        }
                    },
                    "Roee": {
                        "elevenlabs": {
                            "voice_id": "pNInz6obpgDQGcFmaJgB",
                            "model": "eleven_v4",
                        }
                    },
                }
            }
        },
    }
    settings = _build_settings(tmp_path, profile=profile_payload)
    checker = QualityChecker(settings)

    result = checker._check_model_compatibility()

    assert result.status == "PASS"
    assert result.details["models"]["Noa"] == "eleven_v3"
