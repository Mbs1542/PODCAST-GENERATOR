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



# --------------------------------------------------------------------------- whole-episode estimate

# Gemini's tokenizer spends about 2.4 tokens on a Hebrew word and 1.4 on an English one (estimates)
TOKENS_PER_WORD = {"he": 2.4, "en": 1.4}
SPEECH_WPM = {"he": 135, "en": 150}       # the script engine's own pace
TTS_CHARS_PER_WORD = 6.0                   # letters, the space and punctuation: what voice services bill
ESTIMATE_RANGE = (0.6, 1.8)                # thinking length and revisions vary from lesson to lesson


def estimate_podcast(
    transcript_words: int,
    target_minutes: float,
    *,
    language: str = "he",
    writer_model: str = "gemini-3.8-flash",
    writer: str = "gemini",
    voice: str = "elevenlabs",
    images: int = 0,
    today: Optional[date] = None,
) -> dict:
    """What one episode is likely to cost, before it is made: the script (all engine stages) and the voices.

    The script stages, roughly: cleanup reads the whole transcript and returns fixes; the analysis reads it
    again; the plan reads the analysis; each segment is written from its part of the lecture and the plan,
    then fact-checked against the lecture; a third of the segments are revised; a polish pass reads the
    whole script. Gemini 3 also "thinks" before answering, billed as output. The result is a range.
    """
    language = "en" if language == "en" else "he"
    tpw = TOKENS_PER_WORD[language]
    script_words = max(1.0, float(target_minutes)) * SPEECH_WPM[language]
    lecture, script = transcript_words * tpw, script_words * tpw
    segments = max(3, round(float(target_minutes) / 3.5)) + 2
    prompt = (lecture * 1.15            # cleanup (with instructions)
              + lecture + 2000          # analysis
              + 12000                   # plan
              + lecture + segments * 8000  # writing: each segment's sources and the plan
              + script + lecture        # fact-checking against the lecture
              + script                  # polish
              + (script + lecture) / 3)  # revisions
    completion = (lecture * 0.25 * 1.3  # cleanup: fixes and section marks, a little thinking
                  + 8000 + 6000         # analysis and plan, with thinking
                  + script * 2.2        # writing, with thinking
                  + script * 0.15       # fact-check verdicts
                  + script * 0.3        # polish edits
                  + script / 3 * 2.2)   # revisions
    if writer == "local":
        script_cost = 0.0
    else:
        script_cost = llm_token_cost(writer_model if writer == "gemini" else "gpt", int(prompt), int(completion), today)
    chars = int(script_words * TTS_CHARS_PER_WORD * 1.03)
    seconds = float(target_minutes) * 60
    if voice == "gemini":
        voice_cost = gemini_tts_cost(seconds, today)
    elif voice == "azure":
        voice_cost = tts_cost(chars, "azure")[0]
    else:
        voice_cost = tts_cost(chars, "elevenlabs")[0]
    image_cost = gemini_image_cost(images) if images else 0.0
    low, high = ESTIMATE_RANGE
    return {
        "target_minutes": float(target_minutes),
        "script_words": int(script_words),
        "tokens": {"prompt": int(prompt), "completion": int(completion)},
        "script": {"low": round(script_cost * low, 2), "high": round(script_cost * high, 2), "writer": writer},
        "voice": {"provider": voice, "characters": chars, "cost": round(voice_cost, 2)},
        "images": {"count": images, "cost": round(image_cost, 2)},
        "total": {"low": round(script_cost * low + voice_cost + image_cost, 2),
                  "high": round(script_cost * high + voice_cost + image_cost, 2)},
        "gemini_price_doubles": GEMINI_PRICE_DOUBLING.isoformat(),
    }


def estimate_exam(questions: int, topics: int, source_chars: int, lectures: int = 1,
                  writer_model: str = "gemini-3.8-flash", writer: str = "gemini", today: Optional[date] = None) -> dict:
    """What an exam is likely to cost: writing (one call per up to 5 questions of a topic, with its sources),
    the blind check (one call per topic), rewrites (about a quarter of the questions) and, for several
    lectures, the topic merge. Gemini thinking is billed as output. The result is a range."""
    calls = max(1, topics) + questions // 5
    sources = min(source_chars, 9000 * max(1, topics)) / 2.6  # Hebrew: about 2.6 characters per token
    per_call_sources = sources / max(1, topics)
    prompt = calls * (2500 + per_call_sources) + topics * (per_call_sources + 300 * 5) + questions * 0.25 * (2500 + per_call_sources)
    completion = questions * 650 * 2.2 + topics * 900 + questions * 0.25 * 900 * 2.2
    if lectures > 1:
        prompt += 1500 + 80 * topics
        completion += 3000
    cost = 0.0 if writer == "local" else llm_token_cost(writer_model if writer == "gemini" else "gpt", int(prompt), int(completion), today)
    low, high = ESTIMATE_RANGE
    return {"questions": questions, "low": round(cost * low, 3), "high": round(cost * high, 3),
            "tokens": {"prompt": int(prompt), "completion": int(completion)}}
