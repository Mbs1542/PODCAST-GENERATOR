"""Start M.B.S Studio in the browser.

    python -m src.web                 # start (or reuse a running copy) and open the browser
    python -m src.web --no-browser    # start without opening a tab
    python -m src.web --stay          # don't exit when the browser tab is closed

Started from the Start menu with pythonw (no console window), so the server stops by
itself a few minutes after the last browser tab closes, unless a job is still running.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORT = 8765
IDLE_EXIT_SECONDS = 180


def _running_instance(port: int) -> bool:
    try:
        return requests.get(f"http://127.0.0.1:{port}/api/hello", timeout=1).json().get("app") == "mbs-studio"
    except (requests.RequestException, ValueError):
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _idle_watch(server, state) -> None:
    while not server.should_exit:
        time.sleep(15)
        if time.time() - state.last_ping > IDLE_EXIT_SECONDS and not state.runner.busy:
            server.should_exit = True


def main() -> None:
    parser = argparse.ArgumentParser(description="M.B.S Studio (browser UI)")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stay", action="store_true")
    args = parser.parse_args()

    os.chdir(ROOT)
    if sys.stdout is None or sys.stderr is None:  # pythonw: keep a log instead of losing output
        (ROOT / "logs").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(ROOT / "logs" / "web.log", "a", encoding="utf-8", buffering=1)

    if not args.port and _running_instance(DEFAULT_PORT):
        webbrowser.open(f"http://127.0.0.1:{DEFAULT_PORT}/")
        return
    port = args.port or next(p for p in range(DEFAULT_PORT, DEFAULT_PORT + 40) if _port_free(p))

    import uvicorn

    from .server import create_app

    app = create_app(ROOT)
    state = app.state.mbs
    state.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    url = f"http://127.0.0.1:{port}/"
    print(f"M.B.S Studio: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    if not args.stay:
        threading.Thread(target=_idle_watch, args=(server, state), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
