"""Check what this installation is configured to use, without printing any secret.

Answers in particular: which voice (TTS) service do I use? It looks at the keys in
.env, the choice saved by the app, the voice profiles, and the logs of past runs.

    python -m scripts.doctor
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.engine.llm import local_model_available  # noqa: E402
from src.utils.config import read_saved_tts_provider, resolve_tts_provider  # noqa: E402

_TTS_LOG_RE = re.compile(r"Using TTS provider:\s*(\w+)")


def _ok(flag: bool) -> str:
    return "yes" if flag else "no "


def _env() -> Dict[str, str]:
    values = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v}
    values.update({k: v for k, v in os.environ.items() if v and k not in values})
    return values


def _voice_profiles(path: Path) -> List[Tuple[str, List[str]]]:
    """Profile name -> providers that have voices configured in it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    profiles = []
    for name, payload in (data.get("profiles") or {}).items():
        providers = {payload["provider"]} if payload.get("provider") else set()
        for speaker_map in (payload.get("voices") or {}).values():
            if isinstance(speaker_map, dict):
                if isinstance(speaker_map.get("elevenlabs"), dict) and speaker_map["elevenlabs"].get("voice_id"):
                    providers.add("elevenlabs")
                if "azure" in speaker_map or any(str(v).startswith(("he-IL", "en-US")) for v in speaker_map.values()):
                    providers.add("azure")
        profiles.append((name, sorted(providers)))
    return profiles


def _past_runs(output_dir: Path) -> Tuple[Counter, str]:
    """Count TTS providers recorded in past run logs; return (counts, most recent provider)."""
    counts: Counter = Counter()
    latest, latest_time = "", 0.0
    if not output_dir.exists():
        return counts, latest
    for log in output_dir.rglob("processing_log.txt"):
        try:
            found = _TTS_LOG_RE.findall(log.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if not found:
            continue
        counts.update(found)
        mtime = log.stat().st_mtime
        if mtime > latest_time:
            latest, latest_time = found[-1], mtime
    return counts, latest


def main() -> None:
    env = _env()
    print(f"Project: {ROOT}")
    print(f".env file: {'found' if (ROOT / '.env').exists() else 'MISSING (copy .env.example to .env)'}\n")

    has_openai = all(env.get(k) for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"))
    has_azure_tts = bool(env.get("AZURE_SPEECH_KEY") and env.get("AZURE_SPEECH_REGION"))
    has_eleven = bool(env.get("ELEVENLABS_API_KEY"))
    print("Services with keys in .env")
    print(f"  [{_ok(has_openai)}] Azure OpenAI (script writing)   deployment: {env.get('AZURE_OPENAI_DEPLOYMENT', '-')}")
    print(f"  [{_ok(has_azure_tts)}] Azure Speech (voices)           region: {env.get('AZURE_SPEECH_REGION', '-')}")
    print(f"  [{_ok(has_eleven)}] ElevenLabs (voices)")
    print(f"  [{_ok(bool(env.get('GEMINI_API_KEY')))}] Gemini (chat / visuals)\n")

    ui_config = ROOT / env.get("UI_CONFIG_PATH", "config/ui_config.json")
    saved = read_saved_tts_provider(ui_config)
    profiles = _voice_profiles(ROOT / env.get("VOICE_PROFILES_PATH", "config/voice_profiles.json"))
    counts, latest = _past_runs(ROOT / env.get("OUTPUT_BASE_DIR", "outputs"))

    print("Voice service evidence")
    print(f"  DEFAULT_TTS_PROVIDER in .env: {env.get('DEFAULT_TTS_PROVIDER', '(not set)')}")
    print(f"  Saved in the app:             {saved or '(nothing saved)'}")
    if profiles:
        for name, providers in profiles:
            print(f"  Voice profile '{name}':        {', '.join(providers) or 'no voices'}")
    else:
        print("  Voice profiles:               config/voice_profiles.json not found")
    if counts:
        history = ", ".join(f"{name} x{count}" for name, count in counts.most_common())
        print(f"  Past runs:                    {history}; most recent: {latest}")
    else:
        print("  Past runs:                    no run logs in the output folder")

    provider, reason = resolve_tts_provider(env.get("DEFAULT_TTS_PROVIDER", "auto"), has_azure_tts, has_eleven, saved)
    print(f"\n  -> Voices will use: {provider.upper()} ({reason})")
    if counts and latest and latest != provider:
        print(f"     Note: your most recent run used {latest}. Set DEFAULT_TTS_PROVIDER={latest} in .env to keep it.")
    if not (has_azure_tts or has_eleven):
        print("     No voice keys found: add AZURE_SPEECH_KEY + AZURE_SPEECH_REGION or ELEVENLABS_API_KEY to .env.")

    base_url = env.get("LOCAL_LLM_BASE_URL", "http://localhost:11434")
    model = env.get("LOCAL_LLM_MODEL", "gemma3:12b")
    mode = env.get("LOCAL_LLM", "auto")
    available = mode != "off" and local_model_available(base_url, model)
    print("\nScript engine")
    print(f"  Engine: {env.get('DIALOGUE_ENGINE', 'v2')}, format: {env.get('PODCAST_FORMAT', 'auto')}")
    print(f"  [{_ok(available)}] Local checker model {model} at {base_url} (LOCAL_LLM={mode})")
    if not available and mode != "off":
        print(f"        Optional. To enable: install Ollama, then `ollama pull {model}`. Without it, checks use Azure.")

    print(f"\n  [{_ok(bool(shutil.which('ffmpeg')))}] ffmpeg on PATH (needed for audio)")


if __name__ == "__main__":
    main()
