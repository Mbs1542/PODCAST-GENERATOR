"""The Mac GPU worker and the app's client for it, with the MLX transcription faked.

The worker runs for real (its HTTP server on a free loopback port); only mlx_whisper is replaced,
so the protocol, the token, the model whitelist and the CPU fallback are all exercised.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import threading
import types
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from src.transcribe import HebrewTranscriber
from src.transcribe import gpu_worker
from src.transcribe import whisper as whisper_module

TOKEN = "t" * 32
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is needed to build the test audio")


@pytest.fixture
def media(tmp_path) -> Path:
    path = tmp_path / "lecture.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:a", "aac", str(path)], check=True)
    return path


@pytest.fixture
def worker(monkeypatch):
    calls = []

    def fake_transcribe(audio_path, repo, language, on_fraction):
        assert audio_path.stat().st_size > 0
        calls.append((repo, language))
        for step in range(1, 5):
            on_fraction(step / 4)
        return {"segments": [{"start": 0.0, "end": 1.5, "text": " שלום "}, {"start": 1.5, "end": 3.0, "text": "  "}]}

    monkeypatch.setattr(gpu_worker, "_transcribe", fake_transcribe)
    monkeypatch.setattr(gpu_worker, "HEARTBEAT_SECONDS", 0.0)
    monkeypatch.setattr(gpu_worker.Handler, "token", TOKEN)
    server = ThreadingHTTPServer(("127.0.0.1", 0), gpu_worker.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls
    server.shutdown()


def _cpu_stops_here(monkeypatch) -> list:
    """Make the CPU path record that it was reached and stop there (faster-whisper not needed)."""
    reached = []

    def stop(*args, **_kwargs):
        reached.append(args)
        raise RuntimeError("stop here")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace())
    monkeypatch.setattr(whisper_module, "_load_model", stop)
    return reached


def _cpu_must_not_run(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the CPU path ran although the GPU worker answered")

    monkeypatch.setattr(whisper_module, "_load_model", refuse)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@needs_ffmpeg
def test_the_worker_transcribes_and_reports_progress(monkeypatch, media, worker):
    url, calls = worker
    monkeypatch.setenv("MBS_GPU_URL", url)
    monkeypatch.setenv("MBS_GPU_TOKEN", TOKEN)
    _cpu_must_not_run(monkeypatch)
    seen, messages = [], []
    segments = HebrewTranscriber(log=messages.append).transcribe(media, lambda pos, dur: seen.append((pos, dur)))
    assert [(s.start, s.end, s.text) for s in segments] == [(0.0, 1.5, "שלום")]  # blank text dropped
    assert calls == [("mlx-community/ivrit-ai-whisper-large-v3-turbo-mlx", "he")]
    assert seen and seen[-1][0] == seen[-1][1] > 2.5  # ends at 100% of the ~3 s of audio
    assert any("GPU" in m for m in messages)


@needs_ffmpeg
def test_a_wrong_token_falls_back_to_the_cpu(monkeypatch, media, worker):
    url, calls = worker
    monkeypatch.setenv("MBS_GPU_URL", url)
    monkeypatch.setenv("MBS_GPU_TOKEN", "wrong" * 6)
    fell_back = _cpu_stops_here(monkeypatch)
    messages = []
    with pytest.raises(Exception, match="stop here"):
        HebrewTranscriber(device="auto", log=messages.append).transcribe(media)
    assert calls == [] and fell_back
    assert any("HTTP 401" in m and "CPU" in m for m in messages)


def test_an_unreachable_worker_falls_back_to_the_cpu(monkeypatch, tmp_path):
    monkeypatch.setenv("MBS_GPU_URL", f"http://127.0.0.1:{_free_port()}")
    monkeypatch.setenv("MBS_GPU_TOKEN", TOKEN)
    fell_back = _cpu_stops_here(monkeypatch)
    messages = []
    with pytest.raises(Exception, match="stop here"):
        HebrewTranscriber(log=messages.append).transcribe(tmp_path / "x.mp4")
    assert fell_back and any("does not answer" in m for m in messages)


def test_without_the_environment_the_worker_is_never_tried(monkeypatch, tmp_path):
    monkeypatch.delenv("MBS_GPU_URL", raising=False)
    monkeypatch.delenv("MBS_GPU_TOKEN", raising=False)
    monkeypatch.setattr(whisper_module, "_transcribe_on_worker",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("worker tried")))
    fell_back = _cpu_stops_here(monkeypatch)
    with pytest.raises(Exception, match="stop here"):
        HebrewTranscriber().transcribe(tmp_path / "x.mp4")
    assert fell_back


def test_the_token_comes_from_a_plain_file_or_an_env_file(tmp_path):
    plain, env, other = tmp_path / "plain", tmp_path / "studio.env", tmp_path / "other.env"
    plain.write_text(TOKEN + "\n")
    env.write_text(f"MBS_EXTERNAL_AUTH=cloudflare-access\nMBS_GPU_TOKEN='{TOKEN}'\n")
    other.write_text("SOMETHING_ELSE=value\n")
    assert gpu_worker.read_token(plain) == TOKEN
    assert gpu_worker.read_token(env) == TOKEN
    assert gpu_worker.read_token(other) == ""  # an env file without the token is not a token


def test_the_worker_refuses_unknown_models_and_missing_tokens(worker):
    import requests

    url, calls = worker
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert requests.post(f"{url}/transcribe?model=x", data=b"abc").status_code == 401
    assert requests.post(f"{url}/transcribe?model=evil/repo", data=b"abc", headers=auth).status_code == 400
    assert requests.post(f"{url}/transcribe?model=large-v3&language=fr", data=b"abc", headers=auth).status_code == 400
    assert requests.get(f"{url}/health").json()["ok"] is True
    assert calls == []
