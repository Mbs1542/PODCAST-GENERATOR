"""Start M.B.S Studio in the browser.

    python -m src.web                 # start (or reuse a running copy) and open the browser
    python -m src.web --no-browser    # start without opening a tab
    python -m src.web --stay          # don't exit when the browser tab is closed

In a container or on a server (see docker-compose.yml):
    MBS_HOST=0.0.0.0 MBS_PORT=8765 python -m src.web --no-browser --stay
    MBS_ALLOWED_HOSTS=studio.lan,10.8.0.2   other names/addresses the app is opened by (phone over VPN)
    MBS_ACCESS_KEY=...                      required whenever MBS_ALLOWED_HOSTS names another device
    MBS_EXTERNAL_AUTH=cloudflare-access     instead of the key: a reverse proxy in front of the app
                                            verifies a Cloudflare Access login (Traefik's JWT check);
                                            the app then refuses any request without that header

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


def _find_running_instance() -> int:
    """The port of a copy that is already running (it may have moved off the default port), or 0."""
    for port in range(DEFAULT_PORT, DEFAULT_PORT + 10):
        if not _port_free(port) and _running_instance(port):
            return port
    return 0


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name != "nt":
            # Right after a restart the old socket lingers in TIME_WAIT; uvicorn binds with SO_REUSEADDR,
            # so the check must too or the app drifts to 8766 (on Windows the flag would allow stealing a port)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _idle_watch(server, state) -> None:
    while not server.should_exit:
        time.sleep(15)
        if (time.time() - state.last_ping > IDLE_EXIT_SECONDS and not state.runner.busy and not state.media_runner.busy
                and not state.exam_runner.busy):
            server.should_exit = True


MIN_ACCESS_KEY_LENGTH = 12
LOCAL_NAMES = {"127.0.0.1", "localhost", "[::1]"}


EXTERNAL_AUTH_MODES = {"cloudflare-access"}


def network_config(port: int, extra_hosts: str, access_key: str, external_auth: str = "") -> tuple:
    """The Host headers to accept and whether the setup is safe to start.

    Extra host names (a LAN name, a VPN address, a domain) make the app reachable from
    other devices, so they require an access key, or a login that a proxy in front of the app
    checks (external_auth). Returns (allowed_hosts, error or "").
    """
    if external_auth and external_auth not in EXTERNAL_AUTH_MODES:
        return set(), f"MBS_EXTERNAL_AUTH must be one of {', '.join(sorted(EXTERNAL_AUTH_MODES))}."
    allowed = {f"{name}:{port}" for name in LOCAL_NAMES}
    remote = []
    for entry in (h.strip() for h in extra_hosts.split(",")):
        if not entry:
            continue
        name = entry.rsplit(":", 1)[0] if entry.count(":") == 1 else entry
        allowed |= {entry, f"{name}:{port}", name}
        if name not in LOCAL_NAMES:
            remote.append(entry)
    if remote and len(access_key) < MIN_ACCESS_KEY_LENGTH and not external_auth:
        return allowed, (f"MBS_ALLOWED_HOSTS ({', '.join(remote)}) makes the app reachable from other devices; "
                         f"set MBS_ACCESS_KEY to a password of at least {MIN_ACCESS_KEY_LENGTH} characters.")
    return allowed, ""


def main() -> None:
    parser = argparse.ArgumentParser(description="M.B.S Studio (browser UI)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MBS_PORT") or 0))
    parser.add_argument("--host", default=os.environ.get("MBS_HOST") or "127.0.0.1",
                        help="Address to listen on; 0.0.0.0 inside a container.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stay", action="store_true")
    args = parser.parse_args()

    os.chdir(ROOT)
    if sys.stdout is None or sys.stderr is None:  # pythonw: keep a log instead of losing output
        (ROOT / "logs").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(ROOT / "logs" / "web.log", "a", encoding="utf-8", buffering=1)

    local = args.host in ("127.0.0.1", "localhost")
    running = _find_running_instance() if local and not args.port else 0
    if running:
        webbrowser.open(f"http://127.0.0.1:{running}/")
        return
    port = args.port or next(p for p in range(DEFAULT_PORT, DEFAULT_PORT + 40) if _port_free(p))

    access_key = os.environ.get("MBS_ACCESS_KEY", "")
    external_auth = os.environ.get("MBS_EXTERNAL_AUTH", "").strip()
    allowed, error = network_config(port, os.environ.get("MBS_ALLOWED_HOSTS", ""), access_key, external_auth)
    if error:
        print(f"M.B.S Studio will not start: {error}", file=sys.stderr, flush=True)
        sys.exit(2)

    import uvicorn

    from .server import create_app

    app = create_app(ROOT)
    state = app.state.mbs
    state.allowed_hosts = allowed
    state.access_key = access_key
    state.external_auth = external_auth
    if not local and not access_key and not external_auth:
        print("Note: listening on all interfaces without MBS_ACCESS_KEY. Publish the port on 127.0.0.1 only "
              "(the docker-compose default), or set MBS_ACCESS_KEY.", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port, log_level="warning",
                                           proxy_headers=True, forwarded_allow_ips="*" if not local else "127.0.0.1"))

    url = f"http://127.0.0.1:{port}/"
    print(f"M.B.S Studio: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    if not args.stay:
        threading.Thread(target=_idle_watch, args=(server, state), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
