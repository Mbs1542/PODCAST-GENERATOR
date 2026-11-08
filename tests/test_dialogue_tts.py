"""Conversation audio: request shapes per the provider docs, chunking, timing, caching."""

from __future__ import annotations

import base64
import io
import math
import struct
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.audio.dialogue_tts import (  # noqa: E402
    DialogueTTSError,
    ElevenLabsDialogue,
    GeminiDialogue,
    RenderedChunk,
    chunk_turns,
    clean_delivery,
    list_elevenlabs_voices,
    render_dialogue,
)
from src.engine.writer import normalize_turns  # noqa: E402

TURNS = [
    {"speaker": "Roee", "text": "שלום, היום נדבר על רשתות בענן.", "delivery": "warm"},
    {"speaker": "Noa", "text": "רגע, מה זה בכלל VNet?", "delivery": "curious [laughs]"},
    {"speaker": "Roee", "text": "שאלה מצוינת. זו רשת פרטית ומבודדת."},
]


def wav_bytes(seconds: float = 1.0, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"".join(struct.pack("<h", int(900 * math.sin(n / 9))) for n in range(int(rate * seconds))))
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class FakeHttp:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_chunking_keeps_order_and_limits():
    turns = [{"speaker": "Roee", "text": "x" * 300} for _ in range(7)]
    groups = chunk_turns(turns, 1000)
    assert groups == [[0, 1, 2], [3, 4, 5], [6]]
    assert chunk_turns([{"speaker": "Noa", "text": "y" * 5000}], 1000) == [[0]]  # one long line is never split


def test_delivery_is_sanitized_for_provider_syntax():
    assert clean_delivery("curious [laughs]") == "curious laughs"
    assert clean_delivery("<excited>") == "excited"
    turns = normalize_turns({"turns": [{"speaker": "noa", "text": "וואו", "delivery": "]surprised["}]})
    assert turns == [{"speaker": "Noa", "text": "וואו", "delivery": "surprised"}]


def test_elevenlabs_request_follows_text_to_dialogue_docs(tmp_path):
    payload = {
        "audio_base64": base64.b64encode(wav_bytes(3.0)).decode(),
        "voice_segments": [
            {"voice_id": "v-roee", "start_time_seconds": 0.0, "end_time_seconds": 1.0, "dialogue_input_index": 0},
            {"voice_id": "v-noa", "start_time_seconds": 1.1, "end_time_seconds": 2.0, "dialogue_input_index": 1},
            {"voice_id": "v-roee", "start_time_seconds": 2.05, "end_time_seconds": 3.0, "dialogue_input_index": 2},
        ],
    }
    http = FakeHttp(FakeResponse(200, payload))
    synth = ElevenLabsDialogue("key-123", {"Roee": "v-roee", "Noa": "v-noa"}, http=http)
    rendered = synth.render(TURNS, tmp_path / "chunk")

    url, kwargs = http.calls[0]
    assert url == "https://api.elevenlabs.io/v1/text-to-dialogue/with-timestamps"
    assert kwargs["headers"]["xi-api-key"] == "key-123"
    body = kwargs["json"]
    assert body["model_id"] == "eleven_v4" and body["language_code"] == "he"
    assert body["inputs"][0] == {"text": "[warm] שלום, היום נדבר על רשתות בענן.", "voice_id": "v-roee"}
    assert body["inputs"][1]["text"].startswith("[curious laughs] ")
    assert body["inputs"][2]["text"] == TURNS[2]["text"]  # no delivery, no tag
    assert rendered.turn_starts == [0.0, 1.1, 2.05]
    assert rendered.duration == pytest.approx(3.0, abs=0.05)
    assert rendered.path.exists()


def test_elevenlabs_needs_both_voices_and_explains_errors(tmp_path):
    with pytest.raises(DialogueTTSError, match="נועה|Noa"):
        ElevenLabsDialogue("k", {"Roee": "v1", "Noa": ""})
    synth = ElevenLabsDialogue("bad", {"Roee": "a", "Noa": "b"}, http=FakeHttp(FakeResponse(401, {"detail": {"message": "invalid"}})))
    with pytest.raises(DialogueTTSError, match="401"):
        synth.render(TURNS, tmp_path / "x")
    quota = FakeHttp(FakeResponse(400, {"detail": {"status": "quota_exceeded", "message": "quota"}}))
    with pytest.raises(DialogueTTSError, match="מכסת"):
        ElevenLabsDialogue("k", {"Roee": "a", "Noa": "b"}, http=quota).render(TURNS, tmp_path / "y")


def test_voice_list_is_simplified_and_sorted():
    http = FakeHttp(FakeResponse(200, {"voices": [
        {"voice_id": "2", "name": "zoe", "labels": {"gender": "female", "accent": "israeli"}, "category": "cloned"},
        {"voice_id": "1", "name": "Avi", "labels": {"gender": "male"}, "category": "premade"},
    ]}))
    voices = list_elevenlabs_voices("k", http=http)
    assert [v["name"] for v in voices] == ["Avi", "zoe"]
    assert voices[1]["accent"] == "israeli"


def test_gemini_request_follows_multi_speaker_docs(tmp_path):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_audio=SimpleNamespace(data=base64.b64encode(wav_bytes(2.0)).decode()))

    client = SimpleNamespace(interactions=SimpleNamespace(create=create))
    rendered = GeminiDialogue("k", {"Noa": "Kore"}, client=client).render(TURNS, tmp_path / "g")
    request = calls[0]
    assert request["model"] == "gemini-3.8-flash-tts"
    assert request["response_format"] == {"type": "audio"}
    speakers = request["generation_config"]["speech_config"]["speakers"]
    assert speakers == [{"speaker": "Roee", "voice": "Charon"}, {"speaker": "Noa", "voice": "Kore"}]
    content = request["input"][0]["content"]
    assert content[1]["text"] == TURNS[1]["text"]  # the text stays a verbatim transcript
    assert content[1]["annotations"] == [{"type": "speech_metadata", "speaker": "Noa", "style": "curious laughs"}]
    assert "style" not in content[2]["annotations"][0]
    assert rendered.path.suffix == ".wav" and rendered.duration == pytest.approx(2.0, abs=0.05)
    assert rendered.turn_starts[0] == 0 and rendered.turn_starts[1] < rendered.turn_starts[2]

    with pytest.raises(DialogueTTSError):
        GeminiDialogue("k", {"Roee": "NotAVoice"}, client=client)


class CountingSynth:
    name = "fake"
    max_chars = 80  # each test line is ~37 characters, so two lines fit in a chunk

    def __init__(self) -> None:
        self.calls = 0

    def cache_key(self) -> str:
        return "fake-v1"

    def render(self, turns, out_path: Path) -> RenderedChunk:
        self.calls += 1
        path = out_path.with_suffix(".wav")
        path.write_bytes(wav_bytes(1.0 * len(turns), 16000))
        return RenderedChunk(path, float(len(turns)), [float(i) for i in range(len(turns))])


def test_render_dialogue_times_chapters_and_reuses_chunks(tmp_path):
    turns = [{"speaker": "Roee" if i % 2 == 0 else "Noa", "text": f"שורה מספר {i} " + "מילה " * 5} for i in range(6)]
    dialogue = {"dialogue": turns, "metadata": {"chapters": [{"title": "א", "start_index": 0}, {"title": "ב", "start_index": 4}]}}
    synth = CountingSynth()
    audio = render_dialogue(synth, dialogue, tmp_path)
    assert synth.calls == 3  # chapter 1 splits into two chunks of two lines, chapter 2 is one chunk
    assert audio.chapter_starts == [0.0, pytest.approx(4.45)]  # 4 seconds of speech + the pause
    assert audio.turn_starts == [0.0, 1.0, 2.0, 3.0, pytest.approx(4.45), pytest.approx(5.45)]
    assert audio.parts[2].name == "pause.wav"

    again = CountingSynth()
    assert render_dialogue(again, dialogue, tmp_path).turn_starts == audio.turn_starts
    assert again.calls == 0  # every chunk was reused

    turns[5]["text"] = "שורה ששונתה"
    third = CountingSynth()
    render_dialogue(third, dialogue, tmp_path)
    assert third.calls == 1  # only the chunk that changed is rendered again
