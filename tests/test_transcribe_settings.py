"""Transcription quality levels and the parallel limit.

The default level ("balanced") is exactly the long-standing behaviour, items made before levels existed
keep it, the limit is respected and can change while the app runs, and saving these settings leaves
every other line of .env as it was.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from src.transcribe import TranscriptSegment, model_for_language, normalize_quality
from src.transcribe.whisper import DEFAULT_MODEL, TRANSCRIBE_QUALITIES
from src.web.media import MediaRunner, MediaStore
from src.web.settings_store import SettingsStore


def test_balanced_is_the_old_behaviour_and_unknown_levels_fall_back_to_it():
    assert normalize_quality(None) == normalize_quality("") == normalize_quality("best") == "balanced"
    assert model_for_language("he") == model_for_language("he", "balanced") == DEFAULT_MODEL
    assert TRANSCRIBE_QUALITIES["balanced"]["beam_size"] == 5  # what every transcription used before levels
    assert model_for_language("he", "fast") == DEFAULT_MODEL and TRANSCRIBE_QUALITIES["fast"]["beam_size"] == 1
    assert model_for_language("he", "accurate") == "ivrit-ai/whisper-large-v3-ct2"
    assert model_for_language("en", "accurate") == "large-v3"


class Recorder:
    """A transcriber that records its level and how many run at the same time."""

    lock = threading.Lock()
    running = 0
    peak = 0
    calls = []

    def __init__(self, language, log, quality="balanced", workers=1):
        self.quality, self.workers = quality, workers

    def transcribe(self, media, progress=None):
        with Recorder.lock:
            Recorder.running += 1
            Recorder.peak = max(Recorder.peak, Recorder.running)
            Recorder.calls.append((Path(media).name, self.quality, self.workers))
        time.sleep(0.4)
        with Recorder.lock:
            Recorder.running -= 1
        return [TranscriptSegment(0.0, 1.0, "שלום")]


def _runner(tmp_path, limit):
    Recorder.running, Recorder.peak, Recorder.calls = 0, 0, []
    store = MediaStore(tmp_path / "outputs")
    runner = MediaRunner(store, transcriber_factory=Recorder)
    runner.parallel_limit = lambda: limit[0]
    return store, runner


def _media(tmp_path, count):
    files = []
    for n in range(count):
        path = tmp_path / f"lesson{n}.mp4"
        path.write_bytes(b"x")
        files.append(path)
    return files


def _wait_all(store, ids, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(store.get(i).status == "done" for i in ids):
            return
        time.sleep(0.05)
    raise AssertionError("transcriptions did not finish")


def test_each_transcription_keeps_its_level_and_old_items_stay_balanced(tmp_path):
    limit = [1]
    store, runner = _runner(tmp_path, limit)
    fast, accurate, default = _media(tmp_path, 3)
    ids = [runner.submit_transcription(fast, "a", "he", quality="fast").id,
           runner.submit_transcription(accurate, "b", "he", quality="accurate").id,
           runner.submit_transcription(default, "c", "he").id]
    old = store.create("transcription", "old", "lesson9.mp4", {"language": "he", "media": str(default)})  # pre-levels item
    runner._enqueue(old.id)
    _wait_all(store, ids + [old.id])
    levels = sorted(q for _, q, _ in Recorder.calls)
    assert levels == ["accurate", "balanced", "balanced", "fast"]


def test_parallel_limit_is_respected_and_can_change_while_running(tmp_path):
    limit = [1]
    store, runner = _runner(tmp_path, limit)
    ids = [runner.submit_transcription(m, m.stem, "he").id for m in _media(tmp_path, 3)]
    _wait_all(store, ids)
    assert Recorder.peak == 1  # the default: one at a time, as before

    limit[0] = 3  # changed in the settings: no restart needed
    Recorder.peak = 0
    ids = [runner.submit_transcription(m, m.stem, "he").id for m in _media(tmp_path, 3)]
    _wait_all(store, ids)
    assert Recorder.peak == 3 and all(w == 3 for _, _, w in Recorder.calls[-3:])

    limit[0] = 99  # out of range is clamped to the maximum, never more
    assert runner.limit() == 4
    limit[0] = "nonsense"
    assert runner.limit() == 1


def test_saving_transcription_settings_keeps_every_other_line(tmp_path):
    original = "\n".join([
        "# My settings",
        "GEMINI_API_KEY=secret-gemini-1234567890",
        "ELEVENLABS_API_KEY=secret-eleven-0987654321",
        "LLM_PROVIDER=gemini",
        "",
        "# --- custom note ---",
        "PODCAST_LANGUAGE=he",
        "SOMETHING_ELSE=kept",
    ]) + "\n"
    (tmp_path / ".env").write_text(original, encoding="utf-8")
    store = SettingsStore(tmp_path)
    store.update({"TRANSCRIBE_QUALITY": "accurate", "TRANSCRIBE_PARALLEL": "2"})
    after = (tmp_path / ".env").read_text(encoding="utf-8")
    assert after == original + "TRANSCRIBE_QUALITY=accurate\nTRANSCRIBE_PARALLEL=2\n"
    store.update({"TRANSCRIBE_QUALITY": "fast"})  # changing it again edits that line in place
    assert (tmp_path / ".env").read_text(encoding="utf-8") == original + "TRANSCRIBE_QUALITY=fast\nTRANSCRIBE_PARALLEL=2\n"
