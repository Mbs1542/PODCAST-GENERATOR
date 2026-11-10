"""What the screen needs for "running for / time left" and a useful work log."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from src.engine.llm import _retry_reporter
from src.web.jobs import JobRunner
from src.web.media import MediaStore
from src.web.projects import ProjectStore


def test_a_step_keeps_its_start_time_while_only_its_numbers_change(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("שיעור", "transcript", "a.srt", {})
    store.update(project.id, stage="מנקה את התמלול: חלק 1 מתוך 12")
    started = store.get(project.id).stage_started
    assert started > 0
    time.sleep(0.02)
    store.update(project.id, stage="מנקה את התמלול: חלק 2 מתוך 12", progress=0.1)
    assert store.get(project.id).stage_started == started  # same step, counting on
    store.update(project.id, stage="בונה תוכנית לפרק")
    assert store.get(project.id).stage_started > started  # a new step starts its own clock

    media = MediaStore(tmp_path)
    item = media.create("transcription", "x", "x.mp4", {})
    media.update(item.id, stage="מתמלל: 4 מתוך 112 דקות")
    first = media.get(item.id).stage_started
    time.sleep(0.02)
    media.update(item.id, stage="מתמלל: 22 מתוך 112 דקות")
    assert media.get(item.id).stage_started == first


def test_engine_lines_become_progress_and_readable_steps(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("שיעור", "transcript", "a.srt", {})
    runner = JobRunner.__new__(JobRunner)  # only the log translation is under test
    runner.store = store
    log = runner._engine_log(project.id)

    log("Stage 1: cleaning part 3/12")
    p = store.get(project.id)
    assert p.progress == 2 / 12 and "שלב 1 מתוך 3" in p.stage
    log("Stage 1: 10521 words in 40 sections (cloud), 12 fixes")
    p = store.get(project.id)
    assert p.progress is None and "שלב 2 מתוך 3" in p.stage
    log("Stage 2: data science, level=intro, lecture")
    assert "שלב 3 מתוך 3" in store.get(project.id).stage
    log("Writing segment 2/8")
    p = store.get(project.id)
    assert p.progress == 2 / 8 and p.stage == "כותב ובודק מקטע 2 מתוך 8"
    assert any("cleaning part 3/12" in line for line in p.log)  # everything also stays in the log


def test_a_retry_after_a_busy_model_is_written_to_the_work_log():
    lines = []
    client = SimpleNamespace(name="gemini", on_retry=lines.append)
    busy = SimpleNamespace(code=503)
    state = SimpleNamespace(args=(client,), attempt_number=2, next_action=SimpleNamespace(sleep=12.0),
                            outcome=SimpleNamespace(exception=lambda: busy))
    _retry_reporter(5)(state)
    assert lines == ["gemini עמוס (503); ניסיון 3 מתוך 5 בעוד 12 שניות"]

    silent = SimpleNamespace(name="gemini", on_retry=None)  # no log attached: nothing breaks
    _retry_reporter(5)(SimpleNamespace(args=(silent,), attempt_number=1, next_action=None, outcome=None))
