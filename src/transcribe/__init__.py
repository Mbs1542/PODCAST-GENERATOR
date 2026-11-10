"""Hebrew speech-to-text for lecture videos (ivrit.ai Whisper via faster-whisper)."""

from .whisper import (
    DEFAULT_MODEL,
    MEDIA_EXTENSIONS,
    TRANSCRIBE_LANGUAGES,
    HebrewTranscriber,
    model_for_language,
    release_models,
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
    "TRANSCRIBE_LANGUAGES",
    "HebrewTranscriber",
    "model_for_language",
    "release_models",
    "TranscribeError",
    "TranscriptSegment",
    "download_model",
    "download_url",
    "is_media",
    "to_srt",
    "to_text",
    "transcribe_file",
]
