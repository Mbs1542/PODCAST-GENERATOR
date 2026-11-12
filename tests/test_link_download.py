"""A new project from a link downloads like the Tools downloader: saved cookies, and embed-only Vimeo links."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import src.media.downloader as downloader
from src.web.jobs import JobError, JobRunner

COOKIE = "# Netscape HTTP Cookie File\n.{domain}\tTRUE\t/\tTRUE\t4102444800\tsession\tabc\n"


def _cookies(folder: Path, name: str, domain: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.txt"
    path.write_text(COOKIE.format(domain=domain), encoding="utf-8")
    return path


def test_cookie_candidates_put_the_site_first_and_try_every_site_for_vimeo(tmp_path):
    college = _cookies(tmp_path, "college", "yedion.pac.ac.il")
    course = _cookies(tmp_path, "course", "sylla.academy")
    files = [college, course]
    assert downloader.cookies_for("https://johnbryce.sylla.academy/lesson/1", files) == [course, None]
    assert downloader.cookies_for("https://player.vimeo.com/video/1?title=0", files) == [college, course, None]
    assert downloader.cookies_for("https://www.youtube.com/watch?v=x", files) == [None]


def _runner(tmp_path):
    log, updates = [], []
    store = SimpleNamespace(root=tmp_path / "outputs" / "projects", append_log=lambda pid, line: log.append(line),
                            update=lambda pid, **kw: updates.append(kw))
    return SimpleNamespace(store=store), log, updates


def test_project_link_uses_saved_cookies_and_moves_on_when_one_site_fails(tmp_path, monkeypatch):
    runner, log, updates = _runner(tmp_path)
    folder = tmp_path / "outputs" / "private" / "cookies"
    _cookies(folder, "a_other", "example.org")
    _cookies(folder, "b_college", "yedion.pac.ac.il")
    tried = []

    def fake_download(request, progress=None, log=None, cancel=None, ydl_factory=None):
        tried.append(request.cookies.stem if request.cookies else None)
        assert request.quality == "audio" and not request.subtitles
        if request.cookies is None or request.cookies.stem != "b_college":
            raise downloader.DownloadError("הסרטון ב-Vimeo מוטמע באתר ומתנגן רק מתוכו.")
        progress(0.5, "מוריד")
        out = request.folder / "lecture.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return [request.folder / "lecture.he.vtt", out]

    monkeypatch.setattr(downloader, "download", fake_download)
    project = SimpleNamespace(id="p1", source_name="https://player.vimeo.com/video/1242150951?title=0")
    media = JobRunner._download_link(runner, project, tmp_path / "src")
    assert media.name == "lecture.mp3" and tried == ["a_other", "b_college"]
    assert any("b_college" in line for line in log) and {"stage": "מוריד את ההקלטה", "progress": 0.5} in updates

    tried.clear()
    project.source_name = "https://example.com/video"  # no cookies for this site and the download fails: a clear error
    with pytest.raises(JobError, match="מוטמע"):
        JobRunner._download_link(runner, project, tmp_path / "src2")
    assert tried == [None]
