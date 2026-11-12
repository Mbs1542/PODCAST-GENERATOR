"""Transcription worker for Apple Silicon: ivrit.ai Whisper on the Mac's GPU (MLX).

A container (Docker Desktop on a Mac) cannot reach the GPU: Docker runs in a Linux VM, so
faster-whisper inside it uses the CPU only. This small server runs natively on the Mac, next to
the container, and transcribes on the GPU with mlx-whisper. Measured on an M1 Ultra, 10 minutes of
Hebrew: 14 s here against about 90 s for faster-whisper int8 on 6 CPU threads, with the same word
error rate (0.182 against 0.176 on the same reference).

The app sends AUDIO, not a file path: it extracts a 16 kHz mono FLAC with ffmpeg and posts the
bytes. The worker therefore never opens a path the caller names. It listens on 127.0.0.1 only
(Docker Desktop forwards host.docker.internal to the Mac's loopback) and every request must carry
the shared token. Only the four models below can be requested.

    python -m src.transcribe.gpu_worker --port 8766 --token-file /path/to/token
    pip install -r requirements-mac-gpu.txt

Protocol: POST /transcribe?model=<faster-whisper model name>&language=he|en|auto
          Authorization: Bearer <token>, body = audio bytes.
The answer is NDJSON, one object per line, flushed as it happens:
    {"type": "queued"}                                  another job holds the GPU (repeated)
    {"type": "progress", "position": s, "duration": s}  every few seconds
    {"type": "result", "segments": [[start, end, text], ...], "duration": s, "seconds": s}
    {"type": "error", "message": "..."}
GET /health answers {"ok": true, "busy": bool, "loaded": model or null} without a token.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

# faster-whisper model name (what the app asks for) -> the same weights converted to MLX.
# The ivrit.ai conversions are mlx-community's, made in June 2025 from ivrit.ai's May 2025 weights.
MLX_MODELS = {
    "ivrit-ai/whisper-large-v3-turbo-ct2": "mlx-community/ivrit-ai-whisper-large-v3-turbo-mlx",
    "ivrit-ai/whisper-large-v3-ct2": "mlx-community/ivrit-ai-whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}
LANGUAGES = {"he", "en", "auto"}
MAX_UPLOAD_BYTES = 1_500_000_000  # a 16 kHz mono FLAC of a 10-hour recording is well under this
HEARTBEAT_SECONDS = 5.0
IDLE_UNLOAD_SECONDS = 600  # give the GPU memory back when nobody has transcribed for 10 minutes

_GPU = threading.Lock()  # one transcription at a time: the GPU is shared with Ollama
_state = {"busy": False, "loaded": None, "last_used": 0.0}


def _log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


class _Progress:
    """Stands in for tqdm inside mlx_whisper.transcribe and reports the decoded position."""

    callback = None  # set per job; one job at a time, guarded by _GPU

    def __init__(self, total=0, **_kwargs):
        self.total = total or 0
        self.n = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def update(self, n=1):
        self.n += n
        if _Progress.callback and self.total:
            _Progress.callback(self.n / self.total)


def _mlx_module():
    # mlx_whisper/__init__ does `from .transcribe import transcribe`, so the attribute
    # mlx_whisper.transcribe is the FUNCTION; import_module returns the module itself.
    import importlib

    return importlib.import_module("mlx_whisper.transcribe")


def _transcribe(audio_path: Path, repo: str, language: str, on_fraction) -> dict:
    mlx_transcribe = _mlx_module()
    mlx_transcribe.tqdm.tqdm = _Progress  # the module calls tqdm.tqdm(total=..., disable=...)
    _Progress.callback = on_fraction
    try:
        out = mlx_transcribe.transcribe(
            str(audio_path),
            path_or_hf_repo=repo,
            language=None if language == "auto" else language,
            verbose=False,  # False (not None) is what turns the progress bar, and our hook, on
            # Long lectures: not conditioning on previous text avoids repetition loops (as the CPU path)
            condition_on_previous_text=False,
        )
    finally:
        _Progress.callback = None
    _state["loaded"] = repo
    return out


def _unload_when_idle() -> None:
    while True:
        time.sleep(60)
        if _state["loaded"] and not _state["busy"] and time.time() - _state["last_used"] > IDLE_UNLOAD_SECONDS:
            with _GPU:
                try:
                    import mlx.core as mx

                    mlx_transcribe = _mlx_module()
                    mlx_transcribe.ModelHolder.model = None
                    mlx_transcribe.ModelHolder.model_path = None
                    mx.clear_cache()
                    _log(f"unloaded {_state['loaded']} after {IDLE_UNLOAD_SECONDS}s idle")
                except Exception as exc:  # never let the janitor kill the server
                    _log(f"unload failed: {exc}")
                _state["loaded"] = None


class Handler(BaseHTTPRequestHandler):
    server_version = "mbs-gpu-worker/1"
    token = ""

    def log_message(self, format, *args):  # noqa: A002 (base-class name); one line per request
        _log(f"{self.address_string()} {format % args}")

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _line(self, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode() + b"\n"
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            return self._json(200, {"ok": True, "busy": _state["busy"], "loaded": _state["loaded"]})
        return self._json(404, {"detail": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/transcribe":
            return self._json(404, {"detail": "not found"})
        sent = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not self.token or not hmac.compare_digest(sent.encode(), self.token.encode()):
            return self._json(401, {"detail": "bad token"})
        query = parse_qs(url.query)
        model = (query.get("model") or [""])[0]
        language = (query.get("language") or ["he"])[0]
        if model not in MLX_MODELS or language not in LANGUAGES:
            return self._json(400, {"detail": f"model must be one of {sorted(MLX_MODELS)}, language one of {sorted(LANGUAGES)}"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_UPLOAD_BYTES:
            return self._json(413 if length else 411, {"detail": f"send 1..{MAX_UPLOAD_BYTES} bytes with a Content-Length"})

        with tempfile.TemporaryDirectory(prefix="mbs-gpu-") as tmp:
            audio = Path(tmp) / "audio"
            remaining = length
            with audio.open("wb") as fh:
                while remaining:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        return self._json(400, {"detail": "upload ended early"})
                    fh.write(chunk)
                    remaining -= len(chunk)

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while not _GPU.acquire(timeout=HEARTBEAT_SECONDS):
                    self._line({"type": "queued"})
                try:
                    _state["busy"] = True
                    self._run(audio, MLX_MODELS[model], language)
                finally:
                    _state["busy"] = False
                    _state["last_used"] = time.time()
                    _GPU.release()
            except (BrokenPipeError, ConnectionResetError):
                _log("client went away")
                return
            self.wfile.write(b"0\r\n\r\n")

    def _run(self, audio: Path, repo: str, language: str) -> None:
        started = time.monotonic()
        last = [0.0]
        duration = [0.0]

        def on_fraction(fraction: float) -> None:
            if time.monotonic() - last[0] >= HEARTBEAT_SECONDS:
                last[0] = time.monotonic()
                self._line({"type": "progress", "position": round(fraction * duration[0], 1),
                            "duration": round(duration[0], 1), "fraction": round(fraction, 4)})

        try:
            duration[0] = _probe_seconds(audio)
            _log(f"transcribing {duration[0] / 60:.1f} min with {repo} ({language})")
            out = _transcribe(audio, repo, language, on_fraction)
        except (BrokenPipeError, ConnectionResetError):
            raise
        except Exception as exc:
            _log(f"failed: {exc!r}")
            self._line({"type": "error", "message": str(exc)[:500]})
            return
        segments = [[round(s["start"], 2), round(s["end"], 2), s["text"].strip()]
                    for s in out.get("segments", []) if s.get("text", "").strip()]
        seconds = time.monotonic() - started
        _log(f"done: {len(segments)} segments in {seconds:.1f}s")
        self._line({"type": "result", "segments": segments, "duration": duration[0], "seconds": round(seconds, 1)})


def _probe_seconds(audio: Path) -> float:
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
                         capture_output=True, text=True, timeout=60)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def read_token(path: Path) -> str:
    """The container's env file can be the one place the token lives: MBS_GPU_TOKEN=... is read from it."""
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("MBS_GPU_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "" if "=" in text else text.strip()


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="ivrit.ai Whisper on the Mac's GPU (MLX)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--token-file", required=True,
                        help="the shared token: a file holding only the token, or an env file with MBS_GPU_TOKEN=...")
    args = parser.parse_args(argv)
    token = read_token(Path(args.token_file))
    if len(token) < 24:
        sys.exit("the token must be at least 24 characters")
    if sys.platform != "darwin":
        sys.exit("the GPU worker runs on macOS (Apple Silicon) only")
    Handler.token = token
    threading.Thread(target=_unload_when_idle, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    _log(f"listening on {args.host}:{args.port}; models: {', '.join(MLX_MODELS)}")
    server.serve_forever()


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    main()
