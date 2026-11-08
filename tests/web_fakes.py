"""Stand-ins for the paid services, shared by the web tests and the demo server."""

from __future__ import annotations

import math
import struct
import time
import wave
from pathlib import Path
from typing import Any, Dict, List

from src.engine import EngineLLMs
from src.transcribe import TranscriptSegment
from src.web.jobs import JobRunner

from test_engine import FakeLLM  # noqa: E402  (tests/ is on sys.path)

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_transcript.txt"


def write_tone(path: Path, seconds: float, frequency: float) -> None:
    rate = 16000
    frames = b"".join(
        struct.pack("<h", int(1500 * math.sin(2 * math.pi * frequency * n / rate))) for n in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)


class FakeTTS:
    """One short tone per turn (Roee lower, Noa higher), length by word count."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    def synthesize(self, dialogue_path: Path, run_paths: Any, force: bool = False) -> List[Dict[str, Any]]:
        import json

        turns = json.loads(Path(dialogue_path).read_text(encoding="utf-8"))["dialogue"]
        results = []
        for index, turn in enumerate(turns, start=1):
            target = run_paths.audio_dir / f"{index:04d}_{turn['speaker'].lower()}.wav"
            seconds = max(0.6, len(turn["text"].split()) * 0.06)
            write_tone(target, seconds, 220 if turn["speaker"] == "Roee" else 330)
            results.append({"path": target, "duration": seconds})
            time.sleep(self.delay)
        return results


class FakeTranscriber:
    """Returns the sample lecture, one segment per paragraph, with progress."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    def transcribe(self, media: Path, progress=None) -> List[TranscriptSegment]:
        paragraphs = [p.strip() for p in SAMPLE.read_text(encoding="utf-8").split("\n\n") if p.strip()]
        segments, position = [], 0.0
        for text in paragraphs:
            segments.append(TranscriptSegment(position, position + 9.0, text))
            position += 10.0
            if progress:
                progress(position, 10.0 * len(paragraphs))
            time.sleep(self.delay)
        return segments


class FakeConversation:
    """Stands in for ElevenLabs/Gemini conversation rendering: one tone per chunk."""

    name = "fake-conversation"
    max_chars = 400

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    def cache_key(self) -> str:
        return "fake-conversation-v1"

    def render(self, turns, out_path: Path):
        from src.audio.dialogue_tts import RenderedChunk

        seconds = [max(0.6, len(t["text"].split()) * 0.06) for t in turns]
        path = out_path.with_suffix(".wav")
        write_tone(path, sum(seconds), 260)
        starts, position = [], 0.0
        for s in seconds:
            starts.append(position)
            position += s
        time.sleep(self.delay)
        return RenderedChunk(path, sum(seconds), starts)


def png_bytes(color=(40, 120, 200), size=(320, 180)) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class FakeImageMaker:
    """Stands in for Gemini image generation: a solid-colour PNG per prompt."""

    def __init__(self) -> None:
        self.prompts = []

    def make(self, prompt):
        self.prompts.append(prompt)
        return png_bytes((30 + 40 * len(self.prompts) % 200, 90, 160)), ".png"


def fake_video_renderer(**kwargs):
    """The real slideshow (timeline and slide drawing), with ffmpeg replaced by a stub."""
    import subprocess

    from src.video import render_video

    calls = kwargs.pop("_calls", None)

    def run(command, **_):
        Path(command[-1]).write_bytes(b"fake-mp4")
        if calls is not None:
            calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    return render_video(**kwargs, run=run, duration=60.0)


def fake_runner_factory(delay: float = 0.0):
    def factory(store, settings_store):
        return JobRunner(
            store,
            settings_store,
            llms_factory=lambda settings, costs: EngineLLMs(cloud=FakeLLM("cloud"), local=FakeLLM("local")),
            tts_factory=lambda settings, provider, costs: FakeTTS(delay),
            transcriber_factory=lambda log: FakeTranscriber(delay),
            dialogue_factory=lambda settings, provider: FakeConversation(delay),
            image_maker_factory=lambda settings: FakeImageMaker(),
            video_renderer=fake_video_renderer,
        )
    return factory


def write_fake_env(root: Path, outputs: Path) -> None:
    (root / ".env").write_text(
        "\n".join([
            "AZURE_OPENAI_ENDPOINT=https://example.openai.azure.com/",
            "AZURE_OPENAI_API_KEY=fake-key-for-tests-0000",
            "AZURE_OPENAI_DEPLOYMENT=gpt-4o",
            "AZURE_SPEECH_KEY=fake-speech-key-1111",
            "AZURE_SPEECH_REGION=westeurope",
            "GEMINI_API_KEY=fake-gemini-key-2222",
            "DEFAULT_TTS_PROVIDER=auto",
            "LOCAL_LLM=off",
            f"OUTPUT_BASE_DIR={outputs.as_posix()}",
        ]) + "\n",
        encoding="utf-8",
    )
