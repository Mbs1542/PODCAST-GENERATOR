"""Transcribe Hebrew lecture audio/video into SRT and plain text.

Uses ivrit.ai's Hebrew fine-tune of Whisper large-v3-turbo in CTranslate2 format,
run by faster-whisper. faster-whisper decodes audio and video containers itself
(PyAV), so no separate extraction step is needed. On an NVIDIA GPU it runs in
float16; if CUDA is missing or unsupported it falls back to the CPU in int8.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

DEFAULT_MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"
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
    ) -> None:
        if device not in ("auto", "cuda", "cpu"):
            raise ValueError("device must be auto, cuda or cpu")
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
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscribeError("The transcriber is not installed (pip install -r requirements-transcribe.txt).") from exc

        devices = self._devices()
        for device in devices:
            compute_type = "float16" if device == "cuda" else "int8"
            try:
                self.log(f"Loading {self.model} on {device} ({compute_type})...")
                model = WhisperModel(self.model, device=device, compute_type=compute_type, download_root=str(self.models_dir))
                segments, info = model.transcribe(
                    str(media_path),
                    language=self.language,
                    beam_size=5,
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
