"""Render the script as a conversation: both hosts in one generation per chunk.

Per-line TTS stitched with fixed gaps is what makes generated podcasts sound robotic:
nobody reacts, nothing overlaps, the rhythm resets on every line. These providers
synthesize a stretch of dialogue at once, so turn-taking and reactions come from the
model, the way NotebookLM's audio is made:

- ElevenLabs Text to Dialogue (eleven_v4, Hebrew via language_code="he"), which also
  returns when each line starts.
- Gemini multi-speaker TTS (gemini-3.8-flash-tts, two speakers, a per-line style).

Chunks are cached by content, so re-rendering after a small script edit only pays for
the chunks that changed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests
from pydub import AudioSegment

DIALOGUE_PROVIDERS = ("elevenlabs", "gemini")
SPEAKERS = ("Roee", "Noa")
CHAPTER_PAUSE_MS = 450

ELEVENLABS_DIALOGUE_URL = "https://api.elevenlabs.io/v1/text-to-dialogue/with-timestamps"
ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"
ELEVENLABS_LIBRARY_URL = "https://api.elevenlabs.io/v1/shared-voices"
ELEVENLABS_ADD_VOICE_URL = "https://api.elevenlabs.io/v1/voices/add/{owner}/{voice}"
ELEVENLABS_SUBSCRIPTION_URL = "https://api.elevenlabs.io/v1/user/subscription"
ELEVENLABS_DEFAULT_MODEL = "eleven_v4"
# ElevenLabs advises at most ~2,000 characters across a dialogue request
ELEVENLABS_MAX_CHARS = 1800

GEMINI_DEFAULT_MODEL = "gemini-3.8-flash-tts"
# 8,192 input tokens per request; Hebrew plus per-line markup stays well under this
GEMINI_MAX_CHARS = 3500
GEMINI_VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
    "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird",
    "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)
GEMINI_DEFAULT_VOICES = {"Roee": "Charon", "Noa": "Aoede"}
# The descriptor Google gives each prebuilt voice (speech-generation docs)
GEMINI_VOICE_STYLES = {
    "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm", "Fenrir": "Excitable",
    "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy", "Callirrhoe": "Easy-going", "Autonoe": "Bright",
    "Enceladus": "Breathy", "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth", "Despina": "Smooth",
    "Erinome": "Clear", "Algenib": "Gravelly", "Rasalgethi": "Informative", "Laomedeia": "Upbeat", "Achernar": "Soft",
    "Alnilam": "Firm", "Schedar": "Even", "Gacrux": "Mature", "Pulcherrima": "Forward", "Achird": "Friendly",
    "Zubenelgenubi": "Casual", "Vindemiatrix": "Gentle", "Sadachbia": "Lively", "Sadaltager": "Knowledgeable",
    "Sulafat": "Warm",
}

_DELIVERY_RE = re.compile(r"[^\w\s,\-]", re.U)

Turn = Dict[str, str]


class DialogueTTSError(RuntimeError):
    """A provider refused or failed; the message is written for the user."""


@dataclass
class RenderedChunk:
    path: Path
    duration: float
    turn_starts: List[float]  # seconds from the start of the chunk, one per turn


def clean_delivery(value: Any) -> str:
    """A short delivery hint ("curious", "laughing") safe to embed in either provider's syntax."""
    text = _DELIVERY_RE.sub("", str(value or "")).strip()
    return " ".join(text.split())[:40]


def chunk_turns(turns: Sequence[Turn], max_chars: int) -> List[List[int]]:
    """Group consecutive turns (by index) into chunks of at most max_chars of text."""
    groups: List[List[int]] = []
    current: List[int] = []
    size = 0
    for index, turn in enumerate(turns):
        length = len(turn["text"])
        if current and size + length > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        groups.append(current)
    return groups


def _estimate_starts(turns: Sequence[Turn], duration: float) -> List[float]:
    """Without timestamps, place each line in proportion to its share of the text."""
    total = sum(len(t["text"]) for t in turns) or 1
    starts, position = [], 0.0
    for turn in turns:
        starts.append(round(position, 3))
        position += duration * len(turn["text"]) / total
    return starts


class ElevenLabsDialogue:
    name = "elevenlabs"
    max_chars = ELEVENLABS_MAX_CHARS

    def __init__(self, api_key: str, voices: Dict[str, str], model: str = ELEVENLABS_DEFAULT_MODEL,
                 language: str = "he", http: Any = None) -> None:
        missing = [s for s in SPEAKERS if not voices.get(s)]
        if missing:
            raise DialogueTTSError(f"חסר קול ElevenLabs עבור: {', '.join(missing)}. בחר קולות במסך ההגדרות.")
        self.api_key = api_key
        self.voices = voices
        self.model = model or ELEVENLABS_DEFAULT_MODEL
        self.language = language
        self.http = http or requests

    def cache_key(self) -> str:
        return json.dumps([self.name, self.model, self.language, self.voices], sort_keys=True)

    def render(self, turns: Sequence[Turn], out_path: Path) -> RenderedChunk:
        inputs = []
        for turn in turns:
            delivery = clean_delivery(turn.get("delivery"))
            text = f"[{delivery}] {turn['text']}" if delivery else turn["text"]
            inputs.append({"text": text, "voice_id": self.voices[turn["speaker"]]})
        response = self.http.post(
            ELEVENLABS_DIALOGUE_URL,
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            json={"inputs": inputs, "model_id": self.model, "language_code": self.language},
            timeout=600,
        )
        if response.status_code != 200:
            raise DialogueTTSError(_elevenlabs_error(response))
        data = response.json()
        out_path = out_path.with_suffix(".mp3")
        out_path.write_bytes(base64.b64decode(data["audio_base64"]))
        duration = AudioSegment.from_file(out_path).duration_seconds

        starts: List[Optional[float]] = [None] * len(turns)
        for segment in data.get("voice_segments") or []:
            index = segment.get("dialogue_input_index")
            if isinstance(index, int) and 0 <= index < len(turns) and starts[index] is None:
                starts[index] = float(segment.get("start_time_seconds", 0.0))
        estimated = _estimate_starts(turns, duration)
        return RenderedChunk(out_path, duration, [s if s is not None else estimated[i] for i, s in enumerate(starts)])


def _elevenlabs_error(response: Any) -> str:
    try:
        detail = response.json().get("detail", {})
        message = detail.get("message", "") if isinstance(detail, dict) else str(detail)
        status = detail.get("status", "") if isinstance(detail, dict) else ""
    except ValueError:
        message, status = response.text[:200], ""
    if response.status_code == 401:
        return "מפתח ElevenLabs שגוי (401)."
    if "quota" in f"{status} {message}".lower():
        return "נגמרה מכסת התווים בחשבון ElevenLabs."
    if "voice_limit" in f"{status} {message}".lower() or "maximum amount of custom voices" in message.lower():
        return "הגעת למספר הקולות המרבי בחשבון ElevenLabs. מחק קול שלא בשימוש (ב-elevenlabs.io תחת My Voices) או שדרג את המנוי."
    return f"ElevenLabs החזיר שגיאה {response.status_code}: {message or status}"


def list_elevenlabs_voices(api_key: str, http: Any = None) -> List[Dict[str, Any]]:
    response = (http or requests).get(ELEVENLABS_VOICES_URL, headers={"xi-api-key": api_key}, timeout=20)
    if response.status_code != 200:
        raise DialogueTTSError(_elevenlabs_error(response))
    voices = []
    for voice in response.json().get("voices", []):
        labels = voice.get("labels") or {}
        voices.append({
            "voice_id": voice.get("voice_id", ""),
            "name": voice.get("name", ""),
            "gender": labels.get("gender", ""),
            "accent": labels.get("accent", ""),
            "category": voice.get("category", ""),
            "preview_url": voice.get("preview_url", ""),
        })
    return sorted(voices, key=lambda v: v["name"].lower())


def list_elevenlabs_library(api_key: str, language: str = "he", http: Any = None) -> Dict[str, Any]:
    """Native voices in a language from ElevenLabs' public Voice Library, with the account's voice slots.

    A new account holds only ElevenLabs' built-in voices, which are English (they still read Hebrew with
    v3/v4, with an accent). The library has voices recorded by Hebrew speakers; using one means adding
    it to the account, which takes one of the plan's voice slots.
    """
    http = http or requests
    headers = {"xi-api-key": api_key}
    response = http.get(ELEVENLABS_LIBRARY_URL, headers=headers, timeout=20,
                        params={"language": language, "page_size": 100, "sort": "trending"})
    if response.status_code != 200:
        raise DialogueTTSError(_elevenlabs_error(response))
    tier, used, limit = "", None, None
    try:
        sub = http.get(ELEVENLABS_SUBSCRIPTION_URL, headers=headers, timeout=20)
        if sub.status_code == 200:
            info = sub.json()
            tier, used, limit = info.get("tier", ""), info.get("voice_slots_used"), info.get("voice_limit")
    except requests.RequestException:
        pass
    free = tier == "free"
    voices = [{
        "voice_id": v.get("voice_id", ""), "public_owner_id": v.get("public_owner_id", ""), "name": v.get("name", ""),
        "gender": v.get("gender", ""), "age": v.get("age", ""), "descriptive": v.get("descriptive", ""),
        "use_case": v.get("use_case", ""), "preview_url": v.get("preview_url", ""),
        "added": bool(v.get("is_added_by_user")), "needs_paid": free and not v.get("free_users_allowed", True),
    } for v in response.json().get("voices", []) if v.get("voice_id") and v.get("public_owner_id")]
    voices.sort(key=lambda v: (v["needs_paid"], v["name"].lower()))
    return {"voices": voices, "tier": tier, "slots_used": used, "slots_limit": limit}


def add_elevenlabs_library_voice(api_key: str, public_owner_id: str, voice_id: str, name: str, http: Any = None) -> str:
    """Add a Voice Library voice to the account; returns the voice id to use with it."""
    if not re.fullmatch(r"[A-Za-z0-9]{6,80}", public_owner_id or "") or not re.fullmatch(r"[A-Za-z0-9]{6,80}", voice_id or ""):
        raise DialogueTTSError("מזהה קול לא תקין")
    url = ELEVENLABS_ADD_VOICE_URL.format(owner=public_owner_id, voice=voice_id)
    response = (http or requests).post(url, headers={"xi-api-key": api_key}, json={"new_name": name[:100] or voice_id}, timeout=30)
    if response.status_code != 200:
        raise DialogueTTSError(_elevenlabs_error(response))
    return response.json().get("voice_id") or voice_id


class GeminiDialogue:
    name = "gemini"
    max_chars = GEMINI_MAX_CHARS

    def __init__(self, api_key: str, voices: Optional[Dict[str, str]] = None,
                 model: str = GEMINI_DEFAULT_MODEL, client: Any = None) -> None:
        self.voices = {**GEMINI_DEFAULT_VOICES, **{k: v for k, v in (voices or {}).items() if v}}
        unknown = [v for v in self.voices.values() if v not in GEMINI_VOICES]
        if unknown:
            raise DialogueTTSError(f"קול Gemini לא מוכר: {', '.join(unknown)}")
        self.model = model or GEMINI_DEFAULT_MODEL
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self.client = client

    def cache_key(self) -> str:
        return json.dumps([self.name, self.model, self.voices], sort_keys=True)

    def render(self, turns: Sequence[Turn], out_path: Path) -> RenderedChunk:
        content = []
        for turn in turns:
            metadata: Dict[str, str] = {"type": "speech_metadata", "speaker": turn["speaker"]}
            delivery = clean_delivery(turn.get("delivery"))
            if delivery:
                metadata["style"] = delivery
            content.append({"type": "text", "text": turn["text"], "annotations": [metadata]})
        try:
            interaction = self.client.interactions.create(
                model=self.model,
                input=[{"type": "user_input", "content": content}],
                response_format={"type": "audio"},
                generation_config={"speech_config": {"speakers": [
                    {"speaker": speaker, "voice": self.voices[speaker]} for speaker in SPEAKERS
                ]}},
            )
            audio = base64.b64decode(interaction.output_audio.data)
        except Exception as exc:
            text = str(exc)
            if "API key" in text or "PERMISSION_DENIED" in text or "401" in text:
                raise DialogueTTSError("מפתח Gemini שגוי או ללא הרשאה ל-TTS.") from exc
            raise DialogueTTSError(f"Gemini TTS נכשל: {text[:200]}") from exc
        out_path = out_path.with_suffix(".wav")
        out_path.write_bytes(audio)  # complete WAV (24 kHz mono 16-bit)
        duration = AudioSegment.from_file(out_path).duration_seconds
        return RenderedChunk(out_path, duration, _estimate_starts(turns, duration))


@dataclass
class DialogueAudio:
    parts: List[Path]           # in playback order, including the short pauses between chapters
    turn_starts: List[float]    # seconds from the start of the speech, one per dialogue turn
    chapter_starts: List[float]


def render_dialogue(
    synth: Any,
    dialogue: Dict[str, Any],
    out_dir: Path,
    log: Optional[Callable[[str], None]] = None,
) -> DialogueAudio:
    """Render every chapter of a dialogue.json in chunks; reuse chunks that were already rendered."""
    log = log or (lambda _msg: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    turns: List[Turn] = dialogue["dialogue"]
    chapters = dialogue.get("metadata", {}).get("chapters") or [{"title": "", "start_index": 0}]
    bounds = [c["start_index"] for c in chapters] + [len(turns)]

    pause = out_dir / "pause.wav"
    if not pause.exists():
        AudioSegment.silent(duration=CHAPTER_PAUSE_MS, frame_rate=44100).export(pause, format="wav")

    parts: List[Path] = []
    turn_starts: List[float] = [0.0] * len(turns)
    chapter_starts: List[float] = []
    position = 0.0
    chunk_number = 0
    total_chunks = sum(len(chunk_turns(turns[b:e], synth.max_chars)) for b, e in zip(bounds, bounds[1:]))
    for chapter_index, (begin, end) in enumerate(zip(bounds, bounds[1:])):
        if chapter_index and parts:
            parts.append(pause)
            position += CHAPTER_PAUSE_MS / 1000
        chapter_starts.append(round(position, 3))
        chapter_turns = turns[begin:end]
        for group in chunk_turns(chapter_turns, synth.max_chars):
            chunk = [chapter_turns[i] for i in group]
            chunk_number += 1
            digest = hashlib.sha256(json.dumps([synth.cache_key(), chunk], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
            cached = next((p for p in (out_dir / f"dlg_{digest}.mp3", out_dir / f"dlg_{digest}.wav") if p.exists()), None)
            if cached is not None:
                rendered = _load_cached(cached, chunk)
                log(f"Chunk {chunk_number}/{total_chunks}: reused")
            else:
                log(f"Chunk {chunk_number}/{total_chunks}: rendering {len(chunk)} lines ({synth.name})")
                rendered = synth.render(chunk, out_dir / f"dlg_{digest}")
                rendered.path.with_suffix(".json").write_text(json.dumps(rendered.turn_starts), encoding="utf-8")
            for offset, local_index in zip(rendered.turn_starts, group):
                turn_starts[begin + local_index] = round(position + offset, 3)
            parts.append(rendered.path)
            position += rendered.duration
    return DialogueAudio(parts, turn_starts, chapter_starts)


def _load_cached(path: Path, chunk: Sequence[Turn]) -> RenderedChunk:
    duration = AudioSegment.from_file(path).duration_seconds
    timing = path.with_suffix(".json")
    try:
        starts = json.loads(timing.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        starts = _estimate_starts(chunk, duration)
    return RenderedChunk(path, duration, starts)


def build_dialogue_synth(settings: Any, provider: str) -> Any:
    if provider == "elevenlabs":
        return ElevenLabsDialogue(
            settings.elevenlabs_api_key,
            {"Roee": settings.elevenlabs_voice_roee, "Noa": settings.elevenlabs_voice_noa},
            model=settings.elevenlabs_model,
            language="en" if str(getattr(settings, "podcast_language", "he")).startswith("en") else "he",
        )
    if provider == "gemini":
        return GeminiDialogue(
            settings.gemini_api_key,
            {"Roee": settings.gemini_voice_roee, "Noa": settings.gemini_voice_noa},
            model=settings.gemini_tts_model,
        )
    raise DialogueTTSError(f"הספק {provider} לא תומך בשיחה משותפת")
