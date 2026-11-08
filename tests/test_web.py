"""Browser API end to end: upload -> plan -> edit -> script -> audio, with fake services."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.web.server import create_app  # noqa: E402
from web_fakes import SAMPLE, fake_runner_factory, write_fake_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    saved_env = dict(os.environ)
    root = tmp_path / "app"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REPO / "docs" / "guide.he.html", root / "docs")
    shutil.copy(REPO / "LOGO.JPEG", root)
    write_fake_env(root, tmp_path / "outputs")

    # The real stitcher needs ffmpeg for MP3; here it only has to produce the file
    def fake_build(self, segments, metadata, run_paths):
        run_paths.final_audio_path.write_bytes(b"ID3fake")
        return run_paths.final_audio_path

    monkeypatch.setattr("src.audio.PodcastStitcher.build", fake_build)
    app = create_app(root, runner_factory=fake_runner_factory())
    state = app.state.mbs
    state.allowed_hosts = {"testserver"}
    with TestClient(app) as test_client:
        test_client.headers["X-MBS-Token"] = state.token
        test_client.mbs = state
        yield test_client
    os.environ.clear()
    os.environ.update(saved_env)


def wait_idle(client, project_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        project = client.get(f"/api/projects/{project_id}").json()
        if not project["running"]:
            return project
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_requests_without_token_or_from_another_host_are_refused(client):
    assert client.get("/api/hello").json() == {"app": "mbs-studio"}
    assert client.get("/api/projects", headers={"X-MBS-Token": "wrong"}).status_code == 401
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 403
    page = client.get("/").text
    assert client.mbs.token in page and "__MBS_TOKEN__" not in page


def test_full_flow_from_transcript_to_audio(client):
    created = client.post(
        "/api/projects/upload",
        params={"filename": "lecture.txt", "title": "רשתות בענן", "format": "auto", "minutes": "0", "voice": "auto"},
        content=SAMPLE.read_bytes(),
    )
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    project = wait_idle(client, pid)
    assert project["status"] == "plan_ready", project
    plan = client.get(f"/api/projects/{pid}/plan").json()["plan"]
    body = [s for s in plan["segments"] if s["kind"] == "body"]
    assert len(body) == 2

    # Edit: rename, drop the second body segment, make the first one long
    plan["segments"][1]["title"] = "מה זה VNet, באמת"
    plan["segments"][1]["weight"] = 3
    plan["segments"] = [s for s in plan["segments"] if s is not body[1]]
    plan["target_minutes"] = 3
    saved = client.put(f"/api/projects/{pid}/plan", json={"plan": plan}).json()["plan"]
    assert [s["kind"] for s in saved["segments"]] == ["intro", "body", "closing"]
    assert saved["segments"][1]["title"] == "מה זה VNet, באמת"
    assert abs(saved["target_words"] - 3 * 135) < 70

    assert client.post(f"/api/projects/{pid}/jobs/write", json={}).status_code == 200
    project = wait_idle(client, pid)
    assert project["status"] == "script_ready", project
    script = client.get(f"/api/projects/{pid}/script").json()
    titles = [c["title"] for c in script["dialogue"]["metadata"]["chapters"]]
    assert titles[1] == "מה זה VNet, באמת" and len(titles) == 3  # the edited plan was followed
    text = client.get(f"/api/projects/{pid}/script.txt").text
    assert "רועי:" in text and "## מה זה VNet, באמת" in text

    assert client.post(f"/api/projects/{pid}/jobs/audio", json={}).status_code == 200
    project = wait_idle(client, pid)
    assert project["status"] == "audio_ready", project
    assert "audio" in project["files"]
    timing = client.get(f"/api/projects/{pid}/script").json()["timing"]
    assert [c["title"] for c in timing["chapters"]] == titles
    assert timing["chapters"][0]["start"] == pytest.approx(3.0)  # after the 3-second intro
    audio = client.get(f"/api/projects/{pid}/files/audio")
    assert audio.status_code == 200 and audio.content == b"ID3fake"


def test_conversation_voices_render_chapters_from_chunks(client):
    pid = client.post(
        "/api/projects/upload",
        params={"filename": "lecture.txt", "voice": "gemini", "focus": "תתמקדו בהבדל בין NSG ל-Load Balancer"},
        content=SAMPLE.read_bytes(),
    ).json()["id"]
    project = wait_idle(client, pid)
    assert project["options"]["focus"].startswith("תתמקדו")
    plan = client.get(f"/api/projects/{pid}/plan").json()["plan"]
    assert plan["focus"].startswith("תתמקדו")  # the focus reached the planner
    client.post(f"/api/projects/{pid}/jobs/write", json={})
    wait_idle(client, pid)
    client.post(f"/api/projects/{pid}/jobs/audio", json={})
    project = wait_idle(client, pid)
    assert project["status"] == "audio_ready", project
    assert any("Chunk" in line for line in project["log"])  # rendered as a conversation, not line by line
    timing = client.get(f"/api/projects/{pid}/script").json()["timing"]
    starts = [c["start"] for c in timing["chapters"]]
    assert starts == sorted(starts) and starts[0] == pytest.approx(3.0)


def test_video_from_the_gallery_with_any_voices(client):
    pid = client.post("/api/projects/upload", params={"filename": "l.txt", "voice": "gemini", "format": "brief"},
                      content=SAMPLE.read_bytes()).json()["id"]
    wait_idle(client, pid)
    assert client.get(f"/api/projects/{pid}/plan").json()["plan"]["format"] == "brief"  # the chosen direction
    client.post(f"/api/projects/{pid}/jobs/write", json={})
    wait_idle(client, pid)

    # Video needs the episode audio first
    client.post(f"/api/projects/{pid}/jobs/video", json={})
    assert "פודקאסט" in wait_idle(client, pid)["error"]
    client.post(f"/api/projects/{pid}/jobs/audio", json={})
    wait_idle(client, pid)

    # Gallery: two uploads, two AI images, reorder, drop one, change the rotation
    from web_fakes import png_bytes

    for color in ((200, 30, 30), (30, 200, 30)):
        assert client.post(f"/api/projects/{pid}/gallery/upload", params={"filename": "mine.png"},
                           content=png_bytes(color)).status_code == 200
    assert client.post(f"/api/projects/{pid}/gallery/upload", params={"filename": "x.gif"}, content=b"GIF89a").status_code == 400
    client.post(f"/api/projects/{pid}/jobs/images", json={"count": 2, "style": "photo"})
    project = wait_idle(client, pid)
    assert not project["error"], project["error"]
    items = client.get(f"/api/projects/{pid}/gallery").json()["items"]
    assert [i["source"] for i in items] == ["upload", "upload", "ai", "ai"]
    assert "no text" in items[2]["prompt"].lower() or "no text, letters" in items[2]["prompt"].lower()
    assert client.get(f"/api/projects/{pid}/gallery/{items[0]['id']}/thumb").status_code == 200

    order = [items[2], items[0], items[1], items[3]]
    payload = [{"id": i["id"], "selected": i["id"] != items[1]["id"]} for i in order]
    saved = client.put(f"/api/projects/{pid}/gallery", json={"items": payload, "rotate_seconds": 5}).json()
    assert [i["id"] for i in saved["items"]] == [i["id"] for i in order] and saved["rotate_seconds"] == 5
    assert client.delete(f"/api/projects/{pid}/gallery/{items[3]['id']}").status_code == 200

    preview = client.post("/api/brand/preview", json={"project": pid, "image": items[2]["id"], "kind": "chapter"})
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"

    assert client.post(f"/api/projects/{pid}/jobs/video", json={}).status_code == 200
    project = wait_idle(client, pid)
    assert project["status"] == "video_ready", project["error"]
    assert "video" in project["files"]
    folder = client.mbs.store.folder(client.mbs.store.get(pid))
    timeline = json.loads((folder / "slides" / "timeline.json").read_text(encoding="utf-8"))
    used = {s["image"] for s in timeline if s["image"] is not None}
    assert used == {0, 1}  # the two selected images that are left, cycling
    assert timeline[0]["image"] is None and timeline[-1]["end"] == pytest.approx(60.0)


def test_look_and_branding(client, monkeypatch):
    from web_fakes import png_bytes

    view = client.get("/api/brand").json()
    assert view["prefs"] == {"theme": "auto", "accent": "teal", "logo": "default", "font_he": "auto", "font_en": "auto"}
    assert {"heebo", "inter"} <= {f["id"][2:] for f in view["fonts"] if f["source"] == "google"}

    assert client.put("/api/brand", json={"prefs": {"theme": "dark", "accent": "purple"}}).json()["prefs"]["theme"] == "dark"
    assert client.put("/api/brand", json={"prefs": {"theme": "neon"}}).status_code == 400
    assert client.put("/api/brand", json={"prefs": {"font_he": "g:heebo"}}).status_code == 400  # not downloaded yet

    # A logo upload is selected right away and served as the app's logo
    logo = client.post("/api/brand/logos", params={"filename": "my logo.png"}, content=png_bytes((250, 200, 0))).json()
    chosen = logo["prefs"]["logo"]
    assert chosen.startswith("my_logo-") and chosen.endswith(".png")
    assert client.get("/logo").content == png_bytes((250, 200, 0))
    assert client.post("/api/brand/logos", params={"filename": "evil.png"}, content=b"not an image").status_code == 400
    assert client.post("/api/brand/logos", params={"filename": "../../x.exe"}, content=b"MZ").status_code == 400

    # Font download (network stubbed with a real font file) and selection
    system_font = next((p for p in (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/segoeui.ttf")) if p.exists()), None)
    if system_font is None:
        pytest.skip("no TrueType font on this machine to stand in for the download")

    class FakeHTTP:
        def get(self, url, timeout):
            assert url.startswith("https://raw.githubusercontent.com/google/fonts/main/ofl/heebo/")
            return type("R", (), {"content": system_font.read_bytes(), "raise_for_status": lambda self: None})()

    client.mbs.brand.http = FakeHTTP()
    view = client.post("/api/brand/fonts/download/heebo").json()
    assert next(f for f in view["fonts"] if f["id"] == "g:heebo")["installed"]
    assert client.put("/api/brand", json={"prefs": {"font_he": "g:heebo"}}).json()["prefs"]["font_he"] == "g:heebo"
    assert client.get("/api/brand/fonts/g:heebo/file").status_code == 200
    assert client.post("/api/brand/fonts", params={"filename": "bad.ttf"}, content=b"nope").status_code == 400
    assert client.delete("/api/brand/fonts/s:arial.ttf").status_code == 400  # system fonts stay

    for kind in ("intro", "chapter"):
        response = client.post("/api/brand/preview", json={"kind": kind, "language": "he"})
        assert response.status_code == 200 and len(response.content) > 1000

    # Deleting the chosen logo falls back to the default
    assert client.delete(f"/api/brand/logos/{chosen}").json()["prefs"]["logo"] == "default"


def test_media_upload_is_transcribed_first(client):
    pid = client.post("/api/projects/upload", params={"filename": "lecture.mp4"}, content=b"\x00" * 64).json()["id"]
    project = wait_idle(client, pid)
    assert project["status"] == "plan_ready"
    assert {"transcript_srt", "transcript_txt"} <= set(project["files"])
    assert project["title"] == "lecture"


def test_without_any_writer_the_project_stops_after_transcription(client):
    env = client.mbs.settings_store
    env.write_env({"AZURE_OPENAI_API_KEY": None, "GEMINI_API_KEY": None})
    os.environ.pop("AZURE_OPENAI_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)
    pid = client.post("/api/projects/upload", params={"filename": "talk.m4a"}, content=b"\x00" * 64).json()["id"]
    project = wait_idle(client, pid)
    assert project["status"] == "transcribed"
    assert "מי כותב" in project["notice"]
    status = client.get("/api/status").json()
    assert status["settings_ok"] is False and status["writer"]["ready"] is False


def test_a_gemini_key_alone_is_enough_to_write(client):
    env = client.mbs.settings_store
    env.write_env({"AZURE_OPENAI_ENDPOINT": None, "AZURE_OPENAI_API_KEY": None, "AZURE_OPENAI_DEPLOYMENT": None})
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        os.environ.pop(key, None)
    status = client.get("/api/status").json()
    assert status["settings_ok"] is True
    assert status["writer"] == {"provider": "gemini", "ready": True, "model": "gemini-3.8-flash"}
    pid = client.post("/api/projects/upload", params={"filename": "l.txt"}, content=SAMPLE.read_bytes()).json()["id"]
    assert wait_idle(client, pid)["status"] == "plan_ready"


def test_settings_never_return_secrets_and_keep_unknown_lines(client):
    root = client.mbs.root
    (root / ".env").write_text((root / ".env").read_text(encoding="utf-8") + "# my note\nCUSTOM_FLAG=1\n", encoding="utf-8")
    fields = {f["key"]: f for f in client.get("/api/settings").json()["fields"]}
    key_field = fields["AZURE_OPENAI_API_KEY"]
    assert key_field["is_set"] and "value" not in key_field and key_field["hint"] == "…0000"

    response = client.put("/api/settings", json={"values": {"AZURE_OPENAI_API_KEY": "", "AZURE_SPEECH_REGION": "northeurope",
                                                           "PODCAST_FORMAT": "exam_prep", "AZURE_OPENAI_API_VERSION": "",
                                                           "LOCAL_LLM": ""}})
    assert response.status_code == 200
    env = (root / ".env").read_text(encoding="utf-8")
    assert "AZURE_OPENAI_API_KEY=fake-key-for-tests-0000" in env  # empty secret = keep
    assert "AZURE_SPEECH_REGION=northeurope" in env and "PODCAST_FORMAT=exam_prep" in env
    # An empty setting means "default": no empty line that would override the default with ""
    assert "AZURE_OPENAI_API_VERSION" not in env and "LOCAL_LLM=" not in env
    assert "# my note" in env and "CUSTOM_FLAG=1" in env
    assert client.put("/api/settings", json={"values": {"PODCAST_FORMAT": "nonsense"}}).status_code == 400


def test_upload_rejects_unknown_types_and_path_tricks(client):
    assert client.post("/api/projects/upload", params={"filename": "virus.exe"}, content=b"x").status_code == 400
    assert client.get("/api/projects/..%5C..%5Cetc").status_code == 404
