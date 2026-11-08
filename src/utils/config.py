from __future__ import annotations

import os
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv


def _to_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


TTS_PROVIDERS = ("azure", "elevenlabs", "gemini")


def read_saved_tts_provider(ui_config_path: Path) -> str:
    """Return the TTS provider last chosen in the app (config/ui_config.json or legacy ui_prefs.json)."""
    for path in (ui_config_path, Path("config/ui_prefs.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("default_tts_provider", "")
        except (OSError, ValueError, AttributeError):
            continue
        if value:
            return str(value).lower()
    return ""


def resolve_tts_provider(
    requested: str,
    has_azure: bool,
    has_elevenlabs: bool,
    saved_preference: str = "",
    has_gemini: bool = False,
) -> Tuple[str, str]:
    """Pick the TTS provider from what is actually configured, and say why.

    Order: an explicit DEFAULT_TTS_PROVIDER that has keys, then the choice saved in
    the app, then whichever provider has keys. Never picks a provider without keys
    when another one has them. Gemini is chosen only on request or as the last
    resort: a Gemini key is often there just for images, so it must not take over
    the voices from Azure or ElevenLabs by itself.
    """
    requested = (requested or "").strip().lower()
    keyed = {"azure": has_azure, "elevenlabs": has_elevenlabs, "gemini": has_gemini}
    if keyed.get(requested):
        return requested, "set by DEFAULT_TTS_PROVIDER"
    saved = (saved_preference or "").strip().lower()
    if keyed.get(saved):
        return saved, "last choice saved in the app settings"
    available = [name for name in ("azure", "elevenlabs") if keyed[name]]
    prefix = f"DEFAULT_TTS_PROVIDER={requested} has no keys; " if requested in TTS_PROVIDERS else ""
    if len(available) == 1:
        return available[0], f"{prefix}only provider with keys in .env"
    if available:
        return "azure", "both providers have keys; defaulting to Azure"
    if has_gemini:
        return "gemini", f"{prefix}only provider with keys in .env"
    return (requested if requested in TTS_PROVIDERS else "azure"), "no TTS keys found in .env"


LLM_PROVIDERS = ("gemini", "azure", "local")


def resolve_llm_provider(requested: str, has_azure: bool, has_gemini: bool) -> str:
    """Which model writes the scripts. An explicit choice wins; otherwise whichever has keys.

    With both keys, Azure stays the writer so existing setups keep behaving as before.
    """
    requested = (requested or "").strip().lower()
    if requested in LLM_PROVIDERS:
        return requested
    if has_azure:
        return "azure"
    if has_gemini:
        return "gemini"
    return "azure"


@dataclass(slots=True)
class Settings:
    """Centralized configuration loaded from environment variables."""

    openai_endpoint: str
    openai_api_key: str
    openai_deployment: str
    openai_api_version: str = "2024-02-01"

    speech_key: str = ""
    speech_region: str = ""
    speech_endpoint: str = ""
    azure_default_voice: str = field(default_factory=lambda: os.getenv("AZURE_DEFAULT_VOICE", "he-IL-AvriNeural"))

    output_base_dir: Path = field(default_factory=lambda: Path(os.getenv("OUTPUT_BASE_DIR", "outputs")))
    intro_music: Path = field(default_factory=lambda: Path(os.getenv("INTRO_MUSIC", "assets/audio/intro.mp3")))
    outro_music: Path = field(default_factory=lambda: Path(os.getenv("OUTRO_MUSIC", "assets/audio/outro.mp3")))
    voice_profile_path: Path = field(
        default_factory=lambda: Path(os.getenv("VOICE_PROFILES_PATH", "config/voice_profiles.json"))
    )
    default_voice_profile: str = field(default_factory=lambda: os.getenv("VOICE_PROFILES_DEFAULT", "classic"))
    target_language: str = field(default_factory=lambda: os.getenv("TARGET_LANGUAGE", ""))
    visual_text_language: str = field(default_factory=lambda: os.getenv("VISUAL_TEXT_LANGUAGE", "en"))

    monthly_tts_character_limit: int = field(default_factory=lambda: int(os.getenv("MONTHLY_TTS_CHARACTER_LIMIT", "500000")))
    monthly_openai_cost_limit: float = field(default_factory=lambda: float(os.getenv("MONTHLY_OPENAI_COST_LIMIT", "200.0")))
    budget_reset_day: int = field(default_factory=lambda: int(os.getenv("BUDGET_RESET_DAY", "1")))
    reset_day_azure: int = field(default_factory=lambda: int(os.getenv("RESET_DAY_AZURE", "14")))
    reset_day_gemini: int = field(default_factory=lambda: int(os.getenv("RESET_DAY_GEMINI", "1")))
    reset_day_elevenlabs: int = field(default_factory=lambda: int(os.getenv("RESET_DAY_ELEVENLABS", "1")))
    google_cloud_project: Optional[str] = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT"))
    google_cloud_location: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))

    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    
    # Google AI Visual Generation settings (Imagen 4 + VEO)
    imagen_model: str = field(default_factory=lambda: os.getenv("IMAGEN_MODEL", "imagen-4.0-generate-001"))
    veo_model: str = field(default_factory=lambda: os.getenv("VEO_MODEL", "veo-2.0-generate-001"))
    visual_generator: str = field(default_factory=lambda: os.getenv("VISUAL_GENERATOR", "manim"))
    # visual_generator options: "manim" | "google_ai" | "hybrid"
    
    # Pipeline monitoring settings
    pipeline_stall_timeout: float = field(
        default_factory=lambda: float(os.getenv("PIPELINE_STALL_TIMEOUT", "300.0"))
    )
    pipeline_heartbeat_interval: float = field(
        default_factory=lambda: float(os.getenv("PIPELINE_HEARTBEAT_INTERVAL", "5.0"))
    )
    
    # ElevenLabs TTS settings
    elevenlabs_api_key: str = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))
    default_tts_provider: str = field(default_factory=lambda: os.getenv("DEFAULT_TTS_PROVIDER", "auto"))
    monthly_elevenlabs_character_limit: int = field(
        default_factory=lambda: int(os.getenv("MONTHLY_ELEVENLABS_CHARACTER_LIMIT", "100000"))
    )
    elevenlabs_voice_overrides: Dict[str, Dict[str, object]] = field(default_factory=dict)
    
    metadata_system_prompt: str = field(
        default_factory=lambda: os.getenv(
            "METADATA_SYSTEM_PROMPT",
            (
                "אתה NotebookLM Assistant שיוצר מטא-דאטה עשיר, בעברית ברורה, "
                "כולל נושא, תאריך, תקציר, מושגים מרכזיים, מעבדות, מקורות, ושאלות לחזרה."
            ),
        )
    )
    
    visual_metadata_system_prompt: str = field(
        default_factory=lambda: os.getenv(
            "VISUAL_METADATA_SYSTEM_PROMPT",
            ""  # Default will be set in code to avoid line length issues in env
        )
    )

    enable_visuals: bool = field(default_factory=lambda: _to_bool(os.getenv("ENABLE_VISUALS"), True))
    cache_dialogues: bool = field(default_factory=lambda: _to_bool(os.getenv("CACHE_DIALOGUES"), True))
    ui_preferences_path: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "UI_CONFIG_PATH",
                os.getenv("CHAT_PREFERENCES_PATH", "config/ui_config.json"),
            )
        )
    )
    chat_font_family: str = field(default_factory=lambda: os.getenv("CHAT_FONT_FAMILY", "Assistant"))
    chat_font_path: str = field(default_factory=lambda: os.getenv("CHAT_FONT_PATH", ""))
    chat_background_path: str = field(default_factory=lambda: os.getenv("CHAT_BACKGROUND_PATH", ""))
    chat_font_size: int = field(default_factory=lambda: int(os.getenv("CHAT_FONT_SIZE", "13")))
    chat_theme: str = field(default_factory=lambda: os.getenv("CHAT_THEME", "dark"))
    chat_banner_brightness: float = field(
        default_factory=lambda: float(os.getenv("CHAT_BANNER_BRIGHTNESS", "0.85"))
    )
    chat_bg_theme: str = field(default_factory=lambda: os.getenv("CHAT_BG_THEME", "midnight"))
    chat_user_bubble: str = field(default_factory=lambda: os.getenv("CHAT_USER_BUBBLE", ""))
    chat_assistant_bubble: str = field(default_factory=lambda: os.getenv("CHAT_ASSISTANT_BUBBLE", ""))
    
    # Video duration settings
    auto_video_duration: bool = field(default_factory=lambda: _to_bool(os.getenv("AUTO_VIDEO_DURATION"), True))
    video_duration_seconds: float = field(
        default_factory=lambda: float(os.getenv("VIDEO_DURATION_SECONDS", "300"))
    )
    
    # Image generation settings
    image_count: int = field(default_factory=lambda: int(os.getenv("IMAGE_COUNT", "5")))

    # UI Geometry settings
    visual_settings_geometry: str = field(default_factory=lambda: "")
    voice_selector_geometry: str = field(default_factory=lambda: "")
    main_window_geometry: str = field(default_factory=lambda: "")
    main_window_state: str = field(default_factory=lambda: "")
    main_splitter_state: str = field(default_factory=lambda: "")
    content_splitter_state: str = field(default_factory=lambda: "")
    right_panel_width: int = field(default_factory=lambda: 520)
    main_splitter_sizes: List[int] = field(default_factory=list)
    content_splitter_sizes: List[int] = field(default_factory=list)
    output_mode: str = field(default_factory=lambda: "video_audio")
    include_visuals: bool = field(default_factory=lambda: True)
    preview_mode: bool = field(default_factory=lambda: _to_bool(os.getenv("PREVIEW_MODE"), False))
    enable_ai_quality_enhancement: bool = field(
        default_factory=lambda: _to_bool(os.getenv("ENABLE_AI_QUALITY_ENHANCEMENT"), False)
    )
    auto_apply_ai_fixes: bool = field(
        default_factory=lambda: _to_bool(os.getenv("AUTO_APPLY_AI_FIXES"), False)
    )

    # Which model writes the scripts: gemini, azure, local, or auto (by the keys present)
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "auto"))
    gemini_text_model: str = field(default_factory=lambda: os.getenv("GEMINI_TEXT_MODEL") or "gemini-3.8-flash")

    # Script engine v2: clean -> lesson profile -> episode plan -> segment writing -> verification
    dialogue_engine: str = field(default_factory=lambda: os.getenv("DIALOGUE_ENGINE", "v2"))
    podcast_format: str = field(default_factory=lambda: os.getenv("PODCAST_FORMAT", "auto"))
    podcast_target_minutes: float = field(
        default_factory=lambda: float(os.getenv("PODCAST_TARGET_MINUTES", "0") or 0)
    )
    script_max_revisions: int = field(default_factory=lambda: int(os.getenv("SCRIPT_MAX_REVISIONS", "1")))
    verify_grounding: bool = field(default_factory=lambda: _to_bool(os.getenv("VERIFY_GROUNDING"), True))
    # Local checker model (Ollama). auto = use it when running, on = require it, off = never
    local_llm_mode: str = field(default_factory=lambda: os.getenv("LOCAL_LLM", "auto"))
    local_llm_base_url: str = field(default_factory=lambda: os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434"))
    local_llm_model: str = field(default_factory=lambda: os.getenv("LOCAL_LLM_MODEL") or "gemma3:12b")
    local_llm_context: int = field(default_factory=lambda: int(os.getenv("LOCAL_LLM_CONTEXT", "12288")))
    # Conversation audio (src/audio/dialogue_tts.py): both hosts rendered together per chunk
    elevenlabs_model: str = field(default_factory=lambda: os.getenv("ELEVENLABS_MODEL") or "eleven_v4")
    elevenlabs_voice_roee: str = field(default_factory=lambda: os.getenv("ELEVENLABS_VOICE_ROEE", ""))
    elevenlabs_voice_noa: str = field(default_factory=lambda: os.getenv("ELEVENLABS_VOICE_NOA", ""))
    gemini_tts_model: str = field(default_factory=lambda: os.getenv("GEMINI_TTS_MODEL") or "gemini-3.8-flash-tts")
    gemini_image_model: str = field(default_factory=lambda: os.getenv("GEMINI_IMAGE_MODEL") or "gemini-3.1-flash-image")
    gemini_voice_roee: str = field(default_factory=lambda: os.getenv("GEMINI_VOICE_ROEE") or "Charon")
    gemini_voice_noa: str = field(default_factory=lambda: os.getenv("GEMINI_VOICE_NOA") or "Aoede")
    # What the listener wants emphasized, like NotebookLM's "Customize" box (set per project)
    podcast_focus: str = ""
    # The podcast's language, independent of the lecture's: Hebrew unless English is chosen
    podcast_language: str = field(default_factory=lambda: (os.getenv("PODCAST_LANGUAGE") or "he").strip().lower())
    # Why default_tts_provider was chosen (see resolve_tts_provider)
    tts_provider_reason: str = ""

    def __post_init__(self) -> None:
        has_azure_openai = bool(self.openai_endpoint and self.openai_api_key and self.openai_deployment)
        self.llm_provider = resolve_llm_provider(self.llm_provider, has_azure_openai, bool(self.gemini_api_key))
        if self.llm_provider == "azure" and not has_azure_openai:
            raise ValueError(
                "No model to write scripts: add a Gemini API key, complete the Azure OpenAI details, or choose the local model."
            )
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("Gemini was chosen to write scripts, but GEMINI_API_KEY is empty.")
        self.default_tts_provider, self.tts_provider_reason = resolve_tts_provider(
            requested=self.default_tts_provider,
            has_azure=self._tts_keys_present("azure"),
            has_elevenlabs=self._tts_keys_present("elevenlabs"),
            saved_preference=read_saved_tts_provider(self.ui_preferences_path),
            has_gemini=self._tts_keys_present("gemini"),
        )
        # Voice keys are checked when audio is produced (tts_ready), not here: transcribing and
        # writing scripts must work before any voice service is set up.
        # Ensure reset day is valid calendar day
        for attr in ("budget_reset_day", "reset_day_azure", "reset_day_gemini", "reset_day_elevenlabs"):
            current = getattr(self, attr, 1) or 1
            setattr(self, attr, max(1, min(31, int(current))))
        self.output_base_dir.mkdir(parents=True, exist_ok=True)
        self._load_ui_preferences()
        self._normalize_imagen_model()

    def tts_ready(self, provider: Optional[str] = None) -> bool:
        """Whether the given (or default) TTS provider has its keys."""
        return self._tts_keys_present(provider or self.default_tts_provider)

    def _tts_keys_present(self, provider: str) -> bool:
        if provider == "azure":
            return bool(self.speech_key and self.speech_region)
        if provider == "elevenlabs":
            return bool(self.elevenlabs_api_key)
        if provider == "gemini":
            return bool(self.gemini_api_key)
        return False

    @classmethod
    def load(cls, dotenv_path: Optional[Path] = None) -> "Settings":
        """Load settings from environment, optionally reading a .env file first."""
        if dotenv_path is None:
            dotenv_path = Path(".") / ".env"
        load_dotenv(dotenv_path=dotenv_path, override=False)
        return cls(
            openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            openai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
            openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION") or "2024-02-01",
            speech_key=os.getenv("AZURE_SPEECH_KEY", ""),
            speech_region=os.getenv("AZURE_SPEECH_REGION", ""),
            speech_endpoint=os.getenv("AZURE_SPEECH_ENDPOINT", ""),
            azure_default_voice=os.getenv("AZURE_DEFAULT_VOICE", "he-IL-AvriNeural"),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            imagen_model=os.getenv("IMAGEN_MODEL", "imagen-4.0-generate-001"),
            veo_model=os.getenv("VEO_MODEL", "veo-2.0-generate-001"),
            visual_generator=os.getenv("VISUAL_GENERATOR", "manim"),
            elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
            default_tts_provider=os.getenv("DEFAULT_TTS_PROVIDER", "auto"),
            google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            google_cloud_location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
            target_language=os.getenv("TARGET_LANGUAGE", ""),
            visual_text_language=os.getenv("VISUAL_TEXT_LANGUAGE", "en"),
            enable_ai_quality_enhancement=_to_bool(os.getenv("ENABLE_AI_QUALITY_ENHANCEMENT"), False),
            auto_apply_ai_fixes=_to_bool(os.getenv("AUTO_APPLY_AI_FIXES"), False),
        )

    def _load_ui_preferences(self) -> None:
        """
        Load persisted UI configuration (fonts, splitter sizes, etc.) from disk.
        
        Uses a primary config file (defaults to config/ui_config.json) and falls back
        to the legacy config/ui_prefs.json if found. Any malformed file is ignored
        so the application can continue with built-in defaults.
        """
        candidates = [self.ui_preferences_path]
        legacy_path = Path("config/ui_prefs.json")
        if legacy_path != self.ui_preferences_path and legacy_path.exists():
            candidates.append(legacy_path)

        primary_path = self.ui_preferences_path
        prefs: Optional[Dict[str, object]] = None
        for path in candidates:
            if not path.exists():
                continue
            try:
                prefs = json.loads(path.read_text(encoding="utf-8"))
                # If we loaded a legacy file, keep saving to the new primary path
                if path != primary_path:
                    print(f"[Settings] Loaded legacy UI config from {path}; will re-save to {primary_path}")
                else:
                    self.ui_preferences_path = path
                break
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[Settings] Warning: UI config at {path} is unreadable ({exc}); using defaults.")
                continue

        if prefs is None:
            return
        
        # Valid values for validation
        VALID_VISUAL_GENERATORS = {"manim", "imagen", "imagen_manim", "veo", "hybrid"}
        # Accept a small, curated set of Imagen models and migrate deprecated ones
        VALID_IMAGEN_MODELS = {
            "imagen-4.0-fast-generate-001",
            "imagen-4.0-generate-001",
            "imagen-4.0-ultra-generate-001",
        }
        VALID_VEO_MODELS = {"veo-2.0-generate-001"}
        VALID_TTS_PROVIDERS = set(TTS_PROVIDERS)
        
        for key in (
            "chat_font_family",
            "chat_font_path",
            "chat_font_size",
            "chat_theme",
            "chat_banner_brightness",
            "chat_background_path",
            "chat_background_mode",
            "chat_background_alignment",
            "chat_bg_theme",
            "chat_user_bubble",
            "chat_assistant_bubble",
            "default_tts_provider",
            "visual_generator",
            "imagen_model",
            "veo_model",
            "auto_video_duration",
            "video_duration_seconds",
            "image_count",
            "visual_settings_geometry",
            "voice_selector_geometry",
            "elevenlabs_voice_overrides",
            "azure_default_voice",
            "metadata_system_prompt",
            "visual_metadata_system_prompt",
            "monthly_openai_cost_limit",
            "monthly_tts_character_limit",
            "monthly_elevenlabs_character_limit",
            "main_window_geometry",
            "main_window_state",
            "right_panel_width",
            "main_splitter_sizes",
            "content_splitter_sizes",
            "output_mode",
            "include_visuals",
            "budget_reset_day",
            "reset_day_azure",
            "reset_day_gemini",
            "reset_day_elevenlabs",
        ):
            if key in prefs:
                value = prefs[key]
                
                # Validate specific fields and migrate invalid values
                if key == "visual_generator" and value not in VALID_VISUAL_GENERATORS:
                    print(f"[Settings] Invalid visual_generator '{value}', using default 'manim'")
                    value = "manim"
                elif key == "imagen_model":
                    if isinstance(value, str) and value.startswith("imagen-3."):
                        print("[Settings] Migrating deprecated Imagen 3 selection to 'imagen-4.0-generate-001'")
                        value = "imagen-4.0-generate-001"
                    if value not in VALID_IMAGEN_MODELS:
                        print(f"[Settings] Invalid imagen_model '{value}', using default 'imagen-4.0-generate-001'")
                        value = "imagen-4.0-generate-001"
                elif key == "veo_model" and value not in VALID_VEO_MODELS:
                    print(f"[Settings] Invalid veo_model '{value}', using default 'veo-2.0-generate-001'")
                    value = "veo-2.0-generate-001"
                elif key == "default_tts_provider" and value not in VALID_TTS_PROVIDERS:
                    print(f"[Settings] Invalid default_tts_provider '{value}', using default 'azure'")
                    value = "azure"
                elif key == "default_tts_provider" and not self._tts_keys_present(value):
                    # A saved choice without keys would fail mid-run; keep the provider resolved from .env
                    print(f"[Settings] Saved TTS provider '{value}' has no keys in .env; using '{self.default_tts_provider}'")
                    value = self.default_tts_provider
                elif key == "image_count":
                    # Ensure image_count is within valid range
                    value = max(1, min(20, int(value) if isinstance(value, (int, float)) else 5))
                elif key in {"budget_reset_day", "reset_day_azure", "reset_day_gemini", "reset_day_elevenlabs"}:
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        value = 1
                    value = max(1, min(31, value))
                elif key == "monthly_openai_cost_limit":
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        value = 200.0
                    value = max(0.01, value)
                elif key == "monthly_tts_character_limit":
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        value = 500000
                    value = max(1000, value)
                elif key == "monthly_elevenlabs_character_limit":
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        value = 100000
                    value = max(1000, value)
                elif key == "chat_font_size":
                    value = max(10, min(28, int(value) if isinstance(value, (int, float, str)) else 13))
                elif key == "chat_banner_brightness":
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        value = 0.85
                    value = max(0.2, min(1.0, value))
                elif key == "elevenlabs_voice_overrides":
                    # Migrate old model names to new Hebrew-compatible model
                    if isinstance(value, dict):
                        migrated = False
                        for speaker, config in value.items():
                            if isinstance(config, dict) and config.get("model") == "eleven_multilingual_v2":
                                config["model"] = "eleven_turbo_v2_5"
                                migrated = True
                        if migrated:
                            print("[Settings] Migrated eleven_multilingual_v2 to eleven_turbo_v2_5 in voice overrides")
                            # Save the migrated values back to file
                            prefs[key] = value
                            try:
                                self.ui_preferences_path.write_text(
                                    json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
                                )
                                print(f"[Settings] Saved migrated preferences to {self.ui_preferences_path}")
                            except Exception as e:
                                print(f"[Settings] Warning: Could not save migrated preferences: {e}")
                
                if key in {"main_splitter_sizes", "content_splitter_sizes"}:
                    if isinstance(value, list):
                        value = [int(v) for v in value if isinstance(v, (int, float))]
                    else:
                        value = []
                if key == "right_panel_width":
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        value = 520
                    value = max(320, min(1200, value))
                if key == "chat_font_size":
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        value = 13
                if key == "chat_banner_brightness":
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        value = 0.85
                if key == "output_mode":
                    if value not in {"audio", "video_audio", "presentation", "all", "mixed"}:
                        print(f"[Settings] Invalid output_mode '{value}', using default 'video_audio'")
                        value = "video_audio"
                    if value == "mixed":
                        value = "video_audio"
                if hasattr(self, key):
                    setattr(self, key, value)
                else:
                    print(f"[Settings] Warning: Ignoring unknown setting key '{key}'")

    def save_ui_preferences(self, payload: Dict[str, object]) -> None:
        allowed = {
            "chat_font_family",
            "chat_font_path",
            "chat_font_size",
            "chat_theme",
            "chat_banner_brightness",
            "chat_background_path",
            "chat_background_mode",
            "chat_background_alignment",
            "chat_bg_theme",
            "chat_user_bubble",
            "chat_assistant_bubble",
            "default_tts_provider",
            "visual_generator",
            "imagen_model",
            "veo_model",
            "auto_video_duration",
            "video_duration_seconds",
            "image_count",
            "visual_settings_geometry",
            "voice_selector_geometry",
            "elevenlabs_voice_overrides",
            "azure_default_voice",
            "visual_metadata_system_prompt",
            "main_window_geometry",
            "main_window_state",
            "right_panel_width",
            "main_splitter_sizes",
            "content_splitter_sizes",
            "output_mode",
            "include_visuals",
            "budget_reset_day",
            "reset_day_azure",
            "reset_day_gemini",
            "reset_day_elevenlabs",
            "monthly_openai_cost_limit",
            "monthly_tts_character_limit",
            "monthly_elevenlabs_character_limit",
            "cost_center_geometry",
            "cost_center_tab_index",
            "cost_center_scroll_pos",
        }
        data = {key: payload[key] for key in payload if key in allowed}
        if not data:
            return
        
        # Log what we're saving
        print(f"[Settings] Saving UI preferences: {list(data.keys())}")
        
        existing: Dict[str, object] = {}
        if self.ui_preferences_path.exists():
            try:
                existing = json.loads(self.ui_preferences_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing.update(data)
        self.ui_preferences_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            self.ui_preferences_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[Settings] Successfully saved to {self.ui_preferences_path}")
        except Exception as e:
            print(f"[Settings] ERROR saving preferences: {e}")
            raise
        
        # Update in-memory settings
        for key, value in data.items():
            setattr(self, key, value)
            print(f"[Settings] Updated in-memory: {key} = {value if key != 'elevenlabs_voice_overrides' else '...'}")

    def _normalize_imagen_model(self) -> None:
        """Force Imagen 4.0 standard model to avoid 404/unsupported variants."""
        target = "imagen-4.0-generate-001"
        current = getattr(self, "imagen_model", target) or target
        normalized = current

        lowered = str(current).lower()
        if lowered.startswith("imagen-3."):
            print(
                f"[Settings] Overriding deprecated Imagen 3 model '{current}' to '{target}'"
            )
            normalized = target

        if normalized not in {
            "imagen-4.0-fast-generate-001",
            "imagen-4.0-generate-001",
            "imagen-4.0-ultra-generate-001",
        }:
            print(f"[Settings] Invalid imagen_model '{current}', using '{target}'")
            normalized = target

        setattr(self, "imagen_model", normalized)
