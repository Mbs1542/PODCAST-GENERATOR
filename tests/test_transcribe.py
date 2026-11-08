import sys
import types
from pathlib import Path

import pytest

from src.engine.transcript import build_paragraphs
from src.transcribe import HebrewTranscriber, TranscribeError, TranscriptSegment, is_media, to_srt, to_text
from src.transcribe import whisper as whisper_module
from src.transcribe.whisper import format_timestamp

SEGMENTS = [
    TranscriptSegment(0.0, 2.5, "שלום לכולם"),
    TranscriptSegment(2.6, 5.0, "היום נדבר על VNet"),
    TranscriptSegment(9.0, 3725.25, "ועכשיו על NSG"),
]


def test_timestamps_and_srt_round_trip_into_the_engine():
    assert format_timestamp(3725.25) == "01:02:05,250"
    srt = to_srt(SEGMENTS)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,500\nשלום לכולם\n")
    paragraphs = build_paragraphs(srt)  # stage 1 of the script engine reads it back
    assert [p.text for p in paragraphs] == ["שלום לכולם היום נדבר על VNet", "ועכשיו על NSG"]
    assert paragraphs[1].start == 9.0


def test_text_breaks_paragraphs_on_pauses():
    assert to_text(SEGMENTS) == "שלום לכולם היום נדבר על VNet\n\nועכשיו על NSG\n"


def test_media_detection():
    assert is_media(Path("lecture.MP4")) and is_media(Path("a.m4a"))
    assert not is_media(Path("lecture.srt"))


class _FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def _install_fake_whisper(monkeypatch, fail_on_cuda: bool):
    loaded = []

    class FakeModel:
        def __init__(self, model, device, compute_type, download_root):
            loaded.append((device, compute_type))
            if device == "cuda" and fail_on_cuda:
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

        def transcribe(self, path, **kwargs):
            assert kwargs["language"] == "he" and kwargs["vad_filter"]
            info = types.SimpleNamespace(duration=4.0)
            return iter([_FakeSegment(0, 2, " שלום "), _FakeSegment(2, 4, "  ")]), info

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(whisper_module, "_cuda_available", lambda: True)
    monkeypatch.setattr(whisper_module, "_add_cuda_dll_dirs", lambda: None)
    return loaded


def test_missing_cuda_libraries_fall_back_to_cpu(monkeypatch, tmp_path):
    loaded = _install_fake_whisper(monkeypatch, fail_on_cuda=True)
    messages = []
    segments = HebrewTranscriber(models_dir=tmp_path, log=messages.append).transcribe(tmp_path / "x.mp4")
    assert loaded == [("cuda", "float16"), ("cpu", "int8")]
    assert [s.text for s in segments] == ["שלום"]  # blank segments are dropped
    assert any("CPU" in m for m in messages)


def test_gpu_is_used_when_it_works(monkeypatch, tmp_path):
    loaded = _install_fake_whisper(monkeypatch, fail_on_cuda=False)
    HebrewTranscriber(models_dir=tmp_path).transcribe(tmp_path / "x.mp4")
    assert loaded == [("cuda", "float16")]


def test_other_errors_are_not_hidden_by_the_fallback(monkeypatch, tmp_path):
    class Broken:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Invalid data found when processing input")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Broken))
    monkeypatch.setattr(whisper_module, "_cuda_available", lambda: True)
    monkeypatch.setattr(whisper_module, "_add_cuda_dll_dirs", lambda: None)
    with pytest.raises(TranscribeError, match="Invalid data"):
        HebrewTranscriber(models_dir=tmp_path).transcribe(tmp_path / "x.mp4")
