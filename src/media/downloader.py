"""Download videos and audio from the web with yt-dlp (the Python package, so it updates with pip).

Grew out of the TOOLS repo's video_downloader.bat and sylla_dashboard.py, kept their good
parts (retries, parallel fragments, Hebrew/English subtitles, per-site referer and
cookies for course sites, playlist numbering) and dropped the risky ones: TLS checks stay
on (no --no-check-certificate) and no yt-dlp.exe/ffmpeg.exe binaries are carried around.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse

QUALITIES: Dict[str, Dict[str, str]] = {
    "best": {"label": "הכי טובה", "format": "bestvideo*+bestaudio/best"},
    "1080": {"label": "1080p", "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]"},
    "720": {"label": "720p (קובץ קטן יותר)", "format": "bestvideo[height<=720]+bestaudio/best[height<=720]"},
    "audio": {"label": "שמע בלבד (MP3)", "format": "bestaudio/best"},
}
MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus", ".ogg", ".wav", ".aac", ".flac"}
SUBTITLE_LANGUAGES = ["he", "en"]


class DownloadError(RuntimeError):
    """A message written for the user."""


class DownloadCancelled(DownloadError):
    pass


@dataclass
class SiteProfile:
    name: str
    referer: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    impersonate: str = ""        # e.g. "chrome": needs yt-dlp's curl_cffi extra
    needs_cookies: bool = False
    single_video: bool = False   # the page lists one video; never expand it as a playlist


def site_profile(url: str) -> SiteProfile:
    """Per-site settings learned in the TOOLS downloader."""
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("sylla.academy"):
        # Course lessons embed Vimeo players that only play for a logged-in student from the course site
        return SiteProfile("sylla", referer="https://johnbryce.sylla.academy/", needs_cookies=True)
    if host.endswith("nachostv.com"):
        return SiteProfile("nachostv", referer=url, headers={"Origin": "https://nachostv.com"},
                           impersonate="chrome", single_video=True)
    if host in ("player.vimeo.com", "vimeo.com") or host.endswith(".vimeo.com"):
        return SiteProfile("vimeo")
    return SiteProfile("generic")


@dataclass
class DownloadRequest:
    url: str
    folder: Path
    quality: str = "best"
    playlist: bool = False         # download every item of a playlist / course page
    subtitles: bool = True         # Hebrew and English subtitles, embedded in the video
    cookies: Optional[Path] = None
    referer: str = ""              # overrides the site profile's referer


def _ffmpeg_dir() -> Optional[str]:
    ffmpeg = shutil.which("ffmpeg")
    return str(Path(ffmpeg).parent) if ffmpeg else None


def impersonation_available() -> bool:
    try:
        import curl_cffi  # noqa: F401

        return True
    except ImportError:
        return False


def build_options(request: DownloadRequest, profile: SiteProfile, log: Callable[[str], None]) -> Dict[str, Any]:
    """yt-dlp options for one request (no hooks; those are added by download())."""
    if request.quality not in QUALITIES:
        raise DownloadError(f"איכות לא מוכרת: {request.quality}")
    audio_only = request.quality == "audio"
    playlist = request.playlist and not profile.single_video
    # Short, Windows-safe names; course and playlist items keep their order in the name
    name = "%(title).90B.%(ext)s"
    template = f"%(playlist_index)03d - {name}" if playlist else name

    options: Dict[str, Any] = {
        "format": QUALITIES[request.quality]["format"],
        "outtmpl": str(request.folder / template),
        "paths": {"temp": str(request.folder / ".partial")},
        "windowsfilenames": True,
        "noplaylist": not playlist,
        "ignoreerrors": "only_download" if playlist else False,  # one broken lesson must not stop a course
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "retry_sleep_functions": {"http": lambda n: 5, "fragment": lambda n: 5},
        "socket_timeout": 30,
        "http_chunk_size": 10 * 1024 * 1024,
        "concurrent_fragment_downloads": 4,
        "buffersize": 64 * 1024,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "postprocessors": [],
    }
    ffmpeg_dir = _ffmpeg_dir()
    if ffmpeg_dir:
        options["ffmpeg_location"] = ffmpeg_dir

    if audio_only:
        options["postprocessors"].append({"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"})
    else:
        options["merge_output_format"] = "mp4"
        if request.subtitles:
            options.update({"writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": SUBTITLE_LANGUAGES})
            options["postprocessors"].append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
    options["postprocessors"].append({"key": "FFmpegMetadata", "add_metadata": True})

    headers = dict(profile.headers)
    referer = request.referer.strip() or profile.referer
    if referer:
        headers["Referer"] = referer
    if headers:
        options["http_headers"] = headers
    if request.cookies:
        options["cookiefile"] = str(request.cookies)
    elif profile.needs_cookies:
        log("This site usually needs your login cookies; without them the download will probably be refused.")
    if profile.impersonate:
        if impersonation_available():
            from yt_dlp.networking.impersonate import ImpersonateTarget

            options["impersonate"] = ImpersonateTarget.from_str(profile.impersonate)
        else:
            log("Browser impersonation is not installed (pip install curl_cffi); trying without it.")
    return options


class _Logger:
    def __init__(self, log: Callable[[str], None]) -> None:
        self.log = log

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.log(f"warning: {message}")

    def error(self, message: str) -> None:
        self.log(message)


def _is_embed_only(message: str) -> bool:
    return "embed-only" in message.lower() or "embedding url" in message.lower()


def _embed_referers(request: DownloadRequest, profile: SiteProfile) -> List[str]:
    """Pages that may embed an embed-only Vimeo link: the sites of the chosen cookies, most specific first.

    A bare player.vimeo.com link plays only when the request says it comes from the embedding site, and
    that is usually the same site the cookies were exported from (yedion.pac.ac.il works, pac.ac.il not).
    """
    if profile.name != "vimeo" or request.referer.strip() or not request.cookies:
        return []
    try:
        domains = parse_cookies(Path(request.cookies).read_text(encoding="utf-8", errors="replace")).domains
    except (OSError, DownloadError):
        return []
    hosts = {d.lstrip(".").lower() for d in domains if d and "vimeo" not in d.lower()}
    return [f"https://{host}/" for host in sorted(hosts, key=lambda h: (-h.count("."), h))]


def cookies_for(url: str, cookie_files: Sequence[Path]) -> List[Optional[Path]]:
    """Saved cookie files worth trying for a link, best first, ending with no cookies at all.

    The site's own cookies come first. A Vimeo link plays only inside the site that embeds it, which is the
    site the user exported cookies from, so for Vimeo every saved file is a candidate (each one also gives
    the embedding page to retry from, see _embed_referers).
    """
    host = (urlparse(url).hostname or "").lower()
    matching, others = [], []
    for path in cookie_files:
        try:
            domains = {d.lstrip(".").lower() for d in parse_cookies(Path(path).read_text(encoding="utf-8", errors="replace")).domains}
        except (OSError, DownloadError):
            continue
        (matching if any(host == d or host.endswith("." + d) for d in domains) else others).append(Path(path))
    vimeo = site_profile(url).name == "vimeo"
    return [*matching, *(others if vimeo else []), None]


def friendly_error(message: str) -> str:
    text = message.lower()
    if _is_embed_only(message):
        return ("הסרטון ב-Vimeo מוטמע באתר ומתנגן רק מתוכו. בחר את העוגיות של האתר (למשל של המכללה), "
                "או הדבק ב'מתקדם: כתובת מפנה (Referer)' את כתובת האתר שבו הסרטון מופיע.")
    if "http error 403" in text or "forbidden" in text:
        return "האתר סירב לגשת לסרטון (403). בדרך כלל העוגיות (cookies) פגו: ייצא אותן מחדש מהדפדפן."
    if "http error 401" in text or "login" in text and "required" in text or "logged-in" in text:
        return "הסרטון דורש התחברות. העלה קובץ עוגיות (cookies.txt) של האתר."
    if "http error 404" in text or "not found" in text and "video" in text:
        return "הסרטון לא נמצא בכתובת הזו (404)."
    if "http error 503" in text or "backend read error" in text:
        return "השרת של האתר עמוס (503) גם אחרי כמה ניסיונות. נסה שוב בעוד כמה דקות."
    if "unsupported url" in text:
        return "הכתובת לא נתמכת, או שאין בה סרטון שאפשר לזהות."
    if "private video" in text:
        return "הסרטון פרטי."
    if "ffmpeg" in text and "not found" in text:
        return "ffmpeg חסר. הרץ את 'תיקון התקנה'."
    return f"ההורדה נכשלה: {message.strip()[:300]}"


def download(
    request: DownloadRequest,
    progress: Optional[Callable[[float, str], None]] = None,
    log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    ydl_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> List[Path]:
    """Download one URL (or a whole playlist / course page). Returns the media files written."""
    log = log or (lambda _m: None)
    progress = progress or (lambda _f, _s: None)
    profile = site_profile(request.url)
    request.folder.mkdir(parents=True, exist_ok=True)
    options = build_options(request, profile, log)
    written: List[Path] = []
    last: List[Any] = [0.0, None]  # time of the last report, playlist index it was for

    def on_progress(status: Dict[str, Any]) -> None:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("ההורדה בוטלה")
        if status.get("status") != "downloading":
            return
        info = status.get("info_dict") or {}
        index, count = info.get("playlist_index"), info.get("n_entries") or info.get("playlist_count")
        # Throttled, except when a new playlist item starts (its counter must show at once)
        if time.time() - last[0] < 0.7 and index == last[1]:
            return
        last[0], last[1] = time.time(), index
        done = float(status.get("downloaded_bytes") or 0)
        total = float(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
        fraction = done / total if total else 0.0
        if index and count:
            fraction = (int(index) - 1 + fraction) / int(count)
        speed = status.get("speed")
        label = f"{done / 1048576:.0f}MB" + (f" מתוך {total / 1048576:.0f}MB" if total else "")
        if speed:
            label += f" · {speed / 1048576:.1f}MB/s"
        if index and count:
            label = f"פריט {index} מתוך {count} · " + label
        progress(min(fraction, 0.999), label)

    def on_finished(path: str) -> None:
        file = Path(path)
        if file.suffix.lower() in MEDIA_SUFFIXES and file not in written:
            written.append(file)
            log(f"Saved {file.name}")

    options["progress_hooks"] = [on_progress]
    options["post_hooks"] = [on_finished]
    options["logger"] = _Logger(log)

    if ydl_factory is None:
        from yt_dlp import YoutubeDL

        ydl_factory = YoutubeDL
    referers = _embed_referers(request, profile)
    while True:
        try:
            with ydl_factory(options) as ydl:
                ydl.download([request.url])
            break
        except DownloadCancelled:
            raise
        except Exception as exc:  # yt-dlp wraps everything in DownloadError with the site's message
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled("ההורדה בוטלה") from exc
            if referers and _is_embed_only(str(exc)):
                referer = referers.pop(0)
                log(f"Vimeo plays this video only inside the site that embeds it; retrying as embedded on {referer}")
                options["http_headers"] = {**options.get("http_headers", {}), "Referer": referer}
                continue
            raise DownloadError(friendly_error(str(exc))) from exc
        finally:
            shutil.rmtree(request.folder / ".partial", ignore_errors=True)
    if not written:
        raise DownloadError("לא נמצא סרטון להורדה בכתובת הזו.")
    progress(1.0, "הסתיים")
    return written


# --------------------------------------------------------------------------- cookies

@dataclass
class CookieInfo:
    domains: List[str]
    count: int
    login_expires: Optional[float]  # earliest expiry of a login/session cookie (epoch), None if unknown


LOGIN_COOKIE_HINTS = ("logged_in", "session", "sess", "auth", "token", "login")


def parse_cookies(text: str) -> CookieInfo:
    """Read a Netscape cookies.txt (the format browser extensions export). Values are never kept."""
    domains: List[str] = []
    count = 0
    login_expiry: List[float] = []
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, _path, _secure, expiry, name, _value = parts
        count += 1
        domain = domain.lstrip(".").lower()
        if domain not in domains:
            domains.append(domain)
        try:
            expires = float(expiry)
        except ValueError:
            continue
        if expires > 0 and any(hint in name.lower() for hint in LOGIN_COOKIE_HINTS):
            login_expiry.append(expires)
    if count == 0:
        raise DownloadError("זה לא נראה כמו קובץ עוגיות בפורמט cookies.txt (Netscape). ייצא אותו עם תוסף כמו "
                            "'Get cookies.txt LOCALLY'.")
    return CookieInfo(domains[:12], count, min(login_expiry) if login_expiry else None)
