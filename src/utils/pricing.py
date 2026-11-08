from __future__ import annotations

"""
Centralized pricing helper for AI, TTS, and visual generation.

Keep all rates and shared calculation helpers here so UI (CostCenterDialog)
and backend trackers (CostTracker) stay in sync.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional, Tuple


TTSProvider = Literal["azure", "elevenlabs"]
AIProvider = Literal["azure", "gemini"]
VisualProvider = Literal["manim", "imagen", "veo", "hybrid"]


@dataclass(frozen=True)
class PricingRates:
    words_per_minute: int = 150
    tokens_per_word: float = 1.3
    output_token_ratio: float = 0.6
    chars_per_word: float = 4.5

    # Token pricing (per 1K tokens)
    azure_gpt_input_per_1k: float = 0.005
    azure_gpt_output_per_1k: float = 0.015
    gemini_input_per_1k: float = 0.00035
    gemini_output_per_1k: float = 0.00105

    # TTS pricing (per 1K characters); ElevenLabs v3/v4 API price (elevenlabs.io/pricing/api, Nov 2026)
    azure_tts_per_1k_chars: float = 0.016
    elevenlabs_tts_per_1k_chars: float = 0.08

    # Visual pricing
    imagen_per_image: float = 0.04
    veo_per_second: float = 0.75

    # Production overheads
    video_overhead: float = 0.25
    ppt_extra: float = 0.08


PRICING = PricingRates()

# Gemini paid-tier prices, USD per 1M tokens (ai.google.dev/gemini-api/docs/pricing, Nov 2026).
# Introductory prices hold through 2026-12-31; Google doubles them from 2027-01-01.
# Thinking tokens are billed as output.
GEMINI_PRICE_DOUBLING = date(2027, 1, 1)
GEMINI_TEXT_PER_1M = {  # model prefix: (input, output) at the 2026 price
    "gemini-3.8-flash": (0.75, 3.75),
}
GEMINI_TTS_PER_SECOND = 0.000225   # gemini-3.8-flash-tts: $0.00225 per 10 s of audio (2026)
GEMINI_IMAGE_PER_IMAGE = 0.067     # gemini-3.1-flash-image at 1K resolution


def _gemini_factor(today: Optional[date] = None) -> float:
    return 2.0 if (today or date.today()) >= GEMINI_PRICE_DOUBLING else 1.0


def llm_token_cost(model: str, prompt_tokens: int, completion_tokens: int, today: Optional[date] = None) -> float:
    """USD for one model's tokens. Unknown Gemini models are priced like gemini-3.8-flash (an estimate);
    anything else at the Azure GPT rates."""
    model = (model or "").lower()
    if model.startswith("gemini"):
        rate_in, rate_out = next((r for prefix, r in GEMINI_TEXT_PER_1M.items() if model.startswith(prefix)),
                                 GEMINI_TEXT_PER_1M["gemini-3.8-flash"])
        factor = _gemini_factor(today)
        return (prompt_tokens * rate_in + completion_tokens * rate_out) * factor / 1_000_000
    return (prompt_tokens / 1000) * PRICING.azure_gpt_input_per_1k + (completion_tokens / 1000) * PRICING.azure_gpt_output_per_1k


def gemini_tts_cost(seconds: float, today: Optional[date] = None) -> float:
    return seconds * GEMINI_TTS_PER_SECOND * _gemini_factor(today)


def gemini_image_cost(images: int) -> float:
    return images * GEMINI_IMAGE_PER_IMAGE


def gpt_cost(tokens: float, output_tokens: float, provider: AIProvider) -> float:
    """Return cost for given token counts and provider."""
    if provider == "gemini":
        return (tokens / 1000) * PRICING.gemini_input_per_1k + (output_tokens / 1000) * PRICING.gemini_output_per_1k
    return (tokens / 1000) * PRICING.azure_gpt_input_per_1k + (output_tokens / 1000) * PRICING.azure_gpt_output_per_1k


def tts_cost(chars: float, provider: TTSProvider) -> Tuple[float, float]:
    """
    Return (cost, rate_used) for TTS provider.
    Rate is per 1K characters.
    """
    if provider == "elevenlabs":
        rate = PRICING.elevenlabs_tts_per_1k_chars
    else:
        rate = PRICING.azure_tts_per_1k_chars
    cost = (chars / 1000) * rate
    return cost, rate


def visual_cost(visual_gen: VisualProvider, visual_count: int) -> float:
    """Return cost for visual generation selection."""
    if visual_gen == "imagen":
        return max(0, visual_count) * PRICING.imagen_per_image
    if visual_gen == "veo":
        return max(0, visual_count) * PRICING.veo_per_second
    if visual_gen == "hybrid":
        count = max(0, visual_count)
        imagen_count = count // 2
        veo_secs = count - imagen_count
        return (imagen_count * PRICING.imagen_per_image) + (veo_secs * PRICING.veo_per_second)
    return 0.0


def azure_vs_elevenlabs_diff(chars: float) -> float:
    """
    Difference in cost when choosing ElevenLabs over Azure for given characters.
    Positive => ElevenLabs more expensive.
    """
    eleven_cost, _ = tts_cost(chars, "elevenlabs")
    azure_cost, _ = tts_cost(chars, "azure")
    return eleven_cost - azure_cost

