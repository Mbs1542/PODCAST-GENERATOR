"""Tools screen: downloads, transcriptions, cookies and 'make a podcast', with fake network and model."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.media import DownloadCancelled, DownloadError, DownloadRequest, build_options, download, parse_cookies, site_profile  # noqa: E402
from src.transcribe import TranscriptSegment  # noqa: E402
from src.web.media import MediaRunner, parse_urls  # noqa: E402
from src.web.server import create_app  # noqa: E402
from web_fakes import fake_runner_factory, write_fake_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
COOKIES = (
    "# Netscape HTTP Cookie File\n"
    "#HttpOnly_.johnbryce.sylla.academy\tTRUE\t/\tTRUE\t1893456000\twordpress_logged_in_abc\tSECRETVALUE\n"
    ".sylla.academy\tTRUE\t/\tFALSE\t1999999999\tpref\tx\n"
)


# ------------------------------------------------------------------ downloader

def test_site_profiles_and_safe_options(tmp_path):
    sylla = site_profile("https://johnbryce.sylla.academy/courses/x/lesson-3")
    assert sylla.needs_cookies and sylla.referer == "https://johnbryce.sylla.academy/"
    nachos = site_profile("https://nachostv.com/watch/1")
    assert nachos.impersonate == "chrome" and nachos.single_video
    assert site_profile("https://www.youtube.com/watch?v=x").name == "generic"

    logs = []
    options = build_options(DownloadRequest("https://youtu.be/x", tmp_path, quality="audio"), site_profile("https://youtu.be/x"), logs.append)
    assert options["format"] == "bestaudio/best" and options["noplaylist"]
    assert any(p["key"] == "FFmpegExtractAudio" for p in options["postprocessors"])
    assert "nocheckcertificate" not in options  # TLS stays verified, unlike the old .bat
    course = build_options(DownloadRequest("https://johnbryce.sylla.academy/c", tmp_path, playlist=True, cookies=tmp_path / "c.txt"),
                           sylla, logs.append)
    assert course["outtmpl"].endswith("%(playlist_index)03d - %(title).90B.%(ext)s")
    assert course["cookiefile"].endswith("c.txt") and course["http_headers"]["Referer"].startswith("https://johnbryce")
    assert course["subtitleslangs"] == ["he", "en"] and course["merge_output_format"] == "mp4"
    with pytest.raises(DownloadError):
        build_options(DownloadRequest("https://x.y", tmp_path, quality="8k"), site_profile("https://x.y"), logs.append)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: reports progress, writes files, calls the post hooks."""

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        folder = Path(self.options["outtmpl"]).parent
        for index in (1, 2):
            info = {"playlist_index": index, "n_entries": 2}
            for hook in self.options["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10, "info_dict": info, "speed": 1048576})
            path = folder / f"{index:03d} - lesson.mp4"
            path.write_bytes(b"video")
            for hook in self.options["post_hooks"]:
                hook(str(path))
        (folder / "lesson.he.vtt").write_text("x")  # subtitles are not counted as media


def test_download_reports_progress_and_files(tmp_path):
    seen = []
    files = download(DownloadRequest("https://www.youtube.com/playlist?list=x", tmp_path, playlist=True),
                     progress=lambda f, label: seen.append((round(f, 2), label)), ydl_factory=FakeYDL)
    assert [f.name for f in files] == ["001 - lesson.mp4", "002 - lesson.mp4"]
    assert seen[-1] == (1.0, "הסתיים") and any("פריט 2 מתוך 2" in label for _, label in seen)


def test_download_errors_are_explained_and_cancel_works(tmp_path):
    import threading

    class Forbidden(FakeYDL):
        def download(self, urls):
            raise RuntimeError("ERROR: [vimeo] 123: HTTP Error 403: Forbidden")

    with pytest.raises(DownloadError, match="cookies"):
        download(DownloadRequest("https://player.vimeo.com/video/1", tmp_path), ydl_factory=Forbidden)

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        download(DownloadRequest("https://youtu.be/x", tmp_path), cancel=cancel, ydl_factory=FakeYDL)


def test_embed_only_vimeo_retries_from_the_cookies_site(tmp_path):
    embed_error = ("ERROR: [vimeo] 1: Cannot download embed-only video without embedding URL. "
                   "Please call yt-dlp with the URL of the page that embeds this video.")
    tried = []

    class EmbedOnly(FakeYDL):
        def download(self, urls):
            referer = self.options.get("http_headers", {}).get("Referer")
            tried.append(referer)
            if referer != "https://johnbryce.sylla.academy/":
                raise RuntimeError(embed_error)
            super().download(urls)

    cookies = tmp_path / "cookies.txt"
    cookies.write_text(COOKIES, encoding="utf-8")
    url = "https://player.vimeo.com/video/1?title=0"
    files = download(DownloadRequest(url, tmp_path / "out", cookies=cookies), ydl_factory=EmbedOnly)
    assert files and tried == [None, "https://johnbryce.sylla.academy/"]  # most specific domain first

    with pytest.raises(DownloadError, match="Referer"):  # no cookies: explain what to do
        download(DownloadRequest(url, tmp_path / "out2"), ydl_factory=EmbedOnly)


def test_cookies_are_parsed_without_keeping_values():
    info = parse_cookies(COOKIES)
    assert info.count == 2 and info.domains == ["johnbryce.sylla.academy", "sylla.academy"]
    assert info.login_expires == 1893456000.0
    with pytest.raises(DownloadError):
        parse_cookies("just some text")


def test_url_lists_are_cleaned():
    text = "https://a.com/1\nhttp://a.com/2 , https://a.com/1\nnot-a-url ftp://x"
    assert parse_urls(text) == ["https://a.com/1", "https://a.com/2"]


# ------------------------------------------------------------------ app

class FakeTranscriber:
    calls = []

    def __init__(self, language, log):
        self.language = language

    def transcribe(self, media, progress=None):
        FakeTranscriber.calls.append((Path(media).name, self.language))
        if progress:
            progress(30.0, 60.0)
        return [TranscriptSegment(0.0, 4.0, "שלום וברוכים הבאים לשיעור."), TranscriptSegment(4.5, 9.0, "היום נלמד על התא.")]


def fake_download(request, progress=None, log=None, cancel=None):
    request.folder.mkdir(parents=True, exist_ok=True)
    progress(0.5, "חצי")
    if "fail" in request.url:
        raise DownloadError("ההורדה נכשלה: test")
    path = request.folder / ("lesson.mp3" if request.quality == "audio" else "lesson.mp4")
    path.write_bytes(b"media")
    log(f"cookies={'yes' if request.cookies else 'no'} referer={request.referer}")
    return [path]


@pytest.fixture
def client(tmp_path):
    saved_env = dict(os.environ)
    root = tmp_path / "app"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REPO / "docs" / "guide.he.html", root / "docs")
    shutil.copy(REPO / "LOGO.JPEG", root)
    write_fake_env(root, tmp_path / "outputs")
    FakeTranscriber.calls = []
    app = create_app(root, runner_factory=fake_runner_factory(),
                     media_runner_factory=lambda store: MediaRunner(store, downloader=fake_download, transcriber_factory=FakeTranscriber))
    state = app.state.mbs
    state.allowed_hosts = {"testserver"}
    with TestClient(app) as test_client:
        test_client.headers["X-MBS-Token"] = state.token
        test_client.mbs = state
        yield test_client
    os.environ.clear()
    os.environ.update(saved_env)


def wait_media(client, item_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/media/{item_id}").json()
        if item["status"] not in ("queued", "running", "uploading"):
            return item
        time.sleep(0.05)
    raise AssertionError("media job did not finish")


def test_download_then_transcribe_then_podcast(client):
    saved = client.post("/api/media/cookies", params={"name": "sylla"}, content=COOKIES.encode()).json()["cookies"]
    assert saved[0]["id"] == "sylla" and saved[0]["domains"][0] == "johnbryce.sylla.academy"
    assert "SECRETVALUE" not in client.get("/api/media").text  # cookie values never leave the server

    created = client.post("/api/media/download", json={
        "urls": "https://johnbryce.sylla.academy/l1\nhttps://johnbryce.sylla.academy/fail", "quality": "best",
        "cookies": "sylla", "transcribe": True, "language": "he"}).json()["items"]
    assert len(created) == 2
    ok, failed = (wait_media(client, i["id"]) for i in created)
    assert ok["status"] == "done" and ok["files"][0]["kind"] == "video"
    assert ok["title"] == "lesson"  # the video's own name replaces the bare link
    assert any("cookies=yes" in line for line in ok["log"])
    assert failed["status"] == "error" and "test" in failed["error"]

    # "transcribe after download" queued a transcription automatically
    deadline = time.time() + 10
    while time.time() < deadline:
        items = client.get("/api/media").json()["items"]
        transcription = next((i for i in items if i["kind"] == "transcription"), None)
        if transcription and transcription["status"] == "done":
            break
        time.sleep(0.05)
    assert transcription["parent"] == ok["id"] and FakeTranscriber.calls == [("lesson.mp4", "he")]
    assert [f["kind"] for f in transcription["files"]] == ["subtitles", "text"]
    srt = client.get(f"/api/media/{transcription['id']}/files/0", params={"download": 1})
    assert srt.status_code == 200 and "00:00:00,000 --> 00:00:04,000" in srt.text

    project = client.post(f"/api/media/{transcription['id']}/podcast", json={"format": "brief"}).json()["project"]
    deadline = time.time() + 10
    while time.time() < deadline:
        p = client.get(f"/api/projects/{project}").json()
        if not p["running"]:
            break
        time.sleep(0.05)
    assert p["status"] == "plan_ready" and p["options"]["format"] == "brief"
    assert client.get(f"/api/media/{transcription['id']}").json()["project"] == project

    assert client.delete(f"/api/media/cookies/sylla").json()["cookies"] == []


def test_upload_for_transcription_and_housekeeping(client):
    assert client.post("/api/media/upload", params={"filename": "notes.pdf"}, content=b"x").status_code == 400
    item = client.post("/api/media/upload", params={"filename": "הרצאה 3.m4a", "language": "en"}, content=b"audio").json()
    done = wait_media(client, item["id"])
    assert done["status"] == "done" and FakeTranscriber.calls[-1] == ("הרצאה 3.m4a", "en")
    assert "media" not in done["options"]  # no local paths in the UI
    txt = client.get(f"/api/media/{item['id']}/files/1").text
    assert "שלום וברוכים הבאים" in txt

    assert client.post(f"/api/media/{item['id']}/transcribe", json={}).status_code == 409  # only downloads
    assert client.get(f"/api/media/{item['id']}/files/9").status_code == 404
    assert client.get("/api/media/..%2F..%2Fsecret").status_code == 404
    assert client.delete(f"/api/media/{item['id']}").json() == {"ok": True}
    assert client.get(f"/api/media/{item['id']}").status_code == 404
    assert client.post("/api/media/download", json={"urls": "nothing here"}).status_code == 400
    assert client.post("/api/media/cookies", content=b"not cookies").status_code == 400
