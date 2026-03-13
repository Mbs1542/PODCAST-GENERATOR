"""
M.B.S Studio - Application Constants
=====================================

Central repository for application-wide constants, cost parameters,
and configuration values used throughout the GUI.

Author: M.B.S Studio
Version: 1.0.0
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from src.utils.pricing import PRICING

# =============================================================================
# Application Identity
# =============================================================================

APP_VERSION = "3.4.6"
"""Current application version."""

APP_BUILD_DATE = "2025-12-27"
"""Build date for this version (v3.4.6)."""

APP_DISPLAY_NAME = "M.B.S Studio"
"""The display name shown in window titles and UI elements."""

# Shared QSettings identity
QSETTINGS_ORG = "M.B.S Studio"
QSETTINGS_APP = "Podcast Generator"

APP_ASSISTANT_NAME = "M.B.S"
"""The name used for the AI assistant in chat conversations."""

APP_LOGO_PATH = Path(r"C:\Users\maorb\OneDrive\Desktop\PODCAST GENERATOR\LOGO.JPEG")
"""Path to the application logo file."""

FONT_LIBRARY_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
"""Directory containing custom fonts for the application."""

# =============================================================================
# System Font Configuration
# =============================================================================

SYSTEM_CHAT_FONTS = [
    "Assistant",
    "Segoe UI",
    "Noto Sans Hebrew",
    "Alef",
    "Open Sans",
    "Rubik",
    "Calibri",
    "Frank Ruehl",
    "David",
]
"""List of system fonts available for chat display, ordered by preference."""

# =============================================================================
# Cost Calculation Constants
# =============================================================================

WORDS_PER_MINUTE = PRICING.words_per_minute
"""Average words spoken per minute for duration estimation."""

TOKENS_PER_WORD = PRICING.tokens_per_word
"""Average tokens per word for Hebrew/English mixed content."""

OUTPUT_TOKEN_RATIO = PRICING.output_token_ratio
"""Ratio of output tokens to input tokens for cost estimation."""

CHARS_PER_WORD = PRICING.chars_per_word
"""Average characters per word for TTS cost calculation."""

# Azure OpenAI Pricing (per 1K tokens)
AZURE_GPT_INPUT_COST = PRICING.azure_gpt_input_per_1k
"""Azure GPT-4o input cost per 1,000 tokens (USD)."""

AZURE_GPT_OUTPUT_COST = PRICING.azure_gpt_output_per_1k
"""Azure GPT-4o output cost per 1,000 tokens (USD)."""

# Google Gemini Pricing (per 1K tokens)
GEMINI_INPUT_COST = PRICING.gemini_input_per_1k
"""Gemini Flash input cost per 1,000 tokens (USD)."""

GEMINI_OUTPUT_COST = PRICING.gemini_output_per_1k
"""Gemini Flash output cost per 1,000 tokens (USD)."""

# Azure Neural TTS Pricing
TTS_COST_PER_MILLION = PRICING.azure_tts_per_1k_chars * 1000
"""Azure Neural TTS cost per 1,000,000 characters (USD)."""

AZURE_TTS_COST_PER_THOUSAND = PRICING.azure_tts_per_1k_chars
"""Azure Neural TTS cost per 1,000 characters (USD)."""

# ElevenLabs TTS Pricing
ELEVENLABS_COST_PER_THOUSAND = PRICING.elevenlabs_tts_per_1k_chars
"""ElevenLabs standard cost per 1,000 characters (USD)."""

ELEVENLABS_TURBO_COST_PER_THOUSAND = 0.18
"""ElevenLabs Turbo model cost per 1,000 characters (USD)."""

ELEVENLABS_COST_PER_MILLION = ELEVENLABS_COST_PER_THOUSAND * 1000
"""ElevenLabs cost per 1,000,000 characters (USD)."""

# Google AI Visual Generation Pricing (Imagen 4 + VEO)
IMAGEN_COST_PER_IMAGE = PRICING.imagen_per_image
"""Imagen 4 Standard cost per generated image (USD)."""

IMAGEN_ULTRA_COST_PER_IMAGE = 0.06
"""Imagen 4 Ultra cost per generated image (USD)."""

IMAGEN_FAST_COST_PER_IMAGE = 0.03
"""Imagen 4 Fast cost per generated image (USD)."""

VEO_COST_PER_SECOND = PRICING.veo_per_second
"""VEO video generation cost per second of output (USD)."""

# Additional Production Costs
VIDEO_OVERHEAD_COST = PRICING.video_overhead
"""Estimated overhead cost for video rendering (USD)."""

PPT_EXTRA_COST = PRICING.ppt_extra
"""Estimated cost for PPTX/Story generation (USD)."""

GOOGLE_AI_OVERHEAD_COST = 0.10
"""Estimated overhead for Google AI API calls (USD)."""

# =============================================================================
# Visual Generator Configuration
# =============================================================================

VISUAL_GENERATORS = {
    "manim": {
        "name": "Manim (אנימציות מקומיות)",
        "description": "יצירת אנימציות מתמטיות באמצעות Manim Community - חינם!",
        "requires_api": False,
        "supports_images": False,
        "supports_video": True,
        "enabled": True,
    },
    "imagen": {
        "name": "Imagen 4 (תמונות בלבד)",
        "description": "יצירת תמונות סטטיות באיכות גבוהה באמצעות Google AI",
        "requires_api": True,
        "supports_images": True,
        "supports_video": False,
        "enabled": True,
    },
    "imagen_manim": {
        "name": "Imagen + Manim (תמונות + אנימציות) ⭐",
        "description": "תמונות Google AI עם אנימציות Manim - שילוב מומלץ!",
        "requires_api": True,
        "supports_images": True,
        "supports_video": True,
        "enabled": True,
    },
    "veo": {
        "name": "VEO (וידאו AI בלבד)",
        "description": "יצירת קליפים קצרים עם אנימציות AI - יקר יותר ($0.75/שנייה)",
        "requires_api": True,
        "supports_images": False,
        "supports_video": True,
        "enabled": True,
    },
    "hybrid": {
        "name": "היברידי מלא (Manim + Imagen + VEO) 💎",
        "description": "שילוב מלא: תמונות + אנימציות + וידאו AI - הכי מקיף ויקר",
        "requires_api": True,
        "supports_images": True,
        "supports_video": True,
        "enabled": True,
    },
}
"""Available visual generation backends with capabilities."""

IMAGEN_MODELS = {
    "imagen-4.0-fast-generate-001": {
        "name": "Imagen 4 Fast",
        "description": "גרסה מהירה של Imagen 4 לתצוגות מקדימות",
        "cost_per_image": IMAGEN_FAST_COST_PER_IMAGE,
    },
    "imagen-4.0-generate-001": {
        "name": "Imagen 4 (סטנדרטי) ⭐",
        "description": "מודל Imagen 4 הסטנדרטי והמאוזן",
        "cost_per_image": IMAGEN_COST_PER_IMAGE,
    },
    "imagen-4.0-ultra-generate-001": {
        "name": "Imagen 4 Ultra (איכות גבוהה)",
        "description": "איכות הגבוהה ביותר של Imagen 4",
        "cost_per_image": IMAGEN_ULTRA_COST_PER_IMAGE,
    },
}
"""Available Imagen 4 models with pricing (via google-generativeai SDK)."""

VEO_MODELS = {
    "veo-2.0-generate-001": {
        "name": "VEO 2.0",
        "description": "יצירת סרטונים עם AI",
        "cost_per_second": 0.75,
    },
}
"""Available VEO video generation models."""

# =============================================================================
# Pipeline Stage Definitions
# =============================================================================

# Base pipeline stages with dynamic weights
PIPELINE_STAGES_BASE = [
    {"keyword": "Pipeline execution started", "label": "מאתחל Pipeline", "icon": "🚀", "weight": 1, "dynamic": False, "auto_advance": True},
    {"keyword": "Starting metadata ingestion", "label": "מטעין מטא-דאטה וחומרים", "icon": "🧠", "weight": 8, "dynamic": False},
    {"keyword": "Starting dialogue generation", "label": "מייצר דיאלוג חכם", "icon": "💬", "weight": 15, "dynamic": False},
    {"keyword": "Starting speech synthesis", "label": "מסנתז קולות", "icon": "🎤", "weight": 30, "dynamic": True, "type": "tts"},
    {"keyword": "Speech synthesis completed", "label": "קולות מוכנים", "icon": "✅", "weight": 5, "dynamic": False},
    {"keyword": "Starting audio stitching", "label": "מלחין אודיו", "icon": "🎵", "weight": 10, "dynamic": False},
    {"keyword": "Audio stitching complete", "label": "Mixdown סופי", "icon": "🎧", "weight": 5, "dynamic": False},
    {"keyword": "Generating visual metadata", "label": "יוצר מטא-דאטה ויזואלי", "icon": "🖼️", "weight": 8, "dynamic": False},
    {"keyword": "Generating AI visuals", "label": "מייצר ויזואליים AI", "icon": "🎨", "weight": 10, "dynamic": True, "type": "visuals"},
    {"keyword": "Running post-processing quality checks", "label": "בודק איכות סופית", "icon": "✅", "weight": 6, "dynamic": False},
    {"keyword": "Slide deck exported", "label": "מייצא מצגת", "icon": "📊", "weight": 2, "dynamic": False},
    {"keyword": "Starting video composition", "label": "מרכיב וידאו", "icon": "🎬", "weight": 20, "dynamic": True, "type": "video"},
    {"keyword": "Video composition completed", "label": "וידאו הושלם", "icon": "🎥", "weight": 1, "dynamic": False},
    {"keyword": "Pipeline execution finished", "label": "Pipeline הסתיים", "icon": "🏁", "weight": 1, "dynamic": False},
]

def calculate_adaptive_weights(content_type: str, transcript_length: int = 0,
                              image_count: int = 0, dialogue_entries: int = 0) -> dict:
    """
    Calculate truly adaptive weights based on content type and characteristics.
    """
    # Base weights
    base_weights = {
        "init": 1,
        "dialogue": 15,
        "tts": 35,
        "tts_completion": 5,
        "audio_stitch": 10,
        "audio_finalize": 5,
        "visuals": 12,
        "ppt_export": 2,
        "video": 18,  # Changed from video_compose to match pipeline stages
        "final": 1
    }

    # Content type adjustments
    content_multipliers = {
        "technical_programming": {
            "dialogue": 1.2,  # More explanation needed
            "visuals": 1.1,   # Code screenshots and diagrams
            "tts": 1.3        # Complex terminology
        },

        "technical_networking": {
            "dialogue": 1.1,  # Architecture explanations
            "visuals": 1.2,   # Network diagrams
            "video": 1.1  # Complex visualizations
        },

        "business_professional": {
            "dialogue": 1.0,  # Standard business explanations
            "visuals": 1.0,   # Charts and presentations
            "tts": 0.9        # Familiar business terms
        },

        "science_medical": {
            "dialogue": 1.3,  # Detailed scientific explanations
            "visuals": 1.4,   # Complex diagrams and illustrations
            "tts": 1.2        # Technical medical terminology
        },

        "creative_artistic": {
            "dialogue": 0.9,  # More inspirational
            "visuals": 1.5,   # Artistic creations
            "video": 1.3  # Creative animations
        },

        "educational_general": {
            "dialogue": 1.0,  # Standard educational content
            "visuals": 1.0,   # Standard educational visuals
            "tts": 1.0        # Standard terminology
        }
    }

    # Apply content type multipliers
    multipliers = content_multipliers.get(content_type, content_multipliers["educational_general"])
    for stage, multiplier in multipliers.items():
        if stage in base_weights:
            base_weights[stage] *= multiplier

    # Dynamic TTS adjustment based on content
    if dialogue_entries > 0:
        # Estimate time per dialogue entry based on content complexity
        complexity_multiplier = {"high": 1.5, "medium": 1.0, "low": 0.8}
        complexity = get_content_characteristics(content_type).get("complexity", "medium")

        estimated_tts = dialogue_entries * 3 * complexity_multiplier.get(complexity, 1.0)
        base_weights["tts"] = min(85, max(25, estimated_tts / 2))

    # Visual adjustment based on content needs
    if image_count > 0:
        visual_intensity = {
            "technical_programming": 1.3,
            "science_medical": 1.4,
            "creative_artistic": 1.5
        }.get(content_type, 1.0)

        base_weights["visuals"] = min(35, base_weights["visuals"] * visual_intensity)

    return base_weights


def get_content_characteristics(content_type: str) -> Dict:
    """Get content characteristics for processing decisions."""
    characteristics = {
        "technical_programming": {
            "complexity": "high",
            "dialogue_style": "methodical",
            "visual_needs": "diagrams_code",
            "pace": "slow_detailed"
        },

        "technical_networking": {
            "complexity": "medium",
            "dialogue_style": "systematic",
            "visual_needs": "diagrams_flows",
            "pace": "structured"
        },

        "business_professional": {
            "complexity": "medium",
            "dialogue_style": "strategic",
            "visual_needs": "charts_presentations",
            "pace": "deliberate"
        },

        "science_medical": {
            "complexity": "high",
            "dialogue_style": "methodical",
            "visual_needs": "scientific_illustrations",
            "pace": "slow_detailed"
        },

        "creative_artistic": {
            "complexity": "medium",
            "dialogue_style": "inspirational",
            "visual_needs": "artistic_creations",
            "pace": "creative_flow"
        },

        "educational_general": {
            "complexity": "low",
            "dialogue_style": "clear",
            "visual_needs": "infographics",
            "pace": "balanced"
        }
    }

    return characteristics.get(content_type, characteristics["educational_general"])


# Backward compatibility - keep old function but route to new one
def calculate_dynamic_weights(transcript_length: int = 0, image_count: int = 0, dialogue_entries: int = 0) -> list:
    """
    Legacy function for backward compatibility.
    Now uses content_type-aware adaptive weights with default content type.
    """
    # Use adaptive weights with default educational_general content type
    adaptive_weights = calculate_adaptive_weights("educational_general", transcript_length, image_count, dialogue_entries)

    # Convert to old format (list of stage dicts)
    stages = PIPELINE_STAGES_BASE.copy()
    for stage in stages:
        stage_type = stage.get("type", "")
        if stage_type in adaptive_weights:
            stage["weight"] = int(adaptive_weights[stage_type])

    return stages

def load_performance_history() -> dict:
    """
    Load historical performance data for ETA improvements.

    Returns:
        Dict with historical averages for different pipeline stages
    """
    import json
    from pathlib import Path

    history_file = Path("outputs/performance_history.json")

    if not history_file.exists():
        return {}

    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('averages', {})
    except Exception:
        return {}

def save_performance_data(stage_times: dict, content_stats: dict) -> None:
    """
    Save performance data for future ETA improvements.

    Args:
        stage_times: Dict of stage names to execution times
        content_stats: Dict with content statistics (transcript_length, dialogue_entries, etc.)
    """
    import json
    from pathlib import Path
    from datetime import datetime

    history_file = Path("outputs/performance_history.json")

    # Load existing data
    if history_file.exists():
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except:
            data = {"runs": [], "averages": {}}
    else:
        data = {"runs": [], "averages": {}}

    # Add new run data
    run_data = {
        "timestamp": datetime.now().isoformat(),
        "stage_times": stage_times,
        "content_stats": content_stats
    }
    data["runs"].append(run_data)

    # Keep only last 50 runs
    data["runs"] = data["runs"][-50:]

    # Calculate new averages
    if len(data["runs"]) >= 3:  # Need minimum data for meaningful averages
        _calculate_averages(data)

    # Save updated data
    history_file.parent.mkdir(exist_ok=True)
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _calculate_averages(data: dict) -> None:
    """Calculate rolling averages from performance history."""
    runs = data["runs"]
    averages = {}

    # Calculate TTS performance
    tts_times = []
    tts_entries = []
    for run in runs:
        if "Speech synthesis completed" in run["stage_times"]:
            start_time = run["stage_times"].get("Starting speech synthesis", 0)
            end_time = run["stage_times"].get("Speech synthesis completed", 0)
            if end_time > start_time:
                tts_time = end_time - start_time
                tts_times.append(tts_time)

                entries = run["content_stats"].get("dialogue_entries", 0)
                if entries > 0:
                    tts_entries.append(entries)

    if tts_times and tts_entries:
        # Average TTS time per dialogue entry
        total_tts = sum(tts_times)
        total_entries = sum(tts_entries)
        if total_entries > 0:
            averages["tts_avg_per_entry"] = total_tts / total_entries

    # Calculate visual generation performance
    visual_times = []
    visual_counts = []
    for run in runs:
        if "Generating AI visuals" in run["stage_times"]:
            visual_time = run["stage_times"]["Generating AI visuals"]
            visual_times.append(visual_time)

            img_count = run["content_stats"].get("image_count", 0)
            if img_count > 0:
                visual_counts.append(img_count)

    if visual_times and visual_counts:
        total_visual = sum(visual_times)
        total_images = sum(visual_counts)
        if total_images > 0:
            averages["visual_avg_per_image"] = total_visual / total_images

    # Calculate video composition average
    video_times = []
    for run in runs:
        if "Video composition completed" in run["stage_times"]:
            start_time = run["stage_times"].get("Starting video composition", 0)
            end_time = run["stage_times"].get("Video composition completed", 0)
            if end_time > start_time:
                video_times.append(end_time - start_time)

    if video_times:
        averages["video_avg_time"] = sum(video_times) / len(video_times)

    data["averages"] = averages

# Default static weights for backward compatibility
PIPELINE_STAGES = PIPELINE_STAGES_BASE
"""
Pipeline execution stages for progress tracking.

Each stage contains:
- keyword: String to match in log output
- label: Hebrew label for UI display
- icon: Emoji icon for visual feedback
- weight: Relative time weight for ETA calculation (higher = longer)
"""

# =============================================================================
# UI Theme Colors
# =============================================================================

THEME_COLORS = {
    "dark": {
        "background": "#020617",
        "surface": "#0f172a",
        "border": "#1e293b",
        "text_primary": "#e2e8f0",
        "text_secondary": "#94a3b8",
        "accent_primary": "#dc2626",
        "accent_secondary": "#1d4ed8",
        "success": "#22c55e",
        "warning": "#f97316",
        "info": "#38bdf8",
    },
    "light": {
        "background": "#f8fafc",
        "surface": "#ffffff",
        "border": "#e2e8f0",
        "text_primary": "#0f172a",
        "text_secondary": "#64748b",
        "accent_primary": "#dc2626",
        "accent_secondary": "#2563eb",
        "success": "#16a34a",
        "warning": "#ea580c",
        "info": "#0284c7",
    },
}
"""Color palettes for dark and light themes."""

# =============================================================================
# Chat Background Themes (Preset Colors)
# =============================================================================

CHAT_THEMES = {
    "midnight": {
        "name": "חצות כחולה",
        "background": "#0b1122",
        "gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0b1122, stop:1 #1e3a5f)",
    },
    "ocean": {
        "name": "אוקיינוס",
        "background": "#0c4a6e",
        "gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0c4a6e, stop:1 #164e63)",
    },
    "forest": {
        "name": "יער",
        "background": "#14532d",
        "gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #14532d, stop:1 #166534)",
    },
    "sunset": {
        "name": "שקיעה",
        "background": "#7c2d12",
        "gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7c2d12, stop:1 #9a3412)",
    },
    "purple": {
        "name": "סגול",
        "background": "#581c87",
        "gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #581c87, stop:1 #6b21a8)",
    },
    "slate": {
        "name": "אפור כהה",
        "background": "#1e293b",
        "gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1e293b, stop:1 #334155)",
    },
}
"""Preset color themes for chat background."""

# =============================================================================
# Default Settings
# =============================================================================

DEFAULT_CHAT_FONT_SIZE = 13
"""Default font size for chat display."""

DEFAULT_CHAT_THEME = "dark"
"""Default color theme for chat display."""

DEFAULT_BRIGHTNESS = 0.85
"""Default brightness value for chat background (0.2 - 1.0)."""

LOG_BUFFER_MAX_SIZE = 2000
"""Maximum number of log lines to keep in buffer."""

# Budget & cost tracking defaults
DEFAULT_BUDGET_RESET_DAY = 1
"""Default monthly budget reset day (1 = first day of month)."""

