"""The phone app (/m/): its page, worker and manifest, and the summary as JSON that it draws itself."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.version import __version__  # noqa: E402
from src.web.server import create_app  # noqa: E402
from web_fakes import fake_runner_factory, write_fake_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MOBILE = REPO / "src" / "web" / "static" / "m"


@pytest.fixture
def client(tmp_path):
    saved_env = dict(os.environ)
    root = tmp_path / "app"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REPO / "docs" / "guide.he.html", root / "docs")
    shutil.copy(REPO / "LOGO.JPEG", root)
    write_fake_env(root, tmp_path / "outputs")
    app = create_app(root, runner_factory=fake_runner_factory())
    with TestClient(app) as test_client:
        test_client.mbs = app.state.mbs
        yield test_client
    os.environ.clear()
    os.environ.update(saved_env)


def test_the_phone_page_carries_the_token_and_version(client):
    page = client.get("/m/")
    assert page.status_code == 200
    assert client.mbs.token in page.text and f'content="{__version__}"' in page.text
    assert "__MBS_" not in page.text
    assert client.get("/m", follow_redirects=False).headers["location"] == "/m/"


def test_the_worker_names_its_caches_by_version_and_its_shell_files_exist(client):
    worker = client.get("/m/sw.js")
    assert worker.status_code == 200 and "javascript" in worker.headers["content-type"]
    assert f'const VERSION = "{__version__}"' in worker.text and "__MBS_" not in worker.text
    # Every file the worker pre-caches must exist, or its install fails and nothing works offline
    for path in re.findall(r'"(/static/m/[^"?]+)', worker.text) + ["/static/m/m.css", "/static/m/m.js"]:
        assert client.get(path).status_code == 200, path


def test_the_manifest_installs_the_phone_app_with_real_icons(client):
    res = client.get("/m/manifest.webmanifest")
    assert res.headers["content-type"].startswith("application/manifest+json")
    manifest = json.loads(res.content)
    assert manifest["start_url"] == "/m/" and manifest["scope"] == "/m/" and manifest["display"] == "standalone"
    assert manifest["dir"] == "rtl" and manifest["lang"] == "he"
    assert {i["purpose"] for i in manifest["icons"]} == {"any", "maskable"}
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]


def test_the_summary_comes_as_json_for_the_phone(client):
    store = client.mbs.store
    project = store.create("שיעור 3", "transcript", "x.srt", {})
    profile = {"title": "רגרסיה", "summary": "<b>בקצרה</b>", "concepts": [{"name": "MSE", "definition": "ממוצע ריבועי השגיאה", "importance": 3}]}
    engine = store.engine_dir(project)
    engine.mkdir(parents=True, exist_ok=True)
    (engine / "02_profile.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    headers = {"X-MBS-Token": client.mbs.token}

    model = client.get(f"/api/projects/{project.id}/summary?format=json", headers=headers).json()
    assert model["concepts"][0]["name"] == "MSE" and model["summary"] == "<b>בקצרה</b>"  # raw: the page escapes it
    assert client.get(f"/api/projects/{project.id}/summary?format=json").status_code == 401
    assert client.get(f"/api/projects/{project.id}/summary?format=xml", headers=headers).status_code == 400


def test_behind_access_the_phone_app_needs_the_gate_too(client):
    client.mbs.external_auth = "cloudflare-access"
    for path in ("/m/", "/m/sw.js", "/m/manifest.webmanifest", "/static/m/m.js"):
        assert client.get(path).status_code == 403, path
    assert client.get("/m/", headers={"Cf-Access-Jwt-Assertion": "a.b.c"}).status_code == 200


def test_the_phone_script_escapes_what_it_draws():
    """Every server value that reaches innerHTML in m.js passes through esc(); spot-check the risky ones."""
    script = (MOBILE / "m.js").read_text(encoding="utf-8")
    for field in ("p.title", "item.title", "e.title", "c.name", "c.definition", "t.text", "turn.text", "p.error", "item.error"):
        raw = re.findall(r"\$\{" + re.escape(field) + r"\}", script)
        assert not raw, f"{field} reaches the page unescaped"
