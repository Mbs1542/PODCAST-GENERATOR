"""Gemini text generation through the google-genai SDK.

Replaces google.generativeai, which Google deprecated and whose gRPC extension is
blocked by Windows Smart App Control. Keeps the small interface the app used:
GeminiModel(key, name).generate_content(prompt, generation_config={...}).text
"""

from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
# 1.x/2.0 are shut down and 2.5 is deprecated (Dec 2026); settings saved by older versions still name them
_RETIRED_PREFIXES = ("gemini-1.0", "gemini-1.5", "gemini-pro", "gemini-2.0", "gemini-2.5")


def resolve_model_name(name: Optional[str]) -> str:
    name = (name or "").strip()
    if not name or name.startswith(_RETIRED_PREFIXES):
        return DEFAULT_GEMINI_MODEL
    return name


class GeminiModel:
    def __init__(self, api_key: str, model_name: Optional[str] = None) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model_name = resolve_model_name(model_name)

    def generate_content(self, prompt: str, generation_config: Optional[Dict[str, Any]] = None) -> Any:
        """Return the SDK response; `.text` holds the reply, `.prompt_feedback` any block reason."""
        from google.genai import types

        config = types.GenerateContentConfig(**(generation_config or {}))
        return self._client.models.generate_content(model=self.model_name, contents=prompt, config=config)
