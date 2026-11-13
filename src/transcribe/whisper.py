"""Transcribe Hebrew lecture audio/video into SRT and plain text.

Uses ivrit.ai's Hebrew fine-tune of Whisper large-v3-turbo in CTranslate2 format,
run by faster-whisper. faster-whisper decodes audio and video containers itself
(PyAV), so no separate extraction step is needed. On an NVIDIA GPU it runs in
float16; if CUDA is missing or unsupported it falls back to the CPU in int8.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

DEFAULT_MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"
# ivrit.ai's model is tuned for Hebrew; other languages (and auto-detect) get the general turbo model
GENERAL_MODEL = "large-v3-turbo"
TRANSCRIBE_LANGUAGES = {"he": "עברית", "en": "English", "auto": "זיהוי אוטומטי"}


# Quality levels. "balanced" is the long-standing behaviour and the default, so anything transcribed
# before levels existed (and anything that names no level) is transcribed exactly as it always was.
TRANSCRIBE_QUALITIES = {
    "fast": {"label": "מהיר", "note": "כפול מהר בערך, מעט פחות מדויק", "beam_size": 1, "full_model": False},
    "balanced": {"label": "מאוזן", "note": "ברירת המחדל: מהיר ומדויק", "beam_size": 5, "full_model": False},
    "accurate": {"label": "מדויק", "note": "המודל המלא: איטי פי 2-3, הורדה חד-פעמית של כ-3.1GB",
                 "beam_size": 5, "full_model": True},
}
DEFAULT_QUALITY = "balanced"
# The full (non-turbo) large-v3 models, for the "accurate" level
ACCURATE_MODEL = "ivrit-ai/whisper-large-v3-ct2"
ACCURATE_GENERAL_MODEL = "large-v3"
MAX_PARALLEL = 4


def normalize_quality(quality: Optional[str]) -> str:
    return quality if quality in TRANSCRIBE_QUALITIES else DEFAULT_QUALITY


def model_for_language(language: str, quality: str = DEFAULT_QUALITY) -> str:
    if TRANSCRIBE_QUALITIES[normalize_quality(quality)]["full_model"]:
        return ACCURATE_MODEL if language == "he" else ACCURATE_GENERAL_MODEL
    return DEFAULT_MODEL if language == "he" else GENERAL_MODEL


# Loading the 1.6 GB model takes 10-30 seconds; a batch of files reuses one loaded copy
_MODEL_CACHE: dict = {}
_MODEL_LOCK = threading.Lock()


def release_models() -> None:
    """Free the loaded models (GPU memory) once a batch is done."""
    with _MODEL_LOCK:
        _MODEL_CACHE.clear()


def _load_model(name: str, device: str, compute_type: str, models_dir: Path, workers: int = 1):
    """One loaded copy per model; workers > 1 lets that many transcriptions run on it at once.

    On the CPU the cores are split between the parallel transcriptions instead of each one
    trying to use all of them.
    """
    from faster_whisper import WhisperModel

    workers = max(1, min(MAX_PARALLEL, int(workers)))
    key = (name, device, compute_type, str(models_dir), workers)
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            extra = {"num_workers": workers} if workers > 1 else {}
            if workers > 1 and device == "cpu":
                extra["cpu_threads"] = max(1, (os.cpu_count() or 4) // workers)
            _MODEL_CACHE[key] = WhisperModel(name, device=device, compute_type=compute_type,
                                             download_root=str(models_dir), **extra)
        return _MODEL_CACHE[key]
MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
}
PARAGRAPH_PAUSE_SECONDS = 2.0
# Windows without Developer Mode cannot symlink; the hub falls back to copies but warns on every download
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
_CUDA_ERROR_HINTS = ("cuda", "cublas", "cudnn", "gpu", "device", "sm_")

Log = Callable[[str], None]


class TranscribeError(RuntimeError):
    """Raised when a file cannot be transcribed."""


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


def is_media(path: Path) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTENSIONS


def format_timestamp(seconds: float) -> str:
    millis = int(round(max(seconds, 0.0) * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(segments: List[TranscriptSegment]) -> str:
    blocks = [
        f"{number}\n{format_timestamp(s.start)} --> {format_timestamp(s.end)}\n{s.text}\n"
        for number, s in enumerate(segments, start=1)
    ]
    return "\n".join(blocks)


def to_text(segments: List[TranscriptSegment]) -> str:
    """Plain text with a paragraph break wherever the speaker paused."""
    paragraphs: List[List[str]] = []
    previous_end: Optional[float] = None
    for segment in segments:
        if previous_end is None or segment.start - previous_end > PARAGRAPH_PAUSE_SECONDS:
            paragraphs.append([])
        paragraphs[-1].append(segment.text)
        previous_end = segment.end
    return "\n\n".join(" ".join(p) for p in paragraphs) + "\n"


def _add_cuda_dll_dirs() -> None:
    """Make the CUDA libraries installed as pip wheels (nvidia-cublas/cudnn) loadable on Windows."""
    if sys.platform != "win32":
        return
    for base in map(Path, sys.path):
        for package in ("cublas", "cudnn", "cuda_nvrtc"):
            bin_dir = base / "nvidia" / package / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def default_models_dir() -> Path:
    # A container keeps the 1.6 GB model on a volume so it downloads once (MBS_MODELS_DIR=/data/models)
    configured = os.environ.get("MBS_MODELS_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "models"


def download_model(model: str = DEFAULT_MODEL, models_dir: Optional[Path] = None) -> Path:
    """Download the model ahead of time (the installer does this) and return its local folder."""
    from faster_whisper.utils import download_model as fw_download

    target = models_dir or default_models_dir()
    target.mkdir(parents=True, exist_ok=True)
    return Path(fw_download(model, cache_dir=str(target)))


def download_url(url: str, folder: Path) -> Path:
    """Download the audio of a lecture link (YouTube and similar) with yt-dlp."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise TranscribeError("yt-dlp is not installed (pip install -r requirements-transcribe.txt).") from exc
    folder.mkdir(parents=True, exist_ok=True)
    options = {"format": "bestaudio/best", "outtmpl": str(folder / "%(title).80s.%(ext)s"), "quiet": True, "noprogress": True}
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            return Path(ydl.prepare_filename(info))
    except Exception as exc:
        raise TranscribeError(f"Could not download {url}: {exc}") from exc


# The Mac's GPU, through src/transcribe/gpu_worker.py running natively next to the container.
# Docker Desktop on a Mac cannot reach the GPU, so the container hands the audio to the worker.
GPU_URL_ENV = "MBS_GPU_URL"  # e.g. http://host.docker.internal:8766
GPU_TOKEN_ENV = "MBS_GPU_TOKEN"
GPU_MODELS = {"ivrit-ai/whisper-large-v3-turbo-ct2", "ivrit-ai/whisper-large-v3-ct2", "large-v3-turbo", "large-v3"}


class _WorkerUnavailable(RuntimeError):
    """The GPU worker could not do this transcription; the CPU path takes over."""


def _gpu_worker() -> Optional[Tuple[str, str]]:
    url = os.environ.get(GPU_URL_ENV, "").strip().rstrip("/")
    token = os.environ.get(GPU_TOKEN_ENV, "").strip()
    return (url, token) if url and token else None


def _extract_audio(media_path: Path, target: Path) -> None:
    """16 kHz mono FLAC: what Whisper hears anyway, and a fraction of a video's size."""
    import subprocess

    try:
        done = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "flac", str(target)],
            capture_output=True, text=True, timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _WorkerUnavailable(f"ffmpeg could not extract the audio: {exc}") from exc
    if done.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        raise _WorkerUnavailable(f"ffmpeg could not extract the audio: {done.stderr.strip()[-300:]}")


def _transcribe_on_worker(
    media_path: Path, model: str, language: str, url: str, token: str, log: Log,
    progress: Optional[Callable[[float, float], None]],
) -> List[TranscriptSegment]:
    import json
    import tempfile

    import requests

    try:
        requests.get(f"{url}/health", timeout=3).raise_for_status()
    except requests.RequestException as exc:
        raise _WorkerUnavailable(f"the GPU worker does not answer: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="mbs-audio-") as tmp:
        audio = Path(tmp) / "audio.flac"
        _extract_audio(media_path, audio)
        log(f"Transcribing on the Mac's GPU ({model}, {audio.stat().st_size / 1e6:.0f} MB of audio)...")
        try:
            with audio.open("rb") as body, requests.post(
                f"{url}/transcribe", params={"model": model, "language": language}, data=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "audio/flac"},
                stream=True, timeout=(5, 300),  # the worker writes a line at least every 5 s
            ) as response:
                if response.status_code != 200:
                    raise _WorkerUnavailable(f"the GPU worker refused: HTTP {response.status_code} {response.text[:200]}")
                said_queued = False
                for raw in response.iter_lines():
                    if not raw:
                        continue
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "queued" and not said_queued:
                        said_queued = True
                        log("Waiting for the GPU (another transcription is running)...")
                    elif kind == "progress" and progress:
                        progress(float(event.get("position", 0)), float(event.get("duration", 0)))
                    elif kind == "error":
                        raise _WorkerUnavailable(f"the GPU worker failed: {event.get('message', '')}")
                    elif kind == "result":
                        segments = [TranscriptSegment(float(s), float(e), t) for s, e, t in event["segments"] if t]
                        duration = float(event.get("duration") or 0)
                        if progress and duration:
                            progress(duration, duration)
                        log(f"GPU transcription took {event.get('seconds', 0):.0f} s for {duration / 60:.1f} min of audio.")
                        return segments
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise _WorkerUnavailable(f"the GPU worker connection failed: {exc}") from exc
    raise _WorkerUnavailable("the GPU worker ended without a result")


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class HebrewTranscriber:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        models_dir: Optional[Path] = None,
        device: str = "auto",
        log: Optional[Log] = None,
        language: str = "he",
        quality: str = DEFAULT_QUALITY,
        workers: int = 1,
    ) -> None:
        if device not in ("auto", "cuda", "cpu"):
            raise ValueError("device must be auto, cuda or cpu")
        self.quality = normalize_quality(quality)
        self.workers = workers
        self.model = model
        self.models_dir = models_dir or default_models_dir()
        self.device = device
        self.language = language
        self.log = log or (lambda _msg: None)

    def _devices(self) -> List[str]:
        if self.device != "auto":
            return [self.device]
        _add_cuda_dll_dirs()
        return ["cuda", "cpu"] if _cuda_available() else ["cpu"]

    def transcribe(
        self,
        media_path: Path,
        progress: Optional[Callable[[float, float], None]] = None,
    ) -> List[TranscriptSegment]:
        worker = _gpu_worker() if self.device == "auto" and self.model in GPU_MODELS else None
        if worker:
            try:
                return _transcribe_on_worker(Path(media_path), self.model, self.language, *worker, self.log, progress)
            except _WorkerUnavailable as exc:
                self.log(f"The Mac's GPU is not available ({exc}); transcribing on the CPU instead (slower).")
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise TranscribeError("The transcriber is not installed (pip install -r requirements-transcribe.txt).") from exc

        devices = self._devices()
        for device in devices:
            compute_type = "float16" if device == "cuda" else "int8"
            try:
                level = TRANSCRIBE_QUALITIES[self.quality]
                self.log(f"Loading {self.model} on {device} ({compute_type}), quality: {self.quality}...")
                model = _load_model(self.model, device, compute_type, self.models_dir, self.workers)
                segments, info = model.transcribe(
                    str(media_path),
                    language=None if self.language == "auto" else self.language,
                    beam_size=level["beam_size"],
                    vad_filter=True,
                    # Long lectures: not conditioning on previous text avoids repetition loops
                    condition_on_previous_text=False,
                )
                result: List[TranscriptSegment] = []
                for segment in segments:  # decoding happens lazily while iterating
                    text = segment.text.strip()
                    if text:
                        result.append(TranscriptSegment(segment.start, segment.end, text))
                    if progress:
                        progress(segment.end, info.duration)
                return result
            except Exception as exc:
                message = str(exc).lower()
                if device == "cuda" and device != devices[-1] and any(h in message for h in _CUDA_ERROR_HINTS):
                    self.log(f"GPU transcription is not available ({exc}); using the CPU instead (slower).")
                    continue
                raise TranscribeError(f"Transcription failed: {exc}") from exc
        raise TranscribeError("No device could run the transcription.")


def transcribe_file(
    media_path: Path,
    out_dir: Optional[Path] = None,
    transcriber: Optional[HebrewTranscriber] = None,
    log: Optional[Log] = None,
) -> Tuple[Path, Path]:
    """Transcribe one file and write <name>.srt and <name>.txt. Returns (srt, txt)."""
    media_path = Path(media_path)
    if not media_path.exists():
        raise TranscribeError(f"File not found: {media_path}")
    log = log or print
    transcriber = transcriber or HebrewTranscriber(log=log)
    out_dir = out_dir or media_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    last_report = [0.0]

    def progress(position: float, duration: float) -> None:
        if duration and time.time() - last_report[0] > 5:
            last_report[0] = time.time()
            log(f"  {position / 60:5.1f} / {duration / 60:.1f} min ({position / duration:.0%})")

    segments = transcriber.transcribe(media_path, progress)
    if not segments:
        raise TranscribeError("No speech was recognized in the file.")
    srt_path = out_dir / f"{media_path.stem}.srt"
    txt_path = out_dir / f"{media_path.stem}.txt"
    srt_path.write_text(to_srt(segments), encoding="utf-8")
    txt_path.write_text(to_text(segments), encoding="utf-8")
    log(f"Done in {(time.time() - started) / 60:.1f} min: {len(segments)} segments.")
    return srt_path, txt_path
