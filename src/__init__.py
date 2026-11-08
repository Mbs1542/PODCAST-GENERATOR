"""Top-level package for the Azure Podcast Generator pipeline."""

import os as _os
from pathlib import Path as _Path

# The installer puts ffmpeg in <install dir>/tools/ffmpeg/bin instead of changing the system PATH.
# Expose it to this process (pydub, moviepy and subprocess calls) before any of them look for it.
_TOOLS_BIN = _Path(__file__).resolve().parents[1] / "tools" / "ffmpeg" / "bin"
if _TOOLS_BIN.is_dir():
    _os.environ["PATH"] = f"{_TOOLS_BIN}{_os.pathsep}{_os.environ.get('PATH', '')}"

__all__ = [
    "dialogue",
    "audio",
    "pipeline",
    "visuals",
    "utils",
]
