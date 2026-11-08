"""Hebrew speech-to-text for lecture videos (ivrit.ai Whisper via faster-whisper)."""

from .whisper import (
    DEFAULT_MODEL,
    MEDIA_EXTENSIONS,
    HebrewTranscriber,
    TranscribeError,
    TranscriptSegment,
    download_model,
    download_url,
    is_media,
    to_srt,
    to_text,
    transcribe_file,
)

__all__ = [
    "DEFAULT_MODEL",
    "MEDIA_EXTENSIONS",
    "HebrewTranscriber",
    "TranscribeError",
    "TranscriptSegment",
    "download_model",
    "download_url",
    "is_media",
    "to_srt",
    "to_text",
    "transcribe_file",
]
