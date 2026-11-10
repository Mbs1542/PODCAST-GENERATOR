"""Keep Windows from sleeping while a long job runs (transcription, downloads, a podcast).

From the TOOLS transcriber's SleepInhibitor, reduced to the documented API: the request
lasts while the job's thread holds it and is released when the job ends, even on error,
so the user's power settings are never changed. The display may still turn off.
Elsewhere (Linux, a container) it does nothing.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


@contextmanager
def keep_awake() -> Iterator[None]:
    if sys.platform != "win32":
        yield
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        kernel32.SetThreadExecutionState(_ES_CONTINUOUS)  # this thread no longer needs the machine awake
