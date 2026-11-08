"""Run the browser UI against fake services, to click through it without any keys or costs.

    python tests/web_demo_server.py [folder] [--port 8799]

Uses a throwaway app folder (fake .env, its own outputs). The script, plan and audio are
produced by stand-ins: the dialogue is placeholder text and the voices are tones.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(REPO), str(HERE)]

import uvicorn  # noqa: E402

from src.web.server import create_app  # noqa: E402
from web_fakes import fake_runner_factory, write_fake_env  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", default="")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()

    root = Path(args.folder) if args.folder else Path(tempfile.mkdtemp(prefix="mbs-demo-"))
    (root / "docs").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "docs" / "guide.he.html", root / "docs")
    shutil.copy(REPO / "LOGO.JPEG", root)
    if not (root / ".env").exists():
        write_fake_env(root, root / "outputs")

    app = create_app(root, runner_factory=fake_runner_factory(delay=0.4))
    app.state.mbs.allowed_hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
    print(f"Demo UI: http://127.0.0.1:{args.port}/  (data in {root})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
