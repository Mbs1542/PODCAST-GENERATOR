"""The podcast's own folder and the Google Drive upload, against a fake Google (no network, no real keys)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.web import delivery as delivery_module  # noqa: E402
from src.web.delivery import DeliveryError, GoogleDrive, build_output_folder, output_folder_for  # noqa: E402
from src.web.server import create_app  # noqa: E402
from web_fakes import fake_runner_factory, write_fake_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CLIENT = {"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "TEST-SECRET-xyz",
                        "project_id": "demo-project", "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}
ID_TOKEN = "x." + base64.urlsafe_b64encode(json.dumps({"email": "student@example.com"}).encode()).decode().rstrip("=") + ".y"


# ------------------------------------------------------------------ the output folder

def test_output_folder_holds_lecture_transcripts_podcast_video_and_summary(tmp_path):
    lecture = tmp_path / "course" / "שיעור 3.mp4"
    lecture.parent.mkdir()
    lecture.write_bytes(b"lecture video")
    srt, txt = lecture.with_name("שיעור 3 - תמלול.srt"), lecture.with_name("שיעור 3 - תמלול.txt")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nשלום\n", encoding="utf-8")
    txt.write_text("שלום", encoding="utf-8")
    audio, video = tmp_path / "final.mp3", tmp_path / "final.mp4"
    audio.write_bytes(b"mp3")
    video.write_bytes(b"mp4")
    docx = tmp_path / "summary.docx"
    docx.write_bytes(b"docx")
    logs = []

    folder = output_folder_for(tmp_path / "course", "רגרסיה: שיעור 3?", "p1")
    assert folder.name == "רגרסיה_ שיעור 3"  # characters a file system refuses are replaced
    args = dict(lectures=[lecture], transcripts=[(srt, srt.name), (txt, txt.name)], audio=audio, video=video,
                summary_html="<html>סיכום</html>", summary_docx=docx, log=logs.append)
    files = build_output_folder(folder, "p1", "רגרסיה: שיעור 3?", **args)
    names = {f.name for f in files}
    assert names == {"שיעור 3.mp4", "שיעור 3 - תמלול.srt", "שיעור 3 - תמלול.txt", "רגרסיה_ שיעור 3 - פודקאסט.mp3",
                     "רגרסיה_ שיעור 3 - וידאו.mp4", "רגרסיה_ שיעור 3 - סיכום.html", "רגרסיה_ שיעור 3 - סיכום.docx"}
    assert (folder / "שיעור 3.mp4").stat().st_ino == lecture.stat().st_ino  # a hard link: no second copy on disk
    assert (folder / "רגרסיה_ שיעור 3 - סיכום.html").read_text(encoding="utf-8") == "<html>סיכום</html>"

    # Building again updates the same folder; another podcast with the same title gets its own
    assert output_folder_for(tmp_path / "course", "רגרסיה: שיעור 3?", "p1") == folder
    assert build_output_folder(folder, "p1", "רגרסיה: שיעור 3?", **args)
    assert output_folder_for(tmp_path / "course", "רגרסיה: שיעור 3?", "p2").name == "רגרסיה_ שיעור 3 (2)"


# ------------------------------------------------------------------ Google Drive against a fake Google

class Reply:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body


class FakeGoogle:
    """Just enough of OAuth and Drive v3 to exercise sign-in, folders and resumable uploads."""

    def __init__(self):
        self.calls, self.files, self.uploads, self.revoked = [], {}, {}, []

    def post(self, url, data=None, json=None, params=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data, json))
        if url.endswith("/token"):
            if data["grant_type"] == "authorization_code":
                return Reply(body={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "id_token": ID_TOKEN})
            return Reply(body={"access_token": "at-2", "expires_in": 3600})
        if url.endswith("/revoke"):
            self.revoked.append(data["token"])
            return Reply()
        if "upload" in url:
            session = f"https://upload.example/session/{len(self.uploads)}"
            self.uploads[session] = {"name": json["name"], "parent": json["parents"][0], "data": b"",
                                     "size": int(headers["X-Upload-Content-Length"])}
            return Reply(headers={"Location": session})
        new_id = f"folder-{len(self.files)}"
        self.files[new_id] = {"name": json["name"], "parent": json["parents"][0], "folder": True, "size": 0}
        return Reply(body={"id": new_id})

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params, None))
        assert headers["Authorization"].startswith("Bearer ")
        query = params["q"]
        if "mimeType" in query:  # folder lookup by name
            name = query.split("name = '")[1].split("'")[0]
            hits = [{"id": i} for i, f in self.files.items() if f["folder"] and f["name"] == name]
            return Reply(body={"files": hits})
        parent = query.split("'")[1]
        return Reply(body={"files": [{"id": i, "name": f["name"], "size": str(f["size"])}
                                     for i, f in self.files.items() if f["parent"] == parent and not f["folder"]]})

    def put(self, url, data=None, headers=None, timeout=None):
        upload = self.uploads[url]
        upload["data"] += data
        if len(upload["data"]) < upload["size"]:
            return Reply(308)
        file_id = f"file-{len(self.files)}"
        self.files[file_id] = {"name": upload["name"], "parent": upload["parent"], "folder": False, "size": upload["size"]}
        return Reply(200, {"id": file_id})


def test_drive_sign_in_uses_pkce_and_keeps_secrets_private(tmp_path):
    google = FakeGoogle()
    drive = GoogleDrive(tmp_path, http=google)
    with pytest.raises(DeliveryError):
        drive.save_client(b"not json")
    with pytest.raises(DeliveryError, match="Desktop app"):
        drive.save_client(json.dumps({"web": CLIENT["installed"]}).encode())
    status = drive.save_client(json.dumps(CLIENT).encode())
    assert status == {"client": True, "project": "demo-project", "connected": False, "email": ""}
    assert "TEST-SECRET" not in json.dumps(status)

    url = urlparse(drive.auth_url("http://127.0.0.1:8765/"))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query["scope"] == "https://www.googleapis.com/auth/drive.file openid email"  # only files the app creates
    assert query["code_challenge_method"] == "S256" and query["redirect_uri"] == "http://127.0.0.1:8765/"
    assert query["access_type"] == "offline"

    with pytest.raises(DeliveryError):
        drive.finish("code", "a-state-this-app-never-made")
    status = drive.finish("the-code", query["state"])
    assert status["connected"] and status["email"] == "student@example.com"
    exchange = google.calls[-1][2]
    assert hashlib.sha256(exchange["code_verifier"].encode()).digest() == \
        base64.urlsafe_b64decode(query["code_challenge"] + "=" * (-len(query["code_challenge"]) % 4))
    with pytest.raises(DeliveryError):
        drive.finish("the-code", query["state"])  # a sign-in answer is good only once
    if os.name != "nt":
        assert stat.S_IMODE(drive.token_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(drive.client_path.stat().st_mode) == 0o600

    drive.disconnect()
    assert google.revoked == ["rt-1"] and not drive.status()["connected"]


def test_drive_mirrors_a_folder_in_chunks_and_uploads_only_what_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery_module, "CHUNK_BYTES", 4)
    google = FakeGoogle()
    drive = GoogleDrive(tmp_path / "private", http=google)
    drive.save_client(json.dumps(CLIENT).encode())
    drive.finish("code", parse_qs(urlparse(drive.auth_url("http://127.0.0.1:1/")).query)["state"][0])
    folder = tmp_path / "שיעור 3"
    folder.mkdir()
    (folder / "פודקאסט.mp3").write_bytes(b"0123456789")  # 10 bytes in 4-byte chunks: 308, 308, 200
    (folder / "סיכום.html").write_text("<html/>", encoding="utf-8")
    (folder / ".mbs-output.json").write_text("{}", encoding="utf-8")
    log = []

    link = drive.upload_folder(folder, "M.B.S Studio", log.append)
    uploaded = {f["name"]: f for f in google.files.values() if not f["folder"]}
    assert set(uploaded) == {"פודקאסט.mp3", "סיכום.html"}  # the marker file stays local
    assert google.uploads and any(u["data"] == b"0123456789" for u in google.uploads.values())
    folders = {i: f for i, f in google.files.items() if f["folder"]}
    lesson_id = next(i for i, f in folders.items() if f["name"] == "שיעור 3")
    assert folders[folders[lesson_id]["parent"]]["name"] == "M.B.S Studio" and link.endswith(lesson_id)

    (folder / "וידאו.mp4").write_bytes(b"video")  # the video arrives later: only it goes up
    before = len(google.uploads)
    drive.upload_folder(folder, "M.B.S Studio", log.append)
    assert len(google.uploads) == before + 1 and log[-1] == "Google Drive: 1 קבצים הועלו"
    assert len([f for f in google.files.values() if f["folder"]]) == 2  # folders reused, not duplicated


# ------------------------------------------------------------------ the app's routes

@pytest.fixture
def client(tmp_path):
    saved_env = dict(os.environ)
    root = tmp_path / "app"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REPO / "docs" / "guide.he.html", root / "docs")
    shutil.copy(REPO / "LOGO.JPEG", root)
    write_fake_env(root, tmp_path / "outputs")
    app = create_app(root, runner_factory=fake_runner_factory())
    state = app.state.mbs
    state.allowed_hosts = {"testserver", "127.0.0.1:8765"}
    state.delivery.drive.http = FakeGoogle()
    with TestClient(app) as test_client:
        test_client.headers["X-MBS-Token"] = state.token
        test_client.mbs = state
        yield test_client
    os.environ.clear()
    os.environ.update(saved_env)


def test_settings_routes_sign_in_round_trip_without_leaking_the_secret(client):
    assert client.get("/api/delivery").json()["drive"]["client"] is False
    saved = client.post("/api/delivery/google/client", content=json.dumps(CLIENT).encode())
    assert saved.status_code == 200 and "TEST-SECRET" not in saved.text
    assert client.put("/api/delivery", json={"drive_auto": True}).status_code == 409  # not signed in yet

    url = client.post("/api/delivery/google/connect").json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    back = client.get("/", params={"state": state, "code": "abc", "scope": "x"}, follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "/#/settings?drive=connected"
    view = client.put("/api/delivery", json={"drive_auto": True, "drive_folder": "קורסים/2026"}).json()
    assert view["drive"]["email"] == "student@example.com" and view["prefs"]["drive_auto"] is True
    assert view["prefs"]["drive_folder"] == "קורסים_2026"
    assert "TEST-SECRET" not in client.get("/api/delivery").text and "rt-1" not in client.get("/api/delivery").text

    client.mbs.access_key = "a-network-password-1234"  # from another device nobody may sign this computer in
    client.cookies.clear()
    assert client.post("/api/delivery/google/connect").status_code in (401, 403)
