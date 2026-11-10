"""Where a finished episode ends up: one folder per podcast, and optionally Google Drive.

Output folder  <root>/<podcast title>/
  the lecture video(s) it was made from   hard links when on the same disk (no extra space), else copies
  <lesson> - תמלול.srt / .txt              one pair per lesson
  <title> - פודקאסט.mp3                    and <title> - וידאו.mp4 once a video is made
  <title> - סיכום.html / .docx             the lesson summary the episode was written from

<root> is the folder the lessons were downloaded to, else the folder chosen in the settings,
else ~/Documents/M.B.S Studio. Building again updates the same folder (a marker file names its project).

Google Drive: sign in once (OAuth for installed apps with PKCE; scope drive.file, so the app can see
only the files it created), then each output folder is mirrored to "<Drive folder>/<podcast title>".
Files already in Drive with the same name and size are skipped, so delivering again after the video
is made uploads only the video. Credentials live in <outputs>/private/google, never in the page.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests

SCOPES = "https://www.googleapis.com/auth/drive.file openid email"
DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK_BYTES = 16 * 1024 * 1024  # a multiple of 256 KiB, as Drive's resumable upload requires
MAX_CLIENT_BYTES = 64 * 1024
PENDING_TTL = 15 * 60
MARKER = ".mbs-output.json"
MEDIA_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"}
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

Log = Callable[[str], None]


class DeliveryError(RuntimeError):
    """A message written for the user."""


def safe_name(text: str, fallback: str = "podcast") -> str:
    name = _UNSAFE_NAME.sub("_", str(text or "")).strip(" ._")
    return name[:100] or fallback


# --------------------------------------------------------------------------- preferences

class DeliveryPrefs:
    DEFAULTS = {"output_root": "", "drive_auto": False, "drive_folder": "M.B.S Studio"}

    def __init__(self, private_dir: Path) -> None:
        self.path = Path(private_dir) / "delivery.json"
        self._lock = threading.Lock()

    def get(self) -> Dict[str, Any]:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        return {**self.DEFAULTS, **{k: v for k, v in saved.items() if k in self.DEFAULTS}}

    def update(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            prefs = self.get()
            if "output_root" in changes:
                prefs["output_root"] = str(changes["output_root"] or "").strip()
            if "drive_auto" in changes:
                prefs["drive_auto"] = bool(changes["drive_auto"])
            if "drive_folder" in changes:
                prefs["drive_folder"] = safe_name(changes["drive_folder"], "M.B.S Studio")[:60]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
            return prefs

    def default_root(self) -> Path:
        return Path.home() / "Documents" / "M.B.S Studio"


# --------------------------------------------------------------------------- output folder

def place(source: Path, target: Path) -> str:
    """Put a file in the output folder: a hard link when possible, else a copy. Returns how."""
    if target.exists():
        if target.stat().st_size == source.stat().st_size:
            return "kept"
        target.unlink()
    try:
        os.link(source, target)
        return "linked"
    except OSError:
        shutil.copy2(source, target)
        return "copied"


def output_folder_for(root: Path, title: str, project_id: str) -> Path:
    """<root>/<title>, or <title> (2)... when that name already belongs to another podcast."""
    base = safe_name(title)
    for number in range(1, 100):
        folder = root / (base if number == 1 else f"{base} ({number})")
        marker = folder / MARKER
        if not folder.exists():
            return folder
        try:
            if json.loads(marker.read_text(encoding="utf-8")).get("project") == project_id:
                return folder
        except (OSError, ValueError):
            if not any(folder.iterdir()):
                return folder  # an empty folder with that name: use it
    raise DeliveryError("יותר מדי תיקיות בשם הזה")


def build_output_folder(
    folder: Path,
    project_id: str,
    title: str,
    *,
    lectures: List[Path],
    transcripts: List[Tuple[Path, str]],
    audio: Optional[Path],
    video: Optional[Path],
    summary_html: str,
    summary_docx: Optional[Path],
    log: Log,
) -> List[Path]:
    """Fill (or update) one podcast's folder. Returns the files it holds."""
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_name(title)
    files: List[Path] = []
    how: Dict[str, int] = {}

    def put(source: Optional[Path], target_name: str) -> None:
        if source is None or not Path(source).exists():
            return
        target = folder / target_name
        result = place(Path(source), target)
        how[result] = how.get(result, 0) + 1
        files.append(target)

    for lecture in lectures:
        put(lecture, Path(lecture).name)
    for transcript, target_name in transcripts:
        put(transcript, target_name)
    if audio is not None:
        put(audio, f"{name} - פודקאסט{Path(audio).suffix}")
    if video is not None:
        put(video, f"{name} - וידאו{Path(video).suffix}")
    page = folder / f"{name} - סיכום.html"
    page.write_text(summary_html, encoding="utf-8")
    files.append(page)
    if summary_docx is not None and summary_docx.exists():
        target = folder / f"{name} - סיכום.docx"
        shutil.copy2(summary_docx, target)
        files.append(target)
    (folder / MARKER).write_text(json.dumps({"project": project_id, "title": title, "updated": time.time()},
                                            ensure_ascii=False), encoding="utf-8")
    if how.get("copied"):
        log(f"{how['copied']} קבצים הועתקו (כונן אחר); קבצים גדולים תופסים מקום פעמיים")
    return files


# --------------------------------------------------------------------------- Google Drive

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwt_email(id_token: str) -> str:
    """The signed-in address, for display only (the token came straight from Google over TLS)."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email", "")
    except (IndexError, ValueError):
        return ""


class GoogleDrive:
    def __init__(self, private_dir: Path, http: Any = None) -> None:
        self.dir = Path(private_dir) / "google"
        self.http = http or requests
        self._pending: Dict[str, Tuple[str, str, float]] = {}  # state -> (verifier, redirect_uri, created)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- credentials on disk

    @property
    def client_path(self) -> Path:
        return self.dir / "client_secret.json"

    @property
    def token_path(self) -> Path:
        return self.dir / "token.json"

    def _write_private(self, path: Path, data: Dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(path)

    def client(self) -> Optional[Dict[str, str]]:
        try:
            raw = json.loads(self.client_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        info = raw.get("installed") or raw.get("web") or {}
        return info if info.get("client_id") and info.get("client_secret") else None

    def save_client(self, raw: bytes) -> Dict[str, Any]:
        if len(raw) > MAX_CLIENT_BYTES:
            raise DeliveryError("הקובץ גדול מדי בשביל קובץ client_secret")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise DeliveryError("זה לא קובץ JSON. הורד את הקובץ מ-Google Cloud Console (OAuth client, Desktop app).") from exc
        info = data.get("installed") or data.get("web") or {}
        if not (info.get("client_id", "").endswith(".apps.googleusercontent.com") and info.get("client_secret")):
            raise DeliveryError("בקובץ חסרים client_id או client_secret של Google")
        if "installed" not in data:
            raise DeliveryError("צריך OAuth client מסוג Desktop app (בקובץ יש 'web'). צור אחד חדש ב-Google Cloud Console.")
        self._write_private(self.client_path, data)
        return self.status()

    def _token(self) -> Dict[str, Any]:
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def status(self) -> Dict[str, Any]:
        client = self.client()
        token = self._token()
        return {
            "client": bool(client),
            "project": (client or {}).get("project_id", ""),
            "connected": bool(token.get("refresh_token")),
            "email": token.get("email", ""),
        }

    # ---------------------------------------------------------------- sign-in (OAuth 2.0 for installed apps)

    def auth_url(self, redirect_uri: str) -> str:
        client = self.client()
        if not client:
            raise DeliveryError("קודם צריך להעלות את קובץ ה-client_secret של Google")
        verifier = _b64url(secrets.token_bytes(48))
        state = _b64url(secrets.token_bytes(24))
        with self._lock:
            now = time.time()
            self._pending = {s: p for s, p in self._pending.items() if now - p[2] < PENDING_TTL}
            self._pending[state] = (verifier, redirect_uri, now)
        params = {
            "client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
            "scope": SCOPES, "state": state, "access_type": "offline", "prompt": "consent",
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
        }
        return f"{client.get('auth_uri', 'https://accounts.google.com/o/oauth2/auth')}?{urlencode(params)}"

    def is_pending(self, state: str) -> bool:
        with self._lock:
            return state in self._pending

    def cancel(self, state: str) -> None:
        with self._lock:
            self._pending.pop(state, None)

    def finish(self, code: str, state: str) -> Dict[str, Any]:
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or time.time() - pending[2] > PENDING_TTL:
            raise DeliveryError("ההתחברות פגה או לא התחילה כאן. נסה שוב מההגדרות.")
        verifier, redirect_uri, _ = pending
        client = self.client() or {}
        response = self.http.post(client.get("token_uri", "https://oauth2.googleapis.com/token"), data={
            "code": code, "client_id": client.get("client_id"), "client_secret": client.get("client_secret"),
            "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
        }, timeout=30)
        data = self._json(response, "ההתחברות ל-Google נכשלה")
        if not data.get("refresh_token"):
            raise DeliveryError("Google לא החזיר הרשאה קבועה. נתק את האפליקציה בחשבון Google ונסה שוב.")
        self._write_private(self.token_path, {
            "refresh_token": data["refresh_token"], "access_token": data.get("access_token", ""),
            "expires_at": time.time() + float(data.get("expires_in", 0)) - 60,
            "email": _jwt_email(data.get("id_token", "")),
        })
        return self.status()

    def disconnect(self) -> Dict[str, Any]:
        token = self._token()
        if token.get("refresh_token"):
            try:  # revoke at Google too, so the access is really gone
                self.http.post(REVOKE_URL, data={"token": token["refresh_token"]}, timeout=15)
            except requests.RequestException:
                pass
        self.token_path.unlink(missing_ok=True)
        return self.status()

    def _access_token(self) -> str:
        token = self._token()
        if not token.get("refresh_token"):
            raise DeliveryError("Google Drive לא מחובר. התחבר בהגדרות.")
        if token.get("access_token") and time.time() < float(token.get("expires_at", 0)):
            return token["access_token"]
        client = self.client() or {}
        response = self.http.post(client.get("token_uri", "https://oauth2.googleapis.com/token"), data={
            "client_id": client.get("client_id"), "client_secret": client.get("client_secret"),
            "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
        }, timeout=30)
        if response.status_code in (400, 401):
            raise DeliveryError("ההרשאה ל-Google Drive בוטלה או פגה. התחבר מחדש בהגדרות.")
        data = self._json(response, "חידוש ההרשאה ל-Google נכשל")
        token.update(access_token=data["access_token"], expires_at=time.time() + float(data.get("expires_in", 3600)) - 60)
        self._write_private(self.token_path, token)
        return token["access_token"]

    @staticmethod
    def _json(response: Any, message: str) -> Dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            error = data.get("error")
            if isinstance(error, dict):  # Drive API errors
                detail = error.get("message") or response.status_code
            else:  # OAuth errors
                detail = data.get("error_description") or error or response.status_code
            raise DeliveryError(f"{message} ({detail})")
        return data

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    # ---------------------------------------------------------------- Drive files

    def ensure_folder(self, name: str, parent: Optional[str] = None) -> str:
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        query = f"name = '{escaped}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        query += f" and '{parent}' in parents" if parent else " and 'root' in parents"
        found = self._json(self.http.get(f"{DRIVE_API}/files", headers=self._headers(), timeout=30,
                                         params={"q": query, "fields": "files(id)", "spaces": "drive"}), "חיפוש תיקייה ב-Drive נכשל")
        if found.get("files"):
            return found["files"][0]["id"]
        created = self._json(self.http.post(f"{DRIVE_API}/files", headers=self._headers(), timeout=30, params={"fields": "id"},
                                            json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent or "root"]}),
                             "יצירת תיקייה ב-Drive נכשלה")
        return created["id"]

    def children(self, folder_id: str) -> Dict[str, Tuple[str, int]]:
        result: Dict[str, Tuple[str, int]] = {}
        page_token = None
        while True:
            params = {"q": f"'{folder_id}' in parents and trashed = false", "fields": "nextPageToken,files(id,name,size)",
                      "pageSize": 200}
            if page_token:
                params["pageToken"] = page_token
            data = self._json(self.http.get(f"{DRIVE_API}/files", headers=self._headers(), params=params, timeout=30),
                              "קריאת התיקייה ב-Drive נכשלה")
            for item in data.get("files", []):
                result[item["name"]] = (item["id"], int(item.get("size") or 0))
            page_token = data.get("nextPageToken")
            if not page_token:
                return result

    def upload(self, path: Path, folder_id: str, progress: Optional[Callable[[int, int], None]] = None,
               replace_id: Optional[str] = None) -> str:
        """Resumable upload in chunks, so a lecture video of a few GB goes up safely."""
        size = path.stat().st_size
        if replace_id:  # same name, different content: upload a new version of that file
            start = self.http.patch(f"{UPLOAD_API}/{replace_id}", params={"uploadType": "resumable"},
                                    headers={**self._headers(), "X-Upload-Content-Length": str(size)}, json={}, timeout=30)
        else:
            start = self.http.post(UPLOAD_API, params={"uploadType": "resumable"}, timeout=30,
                                   headers={**self._headers(), "X-Upload-Content-Length": str(size)},
                                   json={"name": path.name, "parents": [folder_id]})
        if start.status_code >= 400:
            self._json(start, f"ההעלאה של {path.name} נכשלה")
        session = start.headers.get("Location")
        if not session:
            raise DeliveryError(f"Drive לא פתח העלאה עבור {path.name}")
        sent = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK_BYTES)
                end = sent + len(chunk) - 1
                headers = {"Content-Range": f"bytes {sent}-{end}/{size}" if chunk else f"bytes */{size}"}
                for attempt in range(4):
                    try:
                        response = self.http.put(session, data=chunk, headers=headers, timeout=300)
                    except requests.RequestException:
                        if attempt == 3:
                            raise DeliveryError(f"החיבור נפל בזמן העלאת {path.name}")
                        time.sleep(2 ** attempt)
                        continue
                    if response.status_code in (500, 502, 503, 504) and attempt < 3:
                        time.sleep(2 ** attempt)
                        continue
                    break
                sent += len(chunk)
                if progress:
                    progress(min(sent, size), size)
                if response.status_code in (200, 201):
                    return response.json().get("id", "")
                if response.status_code != 308:
                    self._json(response, f"ההעלאה של {path.name} נכשלה")
                if not chunk:
                    raise DeliveryError(f"ההעלאה של {path.name} לא הסתיימה")

    def upload_folder(self, folder: Path, drive_root: str, log: Log) -> str:
        """Mirror one output folder into <drive_root>/<folder name>; returns the Drive folder's link."""
        root_id = self.ensure_folder(drive_root)
        folder_id = self.ensure_folder(folder.name, root_id)
        existing = self.children(folder_id)
        files = sorted((p for p in folder.iterdir() if p.is_file() and p.name != MARKER), key=lambda p: p.stat().st_size)
        uploaded = 0
        for path in files:
            size = path.stat().st_size
            current = existing.get(path.name)
            if current and current[1] == size:
                continue
            last = [0.0]

            def progress(done: int, total: int, name: str = path.name) -> None:
                if total > CHUNK_BYTES and time.time() - last[0] > 5:
                    last[0] = time.time()
                    log(f"Drive: {name} {done * 100 // total}%")

            self.upload(path, folder_id, progress, replace_id=current[0] if current else None)
            uploaded += 1
            log(f"Drive: הועלה {path.name}")
        log(f"Google Drive: {uploaded} קבצים הועלו" if uploaded else "Google Drive: הכול כבר מעודכן")
        return f"https://drive.google.com/drive/folders/{folder_id}"


# --------------------------------------------------------------------------- the service the app uses

class Delivery:
    """Settings, Drive and background uploads, shared by the jobs and the HTTP routes."""

    def __init__(self, private_dir: Path, http: Any = None) -> None:
        self.prefs = DeliveryPrefs(private_dir)
        self.drive = GoogleDrive(private_dir, http)
        self._uploading: set = set()
        self._lock = threading.Lock()

    def root_for(self, options: Dict[str, Any]) -> Path:
        """Where a podcast's folder goes: next to its lessons, else the chosen folder, else Documents."""
        export_dir = options.get("export_dir")
        if export_dir and Path(export_dir).is_dir():
            return Path(export_dir)
        chosen = self.prefs.get()["output_root"]
        return Path(chosen) if chosen else self.prefs.default_root()

    def should_upload(self) -> bool:
        return bool(self.prefs.get()["drive_auto"]) and self.drive.status()["connected"]

    def is_uploading(self, key: str) -> bool:
        with self._lock:
            return key in self._uploading

    def upload_async(self, key: str, folder: Path, log: Log, done: Callable[[str], None]) -> bool:
        """Mirror a folder to Drive in the background; one upload per podcast at a time."""
        with self._lock:
            if key in self._uploading:
                return False
            self._uploading.add(key)

        def run() -> None:
            try:
                log(f"מעלה ל-Google Drive: {folder.name}")
                done(self.drive.upload_folder(folder, self.prefs.get()["drive_folder"], log))
            except DeliveryError as exc:
                log(f"ההעלאה ל-Google Drive נכשלה: {exc}")
            except Exception as exc:  # a network problem must not take the app down
                log(f"ההעלאה ל-Google Drive נכשלה ({exc.__class__.__name__}: {exc})")
            finally:
                with self._lock:
                    self._uploading.discard(key)

        threading.Thread(target=run, name=f"mbs-drive-{key[:12]}", daemon=True).start()
        return True
