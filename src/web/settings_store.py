"""The .env file behind the settings screen, plus system status and connection tests.

Secrets never leave the server in full: the UI sees whether a key is set and its
last four characters, and sends a new value only when the user types one.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from ..audio.dialogue_tts import (
    ELEVENLABS_DEFAULT_MODEL,
    GEMINI_DEFAULT_MODEL,
    GEMINI_DEFAULT_VOICES,
    GEMINI_VOICES,
    ElevenLabsDialogue,
    GeminiDialogue,
)
from ..engine.llm import local_model_available
from ..utils.config import Settings, read_saved_tts_provider, resolve_llm_provider, resolve_tts_provider

VOICE_SAMPLE = [
    {"speaker": "Roee", "text": "שלום וברוכים הבאים! היום אנחנו צוללים לשיעור אחד ומבינים אותו עד הסוף.", "delivery": "warm"},
    {"speaker": "Noa", "text": "ואני אשאל את כל השאלות שאתם בטח שואלים את עצמכם. מתחילים?", "delivery": "curious"},
]


@dataclass(frozen=True)
class EnvField:
    key: str
    label: str
    group: str
    secret: bool = False
    help: str = ""          # anchor in docs/guide.he.html
    placeholder: str = ""
    choices: Tuple[str, ...] = ()


GEMINI_TEXT_MODELS = ("gemini-3.8-flash", "gemini-4-argon", "gemini-3.6-flash")
GEMINI_IMAGE_MODELS = ("gemini-3.1-flash-image", "gemini-3-pro-image")

# Groups: "choose" holds the two provider choices; the UI shows the other groups only when relevant
FIELDS: List[EnvField] = [
    EnvField("LLM_PROVIDER", "מי כותב את התסריט", "choose", help="writer", choices=("gemini", "azure", "local")),
    EnvField("DEFAULT_TTS_PROVIDER", "מי מקריא", "choose", help="voices", choices=("elevenlabs", "gemini", "azure")),
    EnvField("GEMINI_API_KEY", "מפתח Gemini", "gemini", secret=True, help="gemini"),
    EnvField("GEMINI_TEXT_MODEL", "מודל לכתיבה", "gemini", help="gemini", choices=GEMINI_TEXT_MODELS),
    EnvField("GEMINI_IMAGE_MODEL", "מודל לתמונות בווידאו", "gemini", help="gemini", choices=GEMINI_IMAGE_MODELS),
    EnvField("GEMINI_VOICE_ROEE", "קול של רועי", "gemini", help="gemini", choices=GEMINI_VOICES),
    EnvField("GEMINI_VOICE_NOA", "קול של נועה", "gemini", help="gemini", choices=GEMINI_VOICES),
    EnvField("ELEVENLABS_API_KEY", "מפתח ElevenLabs", "elevenlabs", secret=True, help="elevenlabs"),
    EnvField("ELEVENLABS_VOICE_ROEE", "קול של רועי", "elevenlabs", help="elevenlabs"),
    EnvField("ELEVENLABS_VOICE_NOA", "קול של נועה", "elevenlabs", help="elevenlabs"),
    EnvField("ELEVENLABS_MODEL", "מודל", "elevenlabs", help="elevenlabs", choices=("eleven_v4", "eleven_v3")),
    EnvField("AZURE_OPENAI_ENDPOINT", "Endpoint", "openai", help="azure-openai", placeholder="https://my-resource.openai.azure.com/"),
    EnvField("AZURE_OPENAI_API_KEY", "מפתח (KEY 1)", "openai", secret=True, help="azure-openai"),
    EnvField("AZURE_OPENAI_DEPLOYMENT", "שם הפריסה (Deployment)", "openai", help="azure-openai", placeholder="gpt-4o"),
    EnvField("AZURE_OPENAI_API_VERSION", "גרסת API", "openai", help="azure-openai", placeholder="2024-02-01"),
    EnvField("AZURE_SPEECH_KEY", "מפתח Azure Speech", "azure", secret=True, help="azure-speech"),
    EnvField("AZURE_SPEECH_REGION", "אזור", "azure", help="azure-speech", placeholder="westeurope"),
    EnvField("LOCAL_LLM", "בדיקות במודל המקומי", "local", help="local-model", choices=("auto", "on", "off")),
    EnvField("LOCAL_LLM_MODEL", "שם המודל", "local", help="local-model", placeholder="gemma3:12b"),
    EnvField("PODCAST_LANGUAGE", "שפת הפודקאסט", "podcast", help="settings", choices=("he", "en")),
    EnvField("PODCAST_FORMAT", "פורמט ברירת מחדל", "podcast", help="formats",
             choices=("auto", "deep_dive", "beginner_explainer", "exam_prep", "story", "debate", "brief", "critique")),
    EnvField("PODCAST_TARGET_MINUTES", "אורך פרק בדקות (0 = אוטומטי)", "podcast", help="settings", placeholder="0"),
]
FIELDS_BY_KEY = {f.key: f for f in FIELDS}

# resolve_tts_provider explains its choice in English (it also feeds logs and the doctor script)
_TTS_REASONS_HE = (
    ("has no keys", "השירות שנבחר בלי מפתח, אז נבחר זה שיש לו מפתח"),
    ("only provider with keys", "השירות היחיד שיש לו מפתח"),
    ("set by DEFAULT_TTS_PROVIDER", "נבחר בהגדרות"),
    ("last choice saved", "הבחירה האחרונה שנשמרה באפליקציה"),
    ("both providers have keys", "לשני השירותים יש מפתח; ברירת המחדל היא Azure"),
    ("no TTS keys", "אין מפתח לאף שירות קולות"),
)


def tts_reason_hebrew(reason: str) -> str:
    return next((hebrew for marker, hebrew in _TTS_REASONS_HE if marker in reason), reason)
HEBREW_MODEL_FOLDER = "models--ivrit-ai--whisper-large-v3-turbo-ct2"


class SettingsStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.env_path = self.root / ".env"
        self._gpu: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ .env

    def read_env(self) -> Dict[str, str]:
        values: Dict[str, str] = {}
        if not self.env_path.exists():
            return values
        for line in self.env_path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
        return values

    def write_env(self, updates: Dict[str, Optional[str]]) -> None:
        """Set the given keys (None removes the key), keeping every other line as it was."""
        lines = self.env_path.read_text(encoding="utf-8-sig").splitlines() if self.env_path.exists() else [
            "# M.B.S Studio settings. Edit here or in the app's settings screen."
        ]
        remaining = dict(updates)
        kept: List[str] = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
            if key in remaining:
                value = remaining.pop(key)
                if value is None:
                    continue
                line = f"{key}={value}"
            kept.append(line)
        kept.extend(f"{key}={value}" for key, value in remaining.items() if value is not None)
        self.env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    def view(self) -> List[Dict[str, Any]]:
        env = self.read_env()
        fields = []
        for f in FIELDS:
            value = env.get(f.key, "")
            item = {"key": f.key, "label": f.label, "group": f.group, "secret": f.secret, "help": f.help,
                    "placeholder": f.placeholder, "choices": list(f.choices)}
            if f.secret:
                item.update(is_set=bool(value), hint=f"…{value[-4:]}" if len(value) > 8 else "")
            else:
                item["value"] = value
            fields.append(item)
        return fields

    def update(self, values: Dict[str, Any]) -> None:
        updates: Dict[str, Optional[str]] = {}
        for key, value in values.items():
            field_def = FIELDS_BY_KEY.get(key)
            if field_def is None or value is None:
                continue
            value = str(value).strip().replace("\n", "")
            if value == "":
                # Empty secret = keep the current key; empty setting = use the default (an
                # empty line would override the default with "", e.g. an empty API version)
                if not field_def.secret:
                    updates[key] = None
                continue
            if field_def.choices and value not in field_def.choices:
                raise ValueError(f"{field_def.label}: ערך לא חוקי")
            updates[key] = value
        if updates:
            self.write_env(updates)

    def load_settings(self) -> Tuple[Optional[Settings], str]:
        """Re-read .env into the process and build Settings; returns (settings, error)."""
        load_dotenv(self.env_path, override=True)
        try:
            return Settings.load(self.env_path), ""
        except ValueError as exc:
            return None, str(exc)

    # ------------------------------------------------------------------ status

    def gpu(self) -> Dict[str, Any]:
        if self._gpu is None:
            self._gpu = {"name": "", "memory_gb": 0}
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if out.returncode == 0 and "," in out.stdout:
                    name, memory = out.stdout.strip().splitlines()[0].rsplit(",", 1)
                    self._gpu = {"name": name.strip(), "memory_gb": round(int(memory.strip()) / 1024)}
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        return self._gpu

    def status(self) -> Dict[str, Any]:
        env = self.read_env()
        has_azure = bool(env.get("AZURE_SPEECH_KEY") and env.get("AZURE_SPEECH_REGION"))
        has_eleven = bool(env.get("ELEVENLABS_API_KEY"))
        has_gemini = bool(env.get("GEMINI_API_KEY"))
        provider, reason = resolve_tts_provider(
            env.get("DEFAULT_TTS_PROVIDER", "auto"), has_azure, has_eleven,
            read_saved_tts_provider(self.root / "config" / "ui_config.json"), has_gemini,
        )
        eleven_voices_set = bool(env.get("ELEVENLABS_VOICE_ROEE") and env.get("ELEVENLABS_VOICE_NOA"))
        local_model = env.get("LOCAL_LLM_MODEL") or "gemma3:12b"
        local_mode = env.get("LOCAL_LLM", "auto")
        local_available = local_model_available(env.get("LOCAL_LLM_BASE_URL", "http://localhost:11434"), local_model, timeout=1.0)
        models_dir = self.root / "models"
        has_azure_openai = all(env.get(k) for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"))
        writer = resolve_llm_provider(env.get("LLM_PROVIDER", "auto"), has_azure_openai, has_gemini)
        return {
            "openai": has_azure_openai,
            "writer": {
                "provider": writer,
                "ready": {"gemini": has_gemini, "azure": has_azure_openai, "local": local_available}[writer],
                "model": {"gemini": env.get("GEMINI_TEXT_MODEL") or GEMINI_TEXT_MODELS[0],
                          "azure": env.get("AZURE_OPENAI_DEPLOYMENT", ""), "local": local_model}[writer],
            },
            "voices": {
                "provider": provider, "reason": tts_reason_hebrew(reason),
                "azure": has_azure, "elevenlabs": has_eleven, "gemini": has_gemini,
                "conversation": provider in ("elevenlabs", "gemini"),
                "ready": {"azure": has_azure, "elevenlabs": has_eleven and eleven_voices_set, "gemini": has_gemini}.get(provider, False),
                "missing_voices": provider == "elevenlabs" and has_eleven and not eleven_voices_set,
            },
            "local_llm": {
                "model": local_model,
                "mode": local_mode,
                "available": local_mode != "off" and local_available,
                "ollama_installed": bool(shutil.which("ollama")) or (Path.home() / "AppData/Local/Programs/Ollama/ollama.exe").exists(),
            },
            "transcriber": {
                "installed": importlib.util.find_spec("faster_whisper") is not None,
                "hebrew_model": (models_dir / HEBREW_MODEL_FOLDER).exists(),
            },
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "gpu": self.gpu(),
        }

    # ------------------------------------------------------------------ voices

    def conversation_synth(self, provider: str):
        """The conversation renderer for a provider, built straight from .env (no Azure OpenAI needed)."""
        env = self.read_env()
        if provider == "elevenlabs":
            return ElevenLabsDialogue(
                env.get("ELEVENLABS_API_KEY", ""),
                {"Roee": env.get("ELEVENLABS_VOICE_ROEE", ""), "Noa": env.get("ELEVENLABS_VOICE_NOA", "")},
                model=env.get("ELEVENLABS_MODEL") or ELEVENLABS_DEFAULT_MODEL,
                language="en" if env.get("PODCAST_LANGUAGE", "he").startswith("en") else "he",
            )
        if provider == "gemini":
            return GeminiDialogue(
                env.get("GEMINI_API_KEY", ""),
                {"Roee": env.get("GEMINI_VOICE_ROEE") or GEMINI_DEFAULT_VOICES["Roee"],
                 "Noa": env.get("GEMINI_VOICE_NOA") or GEMINI_DEFAULT_VOICES["Noa"]},
                model=env.get("GEMINI_TTS_MODEL") or GEMINI_DEFAULT_MODEL,
            )
        raise ValueError(provider)

    # ------------------------------------------------------------------ connection tests

    def voice_sample(self, voice: str) -> Path:
        """A short Hebrew line in one Gemini voice, made once and kept, to hear a voice before choosing it."""
        if voice not in GEMINI_VOICES:
            raise ValueError(voice)
        folder = self.root / "logs" / "voice-samples"
        folder.mkdir(parents=True, exist_ok=True)
        cached = folder / f"gemini-{voice}.wav"
        if cached.exists():
            return cached
        env = self.read_env()
        synth = GeminiDialogue(env.get("GEMINI_API_KEY", ""), {"Roee": voice},
                               model=env.get("GEMINI_TTS_MODEL") or GEMINI_DEFAULT_MODEL)
        line = [{"speaker": "Roee", "text": "שלום! ככה אני נשמע כשאני מסביר משהו מעניין. נשמע לך טוב?", "delivery": "friendly"}]
        return synth.render(line, folder / f"gemini-{voice}").path

    # ------------------------------------------------------------------ connection tests

    def test_connections(self) -> Dict[str, Dict[str, Any]]:
        env = self.read_env()
        results: Dict[str, Dict[str, Any]] = {}

        has_azure_openai = all(env.get(k) for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"))
        writer = resolve_llm_provider(env.get("LLM_PROVIDER", "auto"), has_azure_openai, bool(env.get("GEMINI_API_KEY")))
        if writer == "gemini":
            results["writer_gemini"] = self._test_gemini(env) if env.get("GEMINI_API_KEY") else {"ok": False, "message": "חסר מפתח Gemini"}
        elif writer == "local":
            model = env.get("LOCAL_LLM_MODEL") or "gemma3:12b"
            ok = local_model_available(env.get("LOCAL_LLM_BASE_URL", "http://localhost:11434"), model)
            results["writer_local"] = {"ok": ok, "message": "פועל" if ok else f"Ollama לא פועל או שהמודל {model} לא הורד"}
        elif has_azure_openai:
            results["openai"] = self._test_openai(env)
        else:
            results["openai"] = {"ok": False, "message": "חסרים פרטים"}

        if env.get("AZURE_SPEECH_KEY") and env.get("AZURE_SPEECH_REGION"):
            results["azure_speech"] = self._test_azure_speech(env["AZURE_SPEECH_KEY"], env["AZURE_SPEECH_REGION"])
        if env.get("ELEVENLABS_API_KEY"):
            results["elevenlabs"] = self._test_elevenlabs(env["ELEVENLABS_API_KEY"])
        return results

    @staticmethod
    def _test_gemini(env: Dict[str, str]) -> Dict[str, Any]:
        try:
            from google import genai

            client = genai.Client(api_key=env["GEMINI_API_KEY"])
            client.models.generate_content(model=env.get("GEMINI_TEXT_MODEL") or GEMINI_TEXT_MODELS[0], contents="ping")
            return {"ok": True, "message": "מחובר"}
        except Exception as exc:
            text = str(exc)
            if "API key" in text or "PERMISSION_DENIED" in text or "401" in text or "403" in text:
                return {"ok": False, "message": "המפתח שגוי או ללא הרשאה"}
            if "404" in text or "not found" in text.lower():
                return {"ok": False, "message": "המודל לא זמין לחשבון הזה. נסה מודל אחר"}
            return {"ok": False, "message": text[:200]}

    @staticmethod
    def _test_openai(env: Dict[str, str]) -> Dict[str, Any]:
        from openai import APIConnectionError, AuthenticationError, AzureOpenAI, NotFoundError

        try:
            client = AzureOpenAI(
                api_key=env["AZURE_OPENAI_API_KEY"],
                api_version=env.get("AZURE_OPENAI_API_VERSION") or "2024-02-01",
                azure_endpoint=env["AZURE_OPENAI_ENDPOINT"],
                timeout=20,
                max_retries=0,
            )
            client.chat.completions.create(
                model=env["AZURE_OPENAI_DEPLOYMENT"],
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return {"ok": True, "message": "מחובר"}
        except AuthenticationError:
            return {"ok": False, "message": "המפתח שגוי (401)"}
        except NotFoundError:
            return {"ok": False, "message": "ה-Endpoint או שם הפריסה שגויים (404)"}
        except APIConnectionError:
            return {"ok": False, "message": "אין חיבור לכתובת. בדוק את ה-Endpoint ואת החיבור לאינטרנט"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)[:200]}

    @staticmethod
    def _test_azure_speech(key: str, region: str) -> Dict[str, Any]:
        url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
        try:
            response = requests.post(url, headers={"Ocp-Apim-Subscription-Key": key}, timeout=15)
        except requests.RequestException:
            return {"ok": False, "message": "אין חיבור. בדוק את שם האזור"}
        if response.status_code == 200:
            return {"ok": True, "message": "מחובר"}
        if response.status_code in (401, 403):
            return {"ok": False, "message": "המפתח שגוי, או שהוא שייך לאזור אחר"}
        return {"ok": False, "message": f"שגיאה {response.status_code}"}

    @staticmethod
    def _test_elevenlabs(key: str) -> Dict[str, Any]:
        try:
            response = requests.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": key}, timeout=15)
        except requests.RequestException:
            return {"ok": False, "message": "אין חיבור ל-ElevenLabs"}
        if response.status_code == 200:
            return {"ok": True, "message": "מחובר"}
        if response.status_code == 401:
            return {"ok": False, "message": "המפתח שגוי"}
        return {"ok": False, "message": f"שגיאה {response.status_code}"}


def apply_project_options(settings: Settings, options: Dict[str, Any]) -> Settings:
    """A copy of the settings with one project's choices (format, length) applied."""
    import copy

    copied = copy.copy(settings)
    if options.get("format"):
        copied.podcast_format = options["format"]
    if options.get("minutes"):
        copied.podcast_target_minutes = float(options["minutes"])
    if options.get("focus"):
        copied.podcast_focus = str(options["focus"])
    if options.get("language") in ("he", "en"):
        copied.podcast_language = options["language"]
    return copied
