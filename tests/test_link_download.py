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


class _FakeYDL:
    """yt-dlp stand-in: an embed-only Vimeo video plays only with the college site as the embedding page."""

    seen: list = []

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        assert download is False and self.options["skip_download"]
        referer = self.options.get("http_headers", {}).get("Referer", "")
        _FakeYDL.seen.append((Path(self.options["cookiefile"]).stem if self.options.get("cookiefile") else None, referer))
        if "vimeo" in url and referer != "https://yedion.pac.ac.il/":
            raise Exception("ERROR: [vimeo] 1: Cannot download embed-only video without embedding URL.")
        return {"title": "מבוא למדע הנתונים", "duration": 7380}


def test_probe_checks_a_link_like_the_app_without_downloading(tmp_path):
    folder = tmp_path / "cookies"
    files = [_cookies(folder, "college", "yedion.pac.ac.il"), _cookies(folder, "other", "example.org")]
    _FakeYDL.seen = []
    result = downloader.probe("https://player.vimeo.com/video/1?title=0", files, ydl_factory=_FakeYDL)
    assert result == {"ok": True, "title": "מבוא למדע הנתונים", "minutes": 123, "cookies": "college",
                      "referer": "https://yedion.pac.ac.il/", "error": ""}
    assert _FakeYDL.seen == [("college", ""), ("college", "https://yedion.pac.ac.il/")]  # plain first, then as embedded
    failed = downloader.probe("https://player.vimeo.com/video/1", [files[1]], ydl_factory=_FakeYDL)
    assert not failed["ok"] and "מוטמע" in failed["error"]  # the Hebrew explanation, not yt-dlp's text


def test_gate_links_stage_blocks_a_link_that_stopped_downloading(tmp_path):
    from src.release import gate as g

    links = tmp_path / "links.txt"
    assert g.stage_links(links, tmp_path).findings == []  # nothing configured: nothing to check
    links.write_text("# course lessons\nhttps://player.vimeo.com/video/1?title=0\nhttps://example.com/gone\n", encoding="utf-8")
    answers = {"https://player.vimeo.com/video/1?title=0": {"ok": True, "title": "שיעור", "minutes": 123, "cookies": "college",
                                                            "referer": "https://yedion.pac.ac.il/"},
               "https://example.com/gone": {"ok": False, "error": "הסרטון לא נמצא בכתובת הזו (404)."}}
    stage = g.stage_links(links, tmp_path, probe=lambda url, cookies: answers[url])
    assert stage.notes[0] == "1 of 2 links download" and "embedded on https://yedion.pac.ac.il/" in stage.notes[1]
    assert [(f.rule, f.blocks, f.file) for f in stage.findings] == [("LNK1", True, "https://example.com/gone")]
    crashed = g.stage_links(links, tmp_path, probe=lambda url, cookies: 1 / 0)
    assert len(crashed.findings) == 2 and "ZeroDivisionError" in crashed.findings[0].message  # reported, not a crash
