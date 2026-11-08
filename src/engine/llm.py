"""LLM access for the script engine.

Two roles:
- cloud: the strong model (Azure OpenAI deployment) that analyses the lesson and writes the script.
- checker: a cheap model for cleanup and verification. A local Ollama model when one is
  running, otherwise the cloud model.

The local model is called through Ollama's native API rather than its OpenAI-compatible
one, because only the native API lets us set the context window; Ollama's default
window is a few thousand tokens and silently truncates long prompts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from openai import APIConnectionError, APITimeoutError, AzureOpenAI, InternalServerError, RateLimitError
from tenacity import retry, retry_if_exception, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..utils.logging import get_logger

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


class LLMError(RuntimeError):
    """Raised when a model call fails or its reply cannot be used."""


def parse_json_reply(text: Optional[str]) -> Dict[str, Any]:
    """Parse a model reply as a JSON object, tolerating code fences and stray text around it."""
    if not text or not text.strip():
        raise LLMError("Model returned an empty reply.")
    cleaned = text.strip()
    fence = _FENCE_RE.match(cleaned)
    if fence:
        cleaned = fence.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"Reply is not JSON: {cleaned[:120]!r}")
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"Reply is not valid JSON: {exc}") from exc
    if isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
        # Models sometimes return the bare list of turns instead of {"turns": [...]}
        return {"turns": data}
    if not isinstance(data, dict):
        raise LLMError("Expected a JSON object at the top level.")
    return data


@dataclass
class LLMClient:
    """A chat model that answers in JSON. Subclasses implement `_complete`."""

    model: str
    name: str

    def __post_init__(self) -> None:
        self.logger = get_logger(f"LLM[{self.name}]")

    def chat_json(
        self,
        task: str,
        system: str,
        user: str,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """Run one chat turn and return the reply as a JSON object.

        `task` names the call for logs (and lets test doubles route replies).
        A malformed reply gets one repair attempt before LLMError is raised.
        """
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Optional[LLMError] = None
        for _ in range(2):
            reply, truncated = self._complete(messages, max_tokens, temperature, task)
            try:
                return parse_json_reply(reply)
            except LLMError as exc:
                last_error = exc
                if truncated:
                    raise LLMError(f"{task}: reply was cut off at max_tokens={max_tokens}.") from exc
                self.logger.warning("%s: malformed JSON, asking for a repair (%s)", task, exc)
                messages = messages + [
                    {"role": "assistant", "content": (reply or "")[:4000]},
                    {"role": "user", "content": "That was not valid JSON. Reply again with only the JSON object."},
                ]
        raise LLMError(f"{task}: {last_error}")

    def _complete(self, messages: List[Dict[str, str]], max_tokens: int, temperature: float, task: str = "") -> Tuple[str, bool]:
        """Return (reply text, whether it was cut off by max_tokens)."""
        raise NotImplementedError


@dataclass
class OpenAIChatClient(LLMClient):
    """Azure OpenAI (or any OpenAI-compatible) chat deployment."""

    client: Any = None
    cost_tracker: Optional[Any] = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)),
        reraise=True,
    )
    def _complete(self, messages: List[Dict[str, str]], max_tokens: int, temperature: float, task: str = "") -> Tuple[str, bool]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        usage = getattr(response, "usage", None)
        if usage and self.cost_tracker:
            self.cost_tracker.add_openai_usage(usage.prompt_tokens, usage.completion_tokens, model="azure")
        choice = response.choices[0]
        return choice.message.content or "", getattr(choice, "finish_reason", None) == "length"


@dataclass
class OllamaChatClient(LLMClient):
    """A local model served by Ollama (native /api/chat)."""

    base_url: str = "http://localhost:11434"
    num_ctx: int = 12288
    timeout: float = 600.0

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        reraise=True,
    )
    def _complete(self, messages: List[Dict[str, str]], max_tokens: int, temperature: float, task: str = "") -> Tuple[str, bool]:
        response = requests.post(
            f"{self.base_url.rstrip('/')}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": self.num_ctx},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", ""), data.get("done_reason") == "length"


def is_daily_quota_error(exc: BaseException) -> bool:
    """A used-up daily quota (free tier: a few dozen requests per model per day) does not
    come back in seconds; retrying only wastes minutes before the same failure."""
    text = str(exc)
    return getattr(exc, "code", None) == 429 and ("PerDay" in text or "per day" in text.lower())


def _gemini_transient(exc: BaseException) -> bool:
    """Rate limits, server errors and dropped connections (a flaky hotspot, DNS hiccups) are worth retrying."""
    if is_daily_quota_error(exc):
        return False
    if getattr(exc, "code", None) in (429, 500, 502, 503, 504):
        return True
    try:
        import httpx

        if isinstance(exc, httpx.TransportError):
            return True
    except ImportError:
        pass
    return isinstance(exc, (requests.ConnectionError, ConnectionError, TimeoutError, OSError))


# Cheap mechanical tasks do not need the model's deeper thinking; it only costs time.
# Analysis, planning and writing keep the model's default.
_GEMINI_LOW_THINKING_TASKS = {"clean", "verify", "eval_outline", "eval_part"}


@dataclass
class GeminiChatClient(LLMClient):
    """A Gemini text model through the google-genai SDK, answering in JSON."""

    api_key: str = ""
    cost_tracker: Optional[Any] = None
    client: Any = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.client is None:
            from google import genai

            self.client = genai.Client(api_key=self.api_key)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=3, min=4, max=90),
        retry=retry_if_exception(_gemini_transient),
        reraise=True,
    )
    def _complete(self, messages: List[Dict[str, str]], max_tokens: int, temperature: float, task: str = "") -> Tuple[str, bool]:
        from google.genai import types

        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=m["content"])])
            for m in messages if m["role"] != "system"
        ]
        # Gemini 3 models think before answering and the thinking counts toward the output
        # limit, so the budget is widened well beyond the visible answer's size
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system or None,
                temperature=temperature,
                max_output_tokens=min(65_536, max_tokens * 3 + 4096),
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_level="LOW") if task in _GEMINI_LOW_THINKING_TASKS else None,
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        if usage and self.cost_tracker:
            # Thinking tokens are billed as output but are not part of candidates_token_count
            output = (usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)
            self.cost_tracker.add_openai_usage(usage.prompt_token_count or 0, output, model=self.model)
        candidates = getattr(response, "candidates", None) or []
        finish = str(getattr(candidates[0], "finish_reason", "")) if candidates else ""
        return response.text or "", finish.endswith("MAX_TOKENS")


@dataclass
class EngineLLMs:
    """The models one engine run uses."""

    cloud: LLMClient
    local: Optional[LLMClient] = None

    @property
    def checker(self) -> LLMClient:
        return self.local or self.cloud


def build_cloud_llm(settings: Any, cost_tracker: Optional[Any] = None, model: str = "") -> LLMClient:
    """The model that analyses lessons and writes scripts: Gemini, Azure OpenAI or the local model.

    `model` overrides the configured model or deployment (used to pick a stronger critic).
    """
    provider = str(getattr(settings, "llm_provider", "azure") or "azure")
    if provider == "gemini":
        return GeminiChatClient(
            model=model or getattr(settings, "gemini_text_model", "") or "gemini-3.8-flash",
            name="cloud", api_key=settings.gemini_api_key, cost_tracker=cost_tracker,
        )
    if provider == "local":
        return OllamaChatClient(
            model=model or getattr(settings, "local_llm_model", "gemma3:12b"),
            name="local-writer",
            base_url=getattr(settings, "local_llm_base_url", "http://localhost:11434"),
            # Writing sees whole lecture sections, so it needs a wider window than checking does
            num_ctx=max(int(getattr(settings, "local_llm_context", 12288)), 32768),
        )
    client = AzureOpenAI(
        api_key=settings.openai_api_key,
        api_version=settings.openai_api_version,
        azure_endpoint=settings.openai_endpoint,
    )
    return OpenAIChatClient(model=model or settings.openai_deployment, name="cloud", client=client, cost_tracker=cost_tracker)


def local_model_available(base_url: str, model: str, timeout: float = 2.0) -> bool:
    """True when Ollama at base_url is running and has `model` pulled."""
    try:
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        response.raise_for_status()
        names = {item.get("name", "") for item in response.json().get("models", [])}
    except (requests.RequestException, ValueError):
        return False
    return model in names or f"{model}:latest" in names


def build_local_llm(settings: Any) -> Optional[LLMClient]:
    """Return the local checker model, or None when it is off or not running.

    LOCAL_LLM=auto (default) uses it only if Ollama is up and has the model;
    LOCAL_LLM=on makes a missing model an error; LOCAL_LLM=off never uses it.
    """
    mode = str(getattr(settings, "local_llm_mode", "auto") or "auto").lower()
    if mode == "off":
        return None
    base_url = getattr(settings, "local_llm_base_url", "http://localhost:11434")
    model = getattr(settings, "local_llm_model", "gemma3:12b")
    if not local_model_available(base_url, model):
        if mode == "on":
            raise LLMError(f"Local model '{model}' is not available at {base_url}. Run: ollama pull {model}")
        return None
    return OllamaChatClient(
        model=model,
        name="local",
        base_url=base_url,
        num_ctx=int(getattr(settings, "local_llm_context", 12288)),
    )


def build_engine_llms(settings: Any, cost_tracker: Optional[Any] = None) -> EngineLLMs:
    writer = build_cloud_llm(settings, cost_tracker)
    if getattr(settings, "llm_provider", "") == "local":
        return EngineLLMs(cloud=writer, local=writer)
    return EngineLLMs(cloud=writer, local=build_local_llm(settings))
