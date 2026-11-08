"""Network mode (container / server opened from a phone): access key, sign-in, host rules."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.web.__main__ import network_config  # noqa: E402
from src.web.server import create_app  # noqa: E402
from web_fakes import fake_runner_factory, write_fake_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
KEY = "correct horse battery"


def test_reaching_the_app_from_other_devices_requires_a_password():
    allowed, error = network_config(8765, "", "")
    assert error == "" and "127.0.0.1:8765" in allowed and "localhost:8765" in allowed

    allowed, error = network_config(8765, "10.8.0.1, studio.lan", "")
    assert "MBS_ACCESS_KEY" in error
    _, error = network_config(8765, "10.8.0.1", "short")
    assert "12 characters" in error

    allowed, error = network_config(8765, "10.8.0.1,studio.lan:8080", KEY)
    assert error == ""
    assert {"10.8.0.1:8765", "10.8.0.1", "studio.lan:8080"} <= allowed
    assert network_config(8765, "localhost", "")[1] == ""  # local names alone need no password


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
    state.allowed_hosts = {"testserver", "10.8.0.1:8765"}
    state.access_key = KEY
    with TestClient(app, base_url="http://10.8.0.1:8765") as test_client:
        test_client.mbs = state
        yield test_client
    os.environ.clear()
    os.environ.update(saved_env)


def test_sign_in_guards_the_page_and_the_api(client):
    page = client.get("/")
    assert page.status_code == 401 and 'name="key"' in page.text
    assert client.mbs.token not in page.text  # the API token is only in the page after sign-in
    assert client.get("/api/projects", headers={"X-MBS-Token": client.mbs.token}).json() == {"detail": "sign in first"}
    assert client.get("/static/app.css").status_code == 200  # the sign-in page needs its styles
    assert client.get("/api/hello").json() == {"app": "mbs-studio"}

    wrong = client.post("/login", data={"key": "guess"}, follow_redirects=False)
    assert wrong.status_code == 401 and "שגויה" in wrong.text

    ok = client.post("/login", data={"key": KEY}, follow_redirects=False)
    assert ok.status_code == 303
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("samesite", "SameSite")
    page = client.get("/")
    assert page.status_code == 200 and client.mbs.token in page.text
    assert client.get("/api/projects", headers={"X-MBS-Token": client.mbs.token}).status_code == 200

    client.post("/logout", follow_redirects=False)
    assert client.get("/").status_code == 401


def test_password_guessing_is_slowed_and_then_blocked(client, monkeypatch):
    import src.web.server as server

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(server.asyncio, "sleep", no_wait)
    for _ in range(server.MAX_FAILED_LOGINS):
        assert client.post("/login", data={"key": "nope"}, follow_redirects=False).status_code == 401
    blocked = client.post("/login", data={"key": KEY}, follow_redirects=False)
    assert blocked.status_code == 429  # even the right key waits out the lockout


def test_container_paths_come_from_the_environment(tmp_path, monkeypatch):
    from src.transcribe.whisper import default_models_dir
    from src.web.settings_store import SettingsStore

    monkeypatch.setenv("MBS_ENV_FILE", str(tmp_path / "data" / ".env"))
    monkeypatch.setenv("MBS_MODELS_DIR", str(tmp_path / "data" / "models"))
    assert SettingsStore(tmp_path).env_path == tmp_path / "data" / ".env"
    assert default_models_dir() == tmp_path / "data" / "models"


def test_the_image_never_contains_keys_or_personal_data():
    ignored = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".env", "studio-data/", "outputs/", "models/", ".git/"):
        assert entry in ignored
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "USER mbs" in dockerfile and "MBS_ENV_FILE=/data/.env" in dockerfile
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8765:8765"' in compose  # this computer only, unless the user opens it up
