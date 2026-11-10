from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import requests

from src.utils import Settings, get_logger


class VoiceLabService:
    """
    ElevenLabs Instant Voice Cloning helper.

    Provides:
    - Quota lookup (v1/user/subscription)
    - Instant cloning (v1/voices/instant)
    - Persisting cloned voice into voice_profiles.json under 'Custom'
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(self.__class__.__name__)
        if not self.settings.elevenlabs_api_key:
            raise ValueError("ElevenLabs API key is required for Voice Lab features.")
        self.base_url = "https://api.elevenlabs.io/v1"
        self.voice_profiles_path = settings.voice_profile_path
        self.headers = {"xi-api-key": self.settings.elevenlabs_api_key}

    def get_subscription_quota(self) -> Dict[str, int]:
        """Return current ElevenLabs character usage/limit."""
        url = f"{self.base_url}/user/subscription"
        resp = requests.get(url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        return {
            "character_count": int(payload.get("character_count", 0)),
            "character_limit": int(payload.get("character_limit", 0)),
        }

    def clone_instant_voice(self, audio_path: Path, voice_name: str = "Custom Voice") -> str:
        """
        Call ElevenLabs Instant Voice Cloning API with a WAV/MP3 sample and return voice_id.
        """
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio sample not found: {audio_path}")

        url = f"{self.base_url}/voices/instant"
        with audio_path.open("rb") as fh:
            files = {"audio": fh}
            data = {"name": voice_name}
            resp = requests.post(url, headers=self.headers, files=files, data=data, timeout=180)
        resp.raise_for_status()
        payload = resp.json()
        voice_id = payload.get("voice_id")
        if not voice_id:
            raise RuntimeError("ElevenLabs cloning did not return a voice_id.")
        self.logger.info("Cloned custom voice_id=%s", voice_id)
        return voice_id

    def save_custom_voice_profile(self, voice_id: str, voice_name: str = "Custom Voice") -> None:
        """
        Persist a 'Custom' profile in voice_profiles.json pointing to the cloned voice.
        """
        path = self.voice_profiles_path
        payload: Dict[str, object] = {"default_profile": "classic", "profiles": {}}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("Failed to read existing voice profiles: %s", exc)
        profiles: Dict[str, object] = payload.setdefault("profiles", {})
        profiles["Custom"] = {
            "description": f"User cloned voice ({voice_name}) via Voice Lab.",
            "provider": "elevenlabs",
            "voices": {
                "Roee": {
                    "elevenlabs": {
                        "voice_id": voice_id,
                        "model": "eleven_v3",
                    }
                },
                "Noa": {
                    "elevenlabs": {
                        "voice_id": voice_id,
                        "model": "eleven_v3",
                    }
                },
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.info("Saved cloned voice to profile 'Custom' in %s", path)
