"""Behind Cloudflare Access (MBS_EXTERNAL_AUTH=cloudflare-access): no password of the app's own,
and every non-public request must carry the JWT header the reverse proxy verified."""

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
JWT = {"Cf-Access-Jwt-Assertion": "header.payload.signature"}


def test_a_proxy_login_replaces_the_password_requirement():
    allowed, error = network_config(8765, "studio.example.org,podcast-studio:8765", "", "cloudflare-access")
    assert error == "" and {"studio.example.org", "podcast-studio:8765"} <= allowed
    assert "MBS_ACCESS_KEY" in network_config(8765, "studio.example.org", "", "")[1]  # still required without it
    assert "MBS_EXTERNAL_AUTH" in network_config(8765, "studio.example.org", "", "basic")[1]


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
    state.allowed_hosts = {"studio.example.org"}
    state.external_auth = "cloudflare-access"
    with TestClient(app, base_url="http://studio.example.org") as test_client:
        test_client.mbs = state
        yield test_client
    os.environ.clear()
    os.environ.update(saved_env)


def test_requests_without_the_access_header_are_refused(client):
    assert client.get("/").status_code == 403
    assert client.get("/static/app.css").status_code == 403
    assert client.get("/api/projects", headers={"X-MBS-Token": client.mbs.token}).status_code == 403
    assert client.get("/api/hello").json() == {"app": "mbs-studio"}  # the container healthcheck


def test_requests_through_the_gate_need_no_password(client):
    page = client.get("/", headers=JWT)
    assert page.status_code == 200 and client.mbs.token in page.text
    assert client.get("/api/projects", headers={**JWT, "X-MBS-Token": client.mbs.token}).status_code == 200
    assert client.get("/api/projects", headers=JWT).status_code == 401  # the per-launch token still applies
