"""
M.B.S Studio - Cost Tracking
=============================

Tracks usage and costs for AI services including OpenAI,
Azure TTS, and ElevenLabs TTS.

Author: M.B.S Studio
Version: 1.1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict

from src.utils.pricing import PRICING, gemini_tts_cost, llm_token_cost


@dataclass
class CostTracker:
    """
    Track costs for OpenAI tokens and TTS characters.
    
    Supports both Azure Neural TTS and ElevenLabs TTS with
    separate tracking and cost calculation.
    """

    # Cost rates (USD per 1K units)
    openai_prompt_cost_per_1k: float = PRICING.azure_gpt_input_per_1k
    openai_completion_cost_per_1k: float = PRICING.azure_gpt_output_per_1k
    azure_tts_cost_per_1k_chars: float = PRICING.azure_tts_per_1k_chars  # Azure Neural TTS
    elevenlabs_cost_per_1k_chars: float = PRICING.elevenlabs_tts_per_1k_chars  # ElevenLabs standard

    # Usage counters
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tts_characters: int = 0  # Azure TTS characters (for backward compatibility)
    elevenlabs_characters: int = 0  # ElevenLabs TTS characters
    visual_cost_usd: float = 0.0  # Imagen/VEO visual generation cost accumulator
    gemini_tts_seconds: float = 0.0
    # Token usage per model, priced by that model's own rate: {model: [prompt, completion]}
    model_tokens: Dict[str, list] = field(default_factory=dict)

    # Provider tracking
    tts_provider: str = "azure"  # Current TTS provider being used

    def add_openai_usage(self, prompt_tokens: int, completion_tokens: int, model: str = "") -> None:
        """Add LLM token usage. `model` picks the price (Gemini or Azure GPT); thinking tokens count as completion."""
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        counts = self.model_tokens.setdefault(model or "azure", [0, 0])
        counts[0] += prompt_tokens
        counts[1] += completion_tokens

    def add_gemini_tts_seconds(self, seconds: float) -> None:
        self.gemini_tts_seconds += max(0.0, float(seconds))

    def add_tts_characters(self, chars: int) -> None:
        """Add Azure TTS character usage (backward compatible)."""
        self.tts_characters += chars

    def add_elevenlabs_characters(self, chars: int) -> None:
        """Add ElevenLabs TTS character usage."""
        self.elevenlabs_characters += chars

    def add_visual_cost(self, amount: float) -> None:
        """Accumulate visual generation cost (e.g., Imagen/VEO)."""
        try:
            self.visual_cost_usd += float(amount)
        except (TypeError, ValueError):
            # Ignore invalid values to avoid crashing cost tracking
            return

    @property
    def openai_cost(self) -> float:
        """Total LLM cost (the name is historical: it covers Gemini and local models too)."""
        if self.model_tokens:
            return round(sum(llm_token_cost(m, p, c) if m != "azure" else
                             (p / 1000) * self.openai_prompt_cost_per_1k + (c / 1000) * self.openai_completion_cost_per_1k
                             for m, (p, c) in self.model_tokens.items()), 4)
        prompt_cost = (self.prompt_tokens / 1000) * self.openai_prompt_cost_per_1k
        completion_cost = (self.completion_tokens / 1000) * self.openai_completion_cost_per_1k
        return round(prompt_cost + completion_cost, 4)

    @property
    def azure_tts_cost(self) -> float:
        """Calculate Azure TTS cost."""
        return round((self.tts_characters / 1000) * self.azure_tts_cost_per_1k_chars, 4)

    @property
    def elevenlabs_tts_cost(self) -> float:
        """Calculate ElevenLabs TTS cost."""
        return round((self.elevenlabs_characters / 1000) * self.elevenlabs_cost_per_1k_chars, 4)

    @property
    def tts_cost(self) -> float:
        """Calculate total TTS cost (all providers)."""
        return round(self.azure_tts_cost + self.elevenlabs_tts_cost + gemini_tts_cost(self.gemini_tts_seconds), 4)

    @property
    def total_tts_characters(self) -> int:
        """Total TTS characters from all providers."""
        return self.tts_characters + self.elevenlabs_characters

    def as_dict(self) -> Dict[str, float]:
        """
        Export all tracked costs as a dictionary.
        
        Returns:
            Dict with all cost metrics for serialization
        """
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tts_characters": self.tts_characters,  # Azure TTS
            "elevenlabs_characters": self.elevenlabs_characters,
            "total_tts_characters": self.total_tts_characters,
            "openai_cost_usd": self.openai_cost,
            "azure_tts_cost_usd": self.azure_tts_cost,
            "elevenlabs_tts_cost_usd": self.elevenlabs_tts_cost,
            "tts_cost_usd": self.tts_cost,
            "visual_cost_usd": round(self.visual_cost_usd, 4),
            "total_cost_usd": round(self.openai_cost + self.tts_cost + self.visual_cost_usd, 4),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


def get_cycle_start_date(reset_day: int, today: date | None = None) -> datetime:
    """
    Calculate the start date of the current billing cycle (UTC-aware).

    Args:
        reset_day: Day of month the cycle resets (1-31, clamped).
        today: Optional override for today's date (UTC date).

    Returns:
        datetime with tzinfo=UTC representing the start of the active billing cycle at midnight UTC.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    safe_day = max(1, min(31, int(reset_day or 1)))
    if today.day >= safe_day:
        cycle_year, cycle_month = today.year, today.month
    else:
        cycle_year, cycle_month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)

    try:
        import calendar

        start_day = min(safe_day, calendar.monthrange(cycle_year, cycle_month)[1])
    except Exception:
        start_day = safe_day

    return datetime(cycle_year, cycle_month, start_day, tzinfo=timezone.utc)
