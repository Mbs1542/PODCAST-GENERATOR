"""Tools: downloads and transcriptions that are not (yet) podcasts.

Each item is a folder under <outputs>/media/<id> with item.json:
  download       a link (a video, a playlist or a whole course page) -> media files
  transcription  one media file (uploaded, or from a download) -> SRT + TXT

Downloads run two at a time (network bound); transcriptions run one at a time (one GPU)
and share one loaded model until the queue is empty. Every job keeps the computer awake.
A transcription can become a podcast project in one step.
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..media import DownloadCancelled, DownloadError, DownloadRequest, QUALITIES, download, parse_cookies
from ..transcribe import TRANSCRIBE_LANGUAGES, HebrewTranscriber, TranscribeError, model_for_language, release_models, to_srt, to_text
from ..transcribe.whisper import MEDIA_EXTENSIONS
from ..utils.keep_awake import keep_awake
from ..utils.logging import get_logger

MAX_LOG_LINES = 200
DOWNLOAD_WORKERS = 2
MAX_URLS = 50
MAX_COOKIE_BYTES = 2 * 1024 * 1024
_SAFE_RE = re.compile(r"[^\w֐-׿ .()\-]+")


@dataclass
class MediaItem:
    id: str
    kind: str                      # download | transcription
    title: str
    source: str                    # the URL, or the uploaded file's name
    created: str
    options: Dict[str, Any] = field(default_factory=dict)
    status: str = "queued"         # queued | running | done | error | cancelled
    stage: str = ""
    progress: Optional[float] = None
    error: str = ""
    files: List[Dict[str, Any]] = field(default_factory=list)  # {"name", "kind", "size"}
    parent: str = ""               # the download a transcription came from
    project: str = ""              # the podcast project made from it
    log: List[str] = field(default_factory=list)


def _kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".srt", ".vtt"}:
        return "subtitles"
    if suffix == ".txt":
        return "text"
    if suffix in {".mp3", ".m4a", ".wav", ".opus", ".ogg", ".aac", ".flac"}:
        return "audio"
    return "video"


class MediaStore:
    def __init__(self, outputs_dir: Path) -> None:
        self.root = Path(outputs_dir) / "media"
        self.cookies_dir = Path(outputs_dir) / "private" / "cookies"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def folder(self, item_id: str) -> Path:
        return self.root / item_id

    def create(self, kind: str, title: str, source: str, options: Dict[str, Any], parent: str = "") -> MediaItem:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        item = MediaItem(id=f"{stamp}-{uuid.uuid4().hex[:6]}", kind=kind, title=title[:120] or source[:120],
                         source=source[:2000], created=datetime.now().isoformat(timespec="seconds"),
                         options=options, parent=parent)
        self.folder(item.id).mkdir(parents=True)
        self.save(item)
        return item

    def get(self, item_id: str) -> Optional[MediaItem]:
        if not re.fullmatch(r"[\w\-]+", item_id or ""):
            return None
        path = self.folder(item_id) / "item.json"
        with self._lock:
            try:
                return MediaItem(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                return None

    def list(self) -> List[MediaItem]:
        items = [self.get(p.name) for p in self.root.iterdir() if (p / "item.json").exists()]
        return sorted((i for i in items if i), key=lambda i: i.created, reverse=True)

    def save(self, item: MediaItem) -> None:
        with self._lock:
            item.log = item.log[-MAX_LOG_LINES:]
            path = self.folder(item.id) / "item.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(asdict(item), ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)

    def update(self, item_id: str, **changes: Any) -> MediaItem:
        with self._lock:
            item = self.get(item_id)
            if item is None:
                raise KeyError(item_id)
            for key, value in changes.items():
                setattr(item, key, value)
            self.save(item)
            return item

    def append_log(self, item_id: str, line: str) -> None:
        with self._lock:
            item = self.get(item_id)
            if item:
                item.log.append(f"{datetime.now():%H:%M:%S}  {line}")
                self.save(item)

    def file_path(self, item: MediaItem, index: int) -> Optional[Path]:
        if not 0 <= index < len(item.files):
            return None
        path = self.folder(item.id) / item.files[index]["name"]
        return path if path.exists() else None

    def register_files(self, item_id: str, paths: List[Path]) -> MediaItem:
        folder = self.folder(item_id)
        entries = []
        for path in paths:
            relative = path.resolve().relative_to(folder.resolve()).as_posix()
            entries.append({"name": relative, "kind": _kind_of(path), "size": path.stat().st_size})
        return self.update(item_id, files=entries)

    def delete(self, item_id: str, force: bool = False) -> bool:
        item = self.get(item_id)
        if item is None or (item.status in ("queued", "running") and not force):
            return False
        shutil.rmtree(self.folder(item_id), ignore_errors=True)
        return True

    # ------------------------------------------------------------------ cookies (login sessions for course sites)

    def cookies(self) -> List[Dict[str, Any]]:
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for path in sorted(self.cookies_dir.glob("*.txt")):
            try:
                info = parse_cookies(path.read_text(encoding="utf-8", errors="replace"))
            except DownloadError:
                continue
            result.append({
                "id": path.stem, "domains": info.domains, "count": info.count,
                "updated": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="minutes"),
                "login_expires": datetime.fromtimestamp(info.login_expires).isoformat(timespec="minutes") if info.login_expires else None,
                "expired": bool(info.login_expires and info.login_expires < time.time()),
            })
        return result

    def save_cookies(self, name: str, text: str) -> Dict[str, Any]:
        if len(text.encode("utf-8")) > MAX_COOKIE_BYTES:
            raise DownloadError("קובץ העוגיות גדול מדי.")
        info = parse_cookies(text)
        cookie_id = _SAFE_RE.sub("_", name.strip())[:40].strip("_ .") or info.domains[0].replace(".", "_")
        cookie_id = re.sub(r"[^\w\-]+", "_", cookie_id, flags=re.ASCII).strip("_") or "cookies"
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        (self.cookies_dir / f"{cookie_id}.txt").write_text(text, encoding="utf-8")
        return next(c for c in self.cookies() if c["id"] == cookie_id)

    def cookie_path(self, cookie_id: str) -> Optional[Path]:
        if not cookie_id or not re.fullmatch(r"[\w\-]+", cookie_id):
            return None
        path = self.cookies_dir / f"{cookie_id}.txt"
        return path if path.exists() else None

    def delete_cookies(self, cookie_id: str) -> None:
        path = self.cookie_path(cookie_id)
        if path is None:
            raise DownloadError("קובץ העוגיות לא נמצא")
        path.unlink()


class MediaRunner:
    def __init__(
        self,
        store: MediaStore,
        downloader: Optional[Callable[..., List[Path]]] = None,
        transcriber_factory: Optional[Callable[[str, Callable[[str], None]], Any]] = None,
    ) -> None:
        self.store = store
        self._download = downloader or download
        self._transcriber_factory = transcriber_factory or (
            lambda language, log: HebrewTranscriber(model=model_for_language(language), language=language, log=log)
        )
        self._downloads = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS, thread_name_prefix="mbs-download")
        self._transcriptions: "queue.Queue[str]" = queue.Queue()
        self._cancel: Dict[str, threading.Event] = {}
        self._active = 0
        self._lock = threading.Lock()
        self.logger = get_logger(self.__class__.__name__)
        threading.Thread(target=self._transcription_worker, name="mbs-transcribe", daemon=True).start()

    @property
    def busy(self) -> bool:
        return self._active > 0 or not self._transcriptions.empty()

    def recover_interrupted(self) -> None:
        for item in self.store.list():
            if item.status == "uploading":
                self.store.delete(item.id, force=True)
            elif item.status in ("queued", "running"):
                self.store.update(item.id, status="error", progress=None, stage="",
                                  error="העבודה הופסקה כשהאפליקציה נסגרה. אפשר להפעיל אותה שוב.")

    # ------------------------------------------------------------------ submit

    def submit_download(self, url: str, options: Dict[str, Any]) -> MediaItem:
        item = self.store.create("download", options.pop("title", "") or url, url, options)
        self._cancel[item.id] = threading.Event()
        self._downloads.submit(self._guard, item.id, self._run_download)
        return item

    def submit_transcription(self, media: Path, title: str, language: str, parent: str = "") -> MediaItem:
        item = self.store.create("transcription", title, media.name, {"language": language, "media": str(media)}, parent)
        self._transcriptions.put(item.id)
        return item

    def prepare_upload(self, filename: str, language: str) -> tuple:
        """A transcription item whose source file is streamed into its own folder; returns (item, target path)."""
        name = _SAFE_RE.sub("_", Path(filename).name).strip() or "recording"
        item = self.store.create("transcription", Path(name).stem, name, {"language": language}, "")
        target = self.store.folder(item.id) / "source" / name
        target.parent.mkdir(parents=True)
        self.store.update(item.id, status="uploading", stage="מעלה")
        return item, target

    def enqueue_upload(self, item_id: str, media: Path) -> MediaItem:
        item = self.store.get(item_id)
        options = {**item.options, "media": str(media)}
        item = self.store.update(item_id, options=options, status="queued", stage="ממתין בתור")
        self._transcriptions.put(item_id)
        return item

    def retry(self, item_id: str) -> MediaItem:
        item = self.store.get(item_id)
        if item is None or item.status in ("queued", "running"):
            raise KeyError(item_id)
        item = self.store.update(item_id, status="queued", error="", progress=None, stage="ממתין בתור")
        if item.kind == "download":
            self._cancel[item.id] = threading.Event()
            self._downloads.submit(self._guard, item.id, self._run_download)
        else:
            self._transcriptions.put(item.id)
        return item

    def cancel(self, item_id: str) -> None:
        event = self._cancel.get(item_id)
        if event:
            event.set()

    # ------------------------------------------------------------------ workers

    def _guard(self, item_id: str, job: Callable[[MediaItem], None]) -> None:
        with self._lock:
            self._active += 1
        try:
            item = self.store.get(item_id)
            if item is None:
                return
            self.store.update(item_id, status="running", stage="מתחיל", progress=0.0)
            with keep_awake():
                job(item)
            if self.store.get(item_id).status == "running":
                self.store.update(item_id, status="done", stage="", progress=None)
        except (DownloadCancelled,) as exc:
            self.store.update(item_id, status="cancelled", stage="", progress=None, error=str(exc))
        except (DownloadError, TranscribeError) as exc:
            self.store.append_log(item_id, f"שגיאה: {exc}")
            self.store.update(item_id, status="error", stage="", progress=None, error=str(exc))
        except Exception as exc:  # never let one item take the worker down
            self.logger.exception("Media job failed for %s", item_id)
            self.store.update(item_id, status="error", stage="", progress=None, error=f"{exc.__class__.__name__}: {exc}")
        finally:
            with self._lock:
                self._active -= 1

    def _transcription_worker(self) -> None:
        while True:
            item_id = self._transcriptions.get()
            self._guard(item_id, self._run_transcription)
            if self._transcriptions.empty():
                release_models()  # free the GPU until the next batch

    def _run_download(self, item: MediaItem) -> None:
        options = item.options
        cookies = self.store.cookie_path(options.get("cookies", ""))
        request = DownloadRequest(
            url=item.source,
            folder=self.store.folder(item.id) / "files",
            quality=options.get("quality", "best"),
            playlist=bool(options.get("playlist")),
            subtitles=bool(options.get("subtitles", True)),
            cookies=cookies,
            referer=str(options.get("referer", "")),
        )
        self.store.update(item.id, stage="מתחבר לאתר")
        files = self._download(
            request,
            progress=lambda fraction, label: self.store.update(item.id, progress=fraction, stage=label),
            log=lambda line: self.store.append_log(item.id, line),
            cancel=self._cancel.get(item.id),
        )
        self.store.register_files(item.id, files)
        if item.title == item.source and files:  # the user gave no name: use the video's own title
            name = files[0].stem
            self.store.update(item.id, title=name if len(files) == 1 else f"{len(files)} קבצים: {name}…")
        self.store.append_log(item.id, f"הורדו {len(files)} קבצים")
        if options.get("transcribe"):
            language = options.get("language", "he")
            for media in files:
                self.submit_transcription(media, media.stem, language, parent=item.id)
            self.store.append_log(item.id, f"{len(files)} קבצים נשלחו לתמלול")

    def _run_transcription(self, item: MediaItem) -> None:
        media = Path(item.options["media"])
        if not media.exists():
            raise TranscribeError("קובץ המקור לא נמצא (אולי ההורדה נמחקה).")
        language = item.options.get("language", "he")
        self.store.update(item.id, stage="טוען את מודל התמלול")
        last = [0.0]

        def progress(position: float, duration: float) -> None:
            if duration and time.time() - last[0] > 1.5:
                last[0] = time.time()
                self.store.update(item.id, progress=min(position / duration, 0.999),
                                  stage=f"מתמלל: {position / 60:.0f} מתוך {duration / 60:.0f} דקות")

        transcriber = self._transcriber_factory(language, lambda line: self.store.append_log(item.id, line))
        segments = transcriber.transcribe(media, progress)
        if not segments:
            raise TranscribeError("לא זוהה דיבור בקובץ.")
        folder = self.store.folder(item.id)
        stem = _SAFE_RE.sub("_", Path(item.title).stem)[:80].strip() or "transcript"
        srt, txt = folder / f"{stem}.srt", folder / f"{stem}.txt"
        srt.write_text(to_srt(segments), encoding="utf-8")
        txt.write_text(to_text(segments), encoding="utf-8")
        self.store.register_files(item.id, [srt, txt])
        minutes = segments[-1].end / 60
        self.store.append_log(item.id, f"התמלול הושלם: {len(segments)} קטעים, {minutes:.0f} דקות")


def validate_download_options(body: Dict[str, Any]) -> Dict[str, Any]:
    quality = str(body.get("quality") or "best")
    language = str(body.get("language") or "he")
    return {
        "quality": quality if quality in QUALITIES else "best",
        "playlist": bool(body.get("playlist")),
        "subtitles": bool(body.get("subtitles", True)),
        "transcribe": bool(body.get("transcribe")),
        "language": language if language in TRANSCRIBE_LANGUAGES else "he",
        "cookies": re.sub(r"[^\w\-]", "", str(body.get("cookies") or ""))[:40],
        "referer": str(body.get("referer") or "").strip()[:500] if str(body.get("referer") or "").startswith("http") else "",
    }


def parse_urls(text: str) -> List[str]:
    urls = [u.strip() for u in re.split(r"[\s,]+", text or "") if u.strip()]
    valid = [u for u in urls if re.match(r"^https?://[^\s/$.?#].[^\s]*$", u)]
    seen: List[str] = []
    for url in valid:
        url = url.replace("http://", "https://", 1)
        if url not in seen:
            seen.append(url)
    return seen[:MAX_URLS]


def is_transcribable(name: str) -> bool:
    return Path(name).suffix.lower() in MEDIA_EXTENSIONS
