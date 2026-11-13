"""HTTP API and static files for the browser UI.

Runs on 127.0.0.1 only. Because any website the user visits could send requests to a
local port, every /api call must carry a per-launch token that only our own page
knows, and the Host header must be the local address (blocks DNS rebinding).
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ..version import __version__
from ..audio.dialogue_tts import (
    DIALOGUE_PROVIDERS,
    GEMINI_VOICE_STYLES,
    DialogueTTSError,
    add_elevenlabs_library_voice,
    list_elevenlabs_library,
    list_elevenlabs_voices,
)
from ..engine.planner import SEGMENT_KINDS, assign_word_budget
from ..engine.prompts import FORMATS
from ..transcribe import MEDIA_EXTENSIONS
from ..video import ACCENTS, SlideStyle, draw_slide
from ..video.slideshow import Slide
from .brand import BrandError, BrandStore
from .chain import ChainError, pick_folder, resolve_save_folder, reveal
from .delivery import Delivery, DeliveryError
from .gallery import Gallery, GalleryError
from .jobs import JobError, JobRunner
from .media import MediaRunner, MediaStore
from .media_api import register_media_routes
from .projects import Project, ProjectStore
from .settings_store import VOICE_SAMPLE, SettingsStore

APP_ID = "mbs-studio"
SESSION_COOKIE = "mbs_session"
SESSION_DAYS = 30
PUBLIC_PATHS = {"/login", "/logo", "/api/hello"}
CF_ACCESS_HEADER = "cf-access-jwt-assertion"
MAX_FAILED_LOGINS = 5
LOGIN_WINDOW_SECONDS = 300


def _login_page(message: str = "") -> str:
    note = f'<p class="help" style="color:var(--bad)">{html.escape(message)}</p>' if message else ""
    return f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>M.B.S Studio</title>
<link rel="icon" href="/logo"><link rel="stylesheet" href="/static/app.css"></head>
<body><main class="container" style="max-width:420px;padding-top:12vh">
<div class="card stack"><div class="row"><img src="/logo" alt="" class="brand-logo"><h1>M.B.S Studio</h1></div>
<form method="post" action="/login" class="stack">
<label class="field"><span>סיסמת גישה</span><input type="password" name="key" autocomplete="current-password" autofocus required></label>
{note}<button class="btn primary lg" type="submit">כניסה</button></form>
<p class="help">הסיסמה מוגדרת בהפעלת השרת (MBS_ACCESS_KEY).</p></div></main></body></html>"""
STATIC_DIR = Path(__file__).resolve().parent / "static"
MOBILE_DIR = STATIC_DIR / "m"
TRANSCRIPT_EXTENSIONS = {".txt", ".srt", ".vtt", ".md"}
MAX_TITLE = 80
_SAFE_NAME_RE = re.compile(r"[^\w֐-׿ .()\-]+")


class AppState:
    def __init__(self, root: Path, runner_factory: Optional[Callable[[ProjectStore, SettingsStore], JobRunner]] = None,
                 media_runner_factory: Optional[Callable[[MediaStore], MediaRunner]] = None):
        self.root = root
        self.token = secrets.token_urlsafe(24)
        self.settings_store = SettingsStore(root)
        settings, _ = self.settings_store.load_settings()
        outputs = settings.output_base_dir if settings else root / "outputs"
        outputs = outputs if Path(outputs).is_absolute() else root / outputs
        self.store = ProjectStore(outputs)
        self.brand = BrandStore(outputs, root)
        self.runner = runner_factory(self.store, self.settings_store) if runner_factory else JobRunner(self.store, self.settings_store)
        self.runner.brand = self.brand
        self.delivery = Delivery(Path(outputs) / "private")
        self.runner.delivery = self.delivery
        from .credits import CreditsLedger

        self.credits = CreditsLedger(Path(outputs) / "private")
        self.runner.credits = self.credits
        from .exams import ExamRunner, ExamStore

        self.exam_store = ExamStore(outputs)
        self.exam_runner = ExamRunner(self.exam_store, self.store, self.settings_store,
                                      llms_factory=getattr(self.runner, "_llms_factory", None))
        self.exam_runner.credits = self.credits
        from .review import ReviewDeck

        self.review_deck = ReviewDeck(self.exam_store.root)
        self.exam_runner.recover_interrupted()
        self.runner.recover_interrupted()
        self.media_store = MediaStore(outputs)
        self.media_runner = media_runner_factory(self.media_store) if media_runner_factory else MediaRunner(self.media_store)
        self.media_runner.parallel_limit = lambda: self.settings_store.read_env().get("TRANSCRIBE_PARALLEL") or 1
        self.media_runner.recover_interrupted()
        self.last_ping = time.time()
        self.allowed_hosts: set = set()
        # Network mode (a container or server reached from a phone over a VPN): an access key
        # guards the page itself, because the per-launch API token is embedded in that page
        self.access_key: str = ""
        # Or: a reverse proxy verifies the login (MBS_EXTERNAL_AUTH=cloudflare-access) and every
        # request except the public paths must arrive with the header it verified
        self.external_auth: str = ""
        self.sessions: set = set()
        self.failed_logins: Dict[str, list] = {}


def _project_view(state: AppState, project: Project, full: bool = False) -> Dict[str, Any]:
    view = {
        "id": project.id, "title": project.title, "created": project.created, "status": project.status,
        "running": project.running, "stage": project.stage, "progress": project.progress,
        "error": project.error, "notice": project.notice, "source_kind": project.source_kind,
        "source_name": project.source_name, "options": project.options,
        "files": sorted(state.store.files(project)),
        "drive_uploading": state.delivery.is_uploading(project.id),
        "stage_started": project.stage_started,
    }
    dialogue = state.store.read_json(state.store.run_paths(project).dialogue_path)
    if dialogue:
        meta = dialogue.get("metadata", {})
        view["minutes"] = meta.get("estimated_duration_minutes")
        view["format"] = meta.get("format")
    if full:
        view["log"] = project.log[-150:]
    return view


def _sanitize_plan(edited: Dict[str, Any], current: Dict[str, Any], section_ids: set) -> Dict[str, Any]:
    """Accept only the editable parts of a plan, in the shape the engine expects."""
    def clean_text(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    plan = dict(current)
    plan["title"] = clean_text(edited.get("title"), 200) or current.get("title", "")
    plan["hook"] = clean_text(edited.get("hook"), 600)
    plan["takeaways"] = [clean_text(t, 200) for t in edited.get("takeaways", current.get("takeaways", [])) if clean_text(t, 200)][:5]
    fmt = edited.get("format", current.get("format"))
    plan["format"] = fmt if fmt in FORMATS else current.get("format", "deep_dive")
    try:
        plan["target_minutes"] = round(min(max(float(edited.get("target_minutes", current["target_minutes"])), 2.0), 60.0), 1)
    except (TypeError, ValueError):
        plan["target_minutes"] = current["target_minutes"]

    segments = []
    for seg in edited.get("segments", []):
        if not isinstance(seg, dict) or seg.get("kind") not in SEGMENT_KINDS:
            continue
        segments.append({
            "kind": seg["kind"],
            "title": clean_text(seg.get("title"), 200),
            "goal": clean_text(seg.get("goal"), 600),
            "concepts": [clean_text(c, 120) for c in seg.get("concepts", []) if clean_text(c, 120)],
            "source_sections": [s for s in seg.get("source_sections", []) if s in section_ids],
            "examples": [clean_text(e, 300) for e in seg.get("examples", []) if clean_text(e, 300)],
            "beats": [clean_text(b, 300) for b in seg.get("beats", []) if clean_text(b, 300)][:4],
            "weight": seg.get("weight", 2),
        })
    intro = next((s for s in segments if s["kind"] == "intro"), None)
    closing = next((s for s in reversed(segments) if s["kind"] == "closing"), None)
    body = [s for s in segments if s["kind"] == "body"]
    if not body:
        raise HTTPException(400, "התוכנית צריכה לפחות מקטע תוכן אחד")
    plan["segments"] = [s for s in (intro, *body, closing) if s]
    return plan


def create_app(root: Path, runner_factory=None, media_runner_factory=None) -> FastAPI:
    state = AppState(root, runner_factory, media_runner_factory)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.mbs = state

    def _signed_in(request: Request) -> bool:
        return not state.access_key or request.cookies.get(SESSION_COOKIE, "") in state.sessions

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if state.allowed_hosts and host not in state.allowed_hosts:
            return PlainTextResponse("Forbidden host", status_code=403)
        path = request.url.path
        # Behind Cloudflare Access, Traefik has checked this JWT's signature and audience; a request
        # without it did not come through that gate (another container calling the port directly)
        if (state.external_auth == "cloudflare-access" and path not in PUBLIC_PATHS
                and not request.headers.get(CF_ACCESS_HEADER)):
            return PlainTextResponse("Sign in through Cloudflare Access", status_code=403)
        if state.access_key and not _signed_in(request) and path not in PUBLIC_PATHS and not path.startswith("/static/"):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "sign in first"}, status_code=401)
            return HTMLResponse(_login_page(), status_code=401)
        if path.startswith("/api/") and path != "/api/hello":
            token = request.headers.get("x-mbs-token") or request.query_params.get("t")
            if not token or not secrets.compare_digest(token, state.token):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    # -------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        # Google sends the sign-in back to this page (the loopback address of an installed app)
        params = request.query_params
        if params.get("state") and state.delivery.drive.is_pending(params["state"]):
            if params.get("error"):
                state.delivery.drive.cancel(params["state"])
                return RedirectResponse(f"/#/settings?drive_error={quote('ההתחברות ל-Google בוטלה')}", status_code=303)
            try:
                state.delivery.drive.finish(params.get("code", ""), params["state"])
            except DeliveryError as exc:
                return RedirectResponse(f"/#/settings?drive_error={quote(str(exc))}", status_code=303)
            return RedirectResponse("/#/settings?drive=connected", status_code=303)
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__MBS_TOKEN__", state.token).replace("__MBS_VERSION__", __version__))

    # -------------------------------------------------------------- the phone app (an installable PWA)

    @app.get("/m", include_in_schema=False)
    def mobile_redirect() -> Response:
        return RedirectResponse("/m/", status_code=308)

    @app.get("/m/", response_class=HTMLResponse)
    def mobile_index() -> Response:
        html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__MBS_TOKEN__", state.token).replace("__MBS_VERSION__", __version__))

    @app.get("/m/sw.js")
    def mobile_worker() -> Response:
        # Served from /m/ so its scope is the phone app only; the version names its caches
        script = (MOBILE_DIR / "sw.js").read_text(encoding="utf-8").replace("__MBS_VERSION__", __version__)
        return Response(script, media_type="text/javascript; charset=utf-8")

    @app.get("/m/manifest.webmanifest")
    def mobile_manifest() -> Response:
        return Response((MOBILE_DIR / "manifest.webmanifest").read_bytes(), media_type="application/manifest+json")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.post("/login")
    async def login(request: Request) -> Response:
        if not state.access_key:
            return RedirectResponse("/", status_code=303)
        client = request.client.host if request.client else "?"
        now = time.time()
        recent = [t for t in state.failed_logins.get(client, []) if now - t < LOGIN_WINDOW_SECONDS]
        if len(recent) >= MAX_FAILED_LOGINS:
            return HTMLResponse(_login_page("יותר מדי ניסיונות. נסה שוב בעוד כמה דקות."), status_code=429)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        key = (form.get("key") or [""])[0]
        if not secrets.compare_digest(key.encode("utf-8"), state.access_key.encode("utf-8")):
            state.failed_logins[client] = recent + [now]
            await asyncio.sleep(1.0)  # slows down guessing
            return HTMLResponse(_login_page("הסיסמה שגויה."), status_code=401)
        state.failed_logins.pop(client, None)
        session = secrets.token_urlsafe(32)
        state.sessions.add(session)
        response = RedirectResponse("/", status_code=303)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="strict", secure=secure,
                            max_age=SESSION_DAYS * 86400)
        return response

    @app.post("/logout")
    def logout(request: Request) -> Response:
        state.sessions.discard(request.cookies.get(SESSION_COOKIE, ""))
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/guide")
    def guide() -> FileResponse:
        return FileResponse(root / "docs" / "guide.he.html", media_type="text/html; charset=utf-8")

    @app.get("/logo")
    def logo() -> FileResponse:
        # The app shows the chosen logo; with "no logo" in the video it still needs an icon
        path = state.brand.logo_path() or root / "LOGO.JPEG"
        return FileResponse(path)

    @app.get("/api/hello")
    def hello() -> Dict[str, str]:
        return {"app": APP_ID}

    @app.post("/api/ping")
    def ping() -> Dict[str, Any]:
        state.last_ping = time.time()
        return {"ok": True, "busy": state.runner.busy or state.media_runner.busy}

    # -------------------------------------------------------------- settings

    @app.get("/api/status")
    def status() -> Dict[str, Any]:
        settings, error = state.settings_store.load_settings()
        return {**state.settings_store.status(), "settings_ok": settings is not None, "settings_error": error}

    @app.get("/api/settings")
    def get_settings() -> Dict[str, Any]:
        return {"fields": state.settings_store.view()}

    @app.put("/api/settings")
    async def put_settings(request: Request) -> Dict[str, Any]:
        body = await request.json()
        try:
            state.settings_store.update(body.get("values", {}))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        settings, error = state.settings_store.load_settings()
        return {"fields": state.settings_store.view(), "settings_ok": settings is not None, "settings_error": error}

    @app.post("/api/settings/test")
    def test_settings() -> Dict[str, Any]:
        return state.settings_store.test_connections()

    @app.get("/api/voices/elevenlabs")
    def elevenlabs_voices() -> Dict[str, Any]:
        key = state.settings_store.read_env().get("ELEVENLABS_API_KEY", "")
        if not key:
            raise HTTPException(400, "שמור קודם מפתח ElevenLabs")
        try:
            return {"voices": list_elevenlabs_voices(key)}
        except DialogueTTSError as exc:
            raise HTTPException(400, str(exc))

    def _eleven_key() -> str:
        key = state.settings_store.read_env().get("ELEVENLABS_API_KEY", "")
        if not key:
            raise HTTPException(400, "שמור קודם מפתח ElevenLabs")
        return key

    # -------------------------------------------------------------- cost estimate (before making an episode)

    _account_cache: Dict[str, Any] = {}

    def _voice_account() -> Optional[Dict[str, Any]]:
        """The ElevenLabs plan and characters left, kept for a minute (the estimate refreshes as options change)."""
        from ..audio.dialogue_tts import elevenlabs_account

        key = state.settings_store.read_env().get("ELEVENLABS_API_KEY", "")
        if not key:
            return None
        cached = _account_cache.get(key)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        try:
            account = elevenlabs_account(key)
        except Exception:  # offline or a bad key: the estimate still works without the account part
            return None
        _account_cache[key] = (time.time(), account)
        return account

    def _estimate(words: int, minutes: float, voice: str, language: str, images: int = 0) -> Dict[str, Any]:
        from ..utils.pricing import estimate_podcast

        env = state.settings_store.read_env()
        writer = env.get("LLM_PROVIDER") or "gemini"
        default_voice = env.get("DEFAULT_TTS_PROVIDER") or "elevenlabs"
        voice = voice if voice in ("elevenlabs", "gemini", "azure") else (default_voice if default_voice != "auto" else "elevenlabs")
        result = estimate_podcast(words, minutes, language=language, writer=writer,
                                  writer_model=env.get("GEMINI_TEXT_MODEL") or "gemini-3.8-flash", voice=voice, images=images)
        if voice == "elevenlabs":
            account = _voice_account()
            result["voice"]["account"] = account
            # eleven_v3 / eleven_v4 bill one credit per character
            result["voice"]["credits"] = result["voice"]["characters"]
            if account:
                left = account["characters_left"]
                result["voice"]["enough"] = left >= result["voice"]["credits"]
                result["voice"]["max_minutes"] = round(left / max(1, result["voice"]["credits"]) * minutes, 1)
        # Google: the episode's Gemini part in shekels, against the balance the user entered
        ledger = state.credits.view()
        rate = ledger["usd_rate"]
        gemini_script = writer == "gemini"
        google_low = (result["script"]["low"] if gemini_script else 0) + (result["voice"]["cost"] if voice == "gemini" else 0) + result["images"]["cost"]
        google_high = (result["script"]["high"] if gemini_script else 0) + (result["voice"]["cost"] if voice == "gemini" else 0) + result["images"]["cost"]
        result["google"] = {"low": round(google_low * rate, 2), "high": round(google_high * rate, 2), "usd_rate": rate,
                            "balance": ledger["balance"], "left": ledger["left"],
                            "enough": None if ledger["left"] is None else ledger["left"] >= google_high * rate}
        return result

    @app.get("/api/credits")
    def credits_view() -> Dict[str, Any]:
        """What is left to spend: ElevenLabs credits (live) and the Google balance (from the ledger)."""
        _account_cache.clear()
        return {"elevenlabs": _voice_account(), "google": state.credits.view()}

    @app.put("/api/credits")
    async def credits_set(request: Request) -> Dict[str, Any]:
        body = await request.json()
        try:
            balance = float(body.get("google_balance"))
            rate = float(body["usd_rate"]) if body.get("usd_rate") not in (None, "") else None
            state.credits.set_google_balance(balance, rate)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc) if str(exc) else "ערך לא תקין")
        from starlette.concurrency import run_in_threadpool

        # the ElevenLabs lookup is a network call: off the event loop, so other requests are not held up
        return {"elevenlabs": await run_in_threadpool(_voice_account), "google": state.credits.view()}

    @app.get("/api/estimate")
    def estimate(words: int = 0, minutes: float = 0, voice: str = "", language: str = "he", images: int = 0) -> Dict[str, Any]:
        if words <= 0 or not 1 <= minutes <= 60:
            raise HTTPException(400, "חסרים אורך התמלול ואורך הפרק")
        return _estimate(words, minutes, voice, language, max(0, min(images, 40)))

    @app.get("/api/projects/{project_id}/estimate")
    def project_estimate(project_id: str, minutes: float = 0) -> Dict[str, Any]:
        """For the plan screen: the plan's length (or one being edited), this lecture, this project's voices."""
        from .chain import transcript_stats

        project = _get(project_id)
        transcript = state.store.transcript_path(project)
        if transcript is None:
            raise HTTPException(404, "אין עדיין תמלול")
        stats = transcript_stats(transcript.read_text(encoding="utf-8-sig"))
        plan = state.store.read_json(state.store.engine_dir(project) / "03_plan.json") or {}
        target = minutes or float(plan.get("target_minutes") or project.options.get("minutes") or 0)
        if not target:
            from ..engine.planner import target_minutes
            target = target_minutes(stats["minutes"])
        return {**_estimate(stats["words"], target, project.options.get("voice", ""), project.options.get("language", "he")),
                "lecture_minutes": stats["minutes"], "words": stats["words"]}

    @app.get("/api/voices/elevenlabs/library")
    def elevenlabs_library(language: str = "he") -> Dict[str, Any]:
        """Native Hebrew voices from ElevenLabs' Voice Library (the account itself starts with English ones)."""
        try:
            return list_elevenlabs_library(_eleven_key(), "en" if language == "en" else "he")
        except DialogueTTSError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/voices/elevenlabs/library/add")
    async def elevenlabs_library_add(request: Request) -> Dict[str, Any]:
        body = await request.json()
        try:
            voice_id = await run_in_threadpool(add_elevenlabs_library_voice, _eleven_key(), str(body.get("public_owner_id", "")),
                                               str(body.get("voice_id", "")), str(body.get("name", "")))
        except DialogueTTSError as exc:
            raise HTTPException(400, str(exc))
        return {"voice_id": voice_id}

    @app.get("/api/voices/gemini")
    def gemini_voices() -> Dict[str, Any]:
        return {"voices": [{"name": name, "style": style} for name, style in GEMINI_VOICE_STYLES.items()]}

    @app.get("/api/voices/gemini/{voice}/sample")
    async def gemini_voice_sample(voice: str) -> FileResponse:
        if voice not in GEMINI_VOICE_STYLES:
            raise HTTPException(404, "קול לא מוכר")
        try:
            path = await run_in_threadpool(state.settings_store.voice_sample, voice)
        except DialogueTTSError as exc:
            raise HTTPException(400, str(exc))
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/voices/preview")
    async def voice_preview(request: Request) -> FileResponse:
        provider = (await request.json()).get("provider")
        if provider not in DIALOGUE_PROVIDERS:
            raise HTTPException(400, "דוגמה זמינה ל-ElevenLabs ול-Gemini")
        folder = Path(tempfile.mkdtemp(prefix="mbs-voice-"))
        try:
            synth = state.settings_store.conversation_synth(provider)
            rendered = await run_in_threadpool(synth.render, VOICE_SAMPLE, folder / "sample")
        except DialogueTTSError as exc:
            raise HTTPException(400, str(exc))
        media = "audio/mpeg" if rendered.path.suffix == ".mp3" else "audio/wav"
        return FileResponse(rendered.path, media_type=media)

    # -------------------------------------------------------------- look & branding

    async def _read_upload(request: Request, limit: int) -> bytes:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                raise HTTPException(413, "הקובץ גדול מדי")
        if not data:
            raise HTTPException(400, "הקובץ ריק")
        return bytes(data)

    def _brand_call(fn, *args):
        try:
            return fn(*args)
        except BrandError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/brand")
    def get_brand() -> Dict[str, Any]:
        return state.brand.view()

    @app.put("/api/brand")
    async def put_brand(request: Request) -> Dict[str, Any]:
        _brand_call(state.brand.update, (await request.json()).get("prefs", {}))
        return state.brand.view()

    @app.post("/api/brand/logos")
    async def add_logo(request: Request) -> Dict[str, Any]:
        data = await _read_upload(request, 12 * 1024 * 1024)
        logo = _brand_call(state.brand.add_logo, request.query_params.get("filename", ""), data)
        _brand_call(state.brand.update, {"logo": logo["id"]})  # a new logo is usually meant to be used
        return state.brand.view()

    @app.get("/api/brand/logos/{logo_id}")
    def logo_file(logo_id: str) -> FileResponse:
        path = state.brand.logo_path(logo_id)
        if path is None:
            raise HTTPException(404, "הלוגו לא נמצא")
        return FileResponse(path)

    @app.delete("/api/brand/logos/{logo_id}")
    def delete_logo(logo_id: str) -> Dict[str, Any]:
        _brand_call(state.brand.delete_logo, logo_id)
        return state.brand.view()

    @app.post("/api/brand/fonts")
    async def add_font(request: Request) -> Dict[str, Any]:
        data = await _read_upload(request, 14 * 1024 * 1024)
        _brand_call(state.brand.add_font, request.query_params.get("filename", ""), data)
        return state.brand.view()

    @app.post("/api/brand/fonts/download/{key}")
    async def download_font(key: str) -> Dict[str, Any]:
        await run_in_threadpool(_brand_call, state.brand.download_font, key)
        return state.brand.view()

    @app.get("/api/brand/fonts/{font_id}/file")
    def font_file(font_id: str) -> FileResponse:
        path = state.brand.font_path(font_id)
        if path is None:
            raise HTTPException(404, "הגופן לא מותקן")
        return FileResponse(path, media_type="font/otf" if path.suffix.lower() == ".otf" else "font/ttf")

    @app.delete("/api/brand/fonts/{font_id}")
    def delete_font(font_id: str) -> Dict[str, Any]:
        _brand_call(state.brand.delete_font, font_id)
        return state.brand.view()

    @app.post("/api/brand/preview")
    async def brand_preview(request: Request) -> Response:
        """One video slide drawn exactly as the video will draw it, from unsaved choices too."""
        body = await request.json()
        prefs = {**state.brand.prefs(), **{k: str(v) for k, v in (body.get("prefs") or {}).items()}}
        language = "en" if body.get("language") == "en" else "he"
        font_id = prefs["font_en" if language == "en" else "font_he"]
        font = state.brand.font_path(font_id) if font_id != "auto" else state.brand.resolve_font(language)
        style = SlideStyle(title_font=font, body_font=font, logo=state.brand.logo_path(prefs.get("logo")),
                           accent=ACCENTS.get(prefs.get("accent"), ACCENTS["teal"]), rtl=language != "en")
        image = None
        if body.get("project") and body.get("image"):
            project = state.store.get(str(body["project"]))
            if project:
                image = Gallery(state.store.folder(project)).path(str(body["image"]))
        sample_title = "Overfitting, explained" if language == "en" else "מבנה התא: אברונים ותפקידיהם"
        sample_chapter = "Why the model memorizes" if language == "en" else "המיטוכונדריה: תחנת הכוח של התא"
        slide = Slide(0, 1, 0 if image else None, str(body.get("chapter") or sample_chapter)[:120],
                      intro=body.get("kind") == "intro")
        title = str(body.get("title") or sample_title)[:120]

        def draw() -> bytes:
            import io
            picture = draw_slide(slide, image, title, style).resize((960, 540))
            buffer = io.BytesIO()
            picture.save(buffer, "JPEG", quality=85)
            return buffer.getvalue()

        return Response(await run_in_threadpool(draw), media_type="image/jpeg")

    # -------------------------------------------------------------- gallery

    def _gallery(project_id: str) -> Gallery:
        return Gallery(state.store.folder(_get(project_id)))

    @app.get("/api/projects/{project_id}/gallery")
    def get_gallery(project_id: str) -> Dict[str, Any]:
        return _gallery(project_id).view()

    @app.post("/api/projects/{project_id}/gallery/upload")
    async def gallery_upload(project_id: str, request: Request) -> Dict[str, Any]:
        gallery = _gallery(project_id)
        data = await _read_upload(request, 26 * 1024 * 1024)
        try:
            gallery.add(request.query_params.get("filename", "image.png"), data)
        except GalleryError as exc:
            raise HTTPException(400, str(exc))
        return gallery.view()

    @app.put("/api/projects/{project_id}/gallery")
    async def put_gallery(project_id: str, request: Request) -> Dict[str, Any]:
        body = await request.json()
        try:
            return _gallery(project_id).update(body.get("items"), body.get("rotate_seconds"))
        except GalleryError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/projects/{project_id}/gallery/{item_id}")
    def delete_gallery_item(project_id: str, item_id: str) -> Dict[str, Any]:
        gallery = _gallery(project_id)
        try:
            gallery.delete(item_id)
        except GalleryError as exc:
            raise HTTPException(404, str(exc))
        return gallery.view()

    @app.get("/api/projects/{project_id}/gallery/{item_id}/{size}")
    def gallery_image(project_id: str, item_id: str, size: str) -> FileResponse:
        path = _gallery(project_id).path(item_id, thumb=size == "thumb")
        if path is None:
            raise HTTPException(404, "התמונה לא נמצאה")
        return FileResponse(path)

    # -------------------------------------------------------------- projects

    @app.get("/api/projects")
    def list_projects() -> Dict[str, Any]:
        return {"projects": [_project_view(state, p) for p in state.store.list()], "formats": list(FORMATS)}

    def _options(params: Dict[str, Any]) -> Dict[str, Any]:
        fmt = params.get("format") or "auto"
        voice = params.get("voice") or "auto"
        try:
            minutes = float(params.get("minutes") or 0)
        except ValueError:
            minutes = 0
        return {
            "format": fmt if fmt == "auto" or fmt in FORMATS else "auto",
            "minutes": minutes if 2 <= minutes <= 60 else 0,
            "voice": voice if voice in ("auto", "azure", "elevenlabs", "gemini") else "auto",
            # Hebrew unless English is explicitly chosen; the lecture's own language does not decide this
            "language": "en" if params.get("language") == "en" else "he",
            "focus": " ".join(str(params.get("focus") or "").split())[:500],
        }

    register_media_routes(app, state, _options)
    from .exam_api import register_exam_routes

    register_exam_routes(app, state)
    from .concepts import register_concept_routes

    register_concept_routes(app, state)

    # -------------------------------------------------------------- output folders & Google Drive

    def _local(request: Request) -> bool:
        return not state.access_key and (request.client.host if request.client else "") in {"127.0.0.1", "::1", "localhost", "testclient"}

    def _require_local(request: Request) -> None:
        if not _local(request):
            raise HTTPException(403, "זמין רק במחשב שעליו האפליקציה רצה")

    def _delivery_view(request: Request) -> Dict[str, Any]:
        return {"prefs": state.delivery.prefs.get(), "drive": state.delivery.drive.status(),
                "default_root": str(state.delivery.prefs.default_root()), "local": _local(request)}

    @app.get("/api/delivery")
    def delivery_get(request: Request) -> Dict[str, Any]:
        return _delivery_view(request)

    @app.put("/api/delivery")
    async def delivery_put(request: Request) -> Dict[str, Any]:
        body = await request.json()
        changes = {k: body[k] for k in ("drive_auto", "drive_folder", "output_root") if k in body}
        if str(changes.get("output_root") or "").strip():
            _require_local(request)
            try:
                changes["output_root"] = str(resolve_save_folder(changes["output_root"]))
            except ChainError as exc:
                raise HTTPException(400, str(exc))
        if changes.get("drive_auto") and not state.delivery.drive.status()["connected"]:
            raise HTTPException(409, "קודם צריך להתחבר ל-Google Drive")
        state.delivery.prefs.update(changes)
        return _delivery_view(request)

    @app.post("/api/delivery/pick-folder")
    async def delivery_pick(request: Request) -> Dict[str, Any]:
        _require_local(request)
        try:
            chosen = await asyncio.get_running_loop().run_in_executor(None, pick_folder, state.delivery.prefs.get()["output_root"])
        except ChainError as exc:
            raise HTTPException(501, str(exc))
        return {"folder": chosen or ""}

    @app.post("/api/delivery/google/client")
    async def delivery_client(request: Request) -> Dict[str, Any]:
        _require_local(request)
        try:
            state.delivery.drive.save_client(await request.body())
        except DeliveryError as exc:
            raise HTTPException(400, str(exc))
        return _delivery_view(request)

    @app.post("/api/delivery/google/connect")
    def delivery_connect(request: Request) -> Dict[str, Any]:
        """The Google sign-in address; Google returns to this app's own page on this computer."""
        _require_local(request)
        port = request.url.port or 80
        try:
            return {"url": state.delivery.drive.auth_url(f"http://127.0.0.1:{port}/")}
        except DeliveryError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/delivery/google/disconnect")
    def delivery_disconnect(request: Request) -> Dict[str, Any]:
        state.delivery.prefs.update({"drive_auto": False})
        state.delivery.drive.disconnect()
        return _delivery_view(request)

    @app.post("/api/projects/{project_id}/deliver")
    async def project_deliver(project_id: str, request: Request) -> Dict[str, Any]:
        """Build (or refresh) the podcast's folder now, and upload it to Drive if asked."""
        project = _get(project_id)
        if project.running:
            raise HTTPException(409, "אפשר אחרי שהתהליך הנוכחי מסתיים")
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        upload = bool(body.get("upload"))
        if upload and not state.delivery.drive.status()["connected"]:
            raise HTTPException(409, "Google Drive לא מחובר. התחבר בהגדרות.")
        folder = await run_in_threadpool(state.runner.deliver, project.id, upload)
        if folder is None:
            raise HTTPException(409, "לא הצלחתי לבנות את תיקיית הפרק. הפרטים ביומן הפרויקט.")
        return _project_view(state, state.store.get(project.id))

    @app.post("/api/projects/{project_id}/reveal")
    def project_reveal(project_id: str, request: Request) -> Dict[str, Any]:
        _require_local(request)
        folder = _get(project_id).options.get("output_folder")
        if not folder or not Path(folder).exists():
            raise HTTPException(404, "עוד אין תיקייה לפרק הזה")
        try:
            reveal(Path(folder))
        except ChainError as exc:
            raise HTTPException(501, str(exc))
        return {"ok": True}

    @app.post("/api/projects/upload")
    async def upload(request: Request) -> Dict[str, Any]:
        params = dict(request.query_params)
        filename = _SAFE_NAME_RE.sub("_", Path(params.get("filename", "")).name).strip() or "lecture"
        suffix = Path(filename).suffix.lower()
        if suffix in MEDIA_EXTENSIONS:
            kind = "media"
        elif suffix in TRANSCRIPT_EXTENSIONS:
            kind = "transcript"
        else:
            raise HTTPException(400, "סוג קובץ לא נתמך. אפשר להעלות וידאו, שמע או תמלול (txt, srt, vtt).")
        title = (params.get("title") or "").strip()[:MAX_TITLE]
        options = {**_options(params), "title_given": bool(title)}
        project = state.store.create(title or Path(filename).stem[:MAX_TITLE], kind, filename, options)

        target = state.store.source_dir(project) / filename
        with target.open("wb") as handle:
            async for chunk in request.stream():
                handle.write(chunk)
        if target.stat().st_size == 0:
            state.store.delete(project.id)
            raise HTTPException(400, "הקובץ ריק")
        if kind == "transcript":
            (state.store.folder(project) / f"transcript{suffix}").write_bytes(target.read_bytes())
            state.store.update(project.id, status="transcribed")
        state.runner.submit(project.id, "prepare")
        return _project_view(state, state.store.get(project.id))

    @app.post("/api/projects/url")
    async def from_url(request: Request) -> Dict[str, Any]:
        body = await request.json()
        url = str(body.get("url", "")).strip()
        if not re.match(r"^https?://", url):
            raise HTTPException(400, "קישור לא תקין")
        title = str(body.get("title", "")).strip()[:MAX_TITLE]
        options = {**_options(body), "title_given": bool(title)}
        project = state.store.create(title or "הרצאה מקישור", "url", url, options)
        state.runner.submit(project.id, "prepare")
        return _project_view(state, state.store.get(project.id))

    def _get(project_id: str) -> Project:
        project = state.store.get(project_id)
        if project is None:
            raise HTTPException(404, "הפרויקט לא נמצא")
        return project

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> Dict[str, Any]:
        return _project_view(state, _get(project_id), full=True)

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str) -> Dict[str, Any]:
        if not state.store.delete(_get(project_id).id):
            raise HTTPException(409, "אי אפשר למחוק פרויקט בזמן שתהליך רץ עליו")
        return {"ok": True}

    @app.post("/api/projects/{project_id}/jobs/{kind}")
    async def run_job(project_id: str, kind: str, request: Request) -> Dict[str, Any]:
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        params = {k: body[k] for k in ("count", "style") if k in body}
        try:
            project = state.runner.submit(_get(project_id).id, kind, force=bool(body.get("force")), params=params)
        except JobError as exc:
            raise HTTPException(409, str(exc))
        return _project_view(state, project)

    # -------------------------------------------------------------- plan & script

    def _plan_path(project: Project) -> Path:
        return state.store.engine_dir(project) / "03_plan.json"

    @app.get("/api/projects/{project_id}/plan")
    def get_plan(project_id: str) -> Dict[str, Any]:
        project = _get(project_id)
        plan = state.store.read_json(_plan_path(project))
        if not plan:
            raise HTTPException(404, "אין תוכנית עדיין")
        profile = state.store.read_json(state.store.engine_dir(project) / "02_profile.json") or {}
        plan.pop("_key", None)
        profile.pop("_key", None)
        return {"plan": plan, "profile": profile, "formats": {k: v["label"] for k, v in FORMATS.items()}}

    @app.put("/api/projects/{project_id}/plan")
    async def put_plan(project_id: str, request: Request) -> Dict[str, Any]:
        project = _get(project_id)
        if project.running:
            raise HTTPException(409, "אי אפשר לערוך בזמן שתהליך רץ")
        stored = state.store.read_json(_plan_path(project))
        clean = state.store.read_json(state.store.engine_dir(project) / "01_clean.json") or {}
        if not stored:
            raise HTTPException(404, "אין תוכנית עדיין")
        key = stored.pop("_key", None)
        edited = (await request.json()).get("plan", {})
        plan = _sanitize_plan(edited, stored, {s["id"] for s in clean.get("sections", [])})
        plan = assign_word_budget(plan, clean.get("language", "he"))
        # Keep the stage key: the engine then treats the edited plan as the plan to follow
        _plan_path(project).write_text(json.dumps({**plan, "_key": key}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"plan": plan}

    @app.get("/api/projects/{project_id}/script")
    def get_script(project_id: str) -> Dict[str, Any]:
        project = _get(project_id)
        dialogue = state.store.read_json(state.store.run_paths(project).dialogue_path)
        if not dialogue:
            raise HTTPException(404, "אין תסריט עדיין")
        report = state.store.read_json(state.store.engine_dir(project) / "05_report.json") or {}
        chapters = state.store.read_json(state.store.folder(project) / "chapters.json") or {}
        return {"dialogue": dialogue, "report": report, "timing": chapters}

    @app.get("/api/projects/{project_id}/script.txt", response_class=PlainTextResponse)
    def script_text(project_id: str) -> PlainTextResponse:
        project = _get(project_id)
        dialogue = state.store.read_json(state.store.run_paths(project).dialogue_path) or {}
        names = {"Roee": "רועי", "Noa": "נועה"}
        lines, chapters = [], {c["start_index"]: c["title"] for c in dialogue.get("metadata", {}).get("chapters", [])}
        for index, turn in enumerate(dialogue.get("dialogue", [])):
            if index in chapters:
                lines.append(f"\n## {chapters[index]}\n")
            lines.append(f"{names.get(turn['speaker'], turn['speaker'])}: {turn['text']}")
        filename = f"{project.id}-script.txt"
        return PlainTextResponse("\n".join(lines).strip() + "\n", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

    @app.get("/api/projects/{project_id}/files/{name}")
    def get_file(project_id: str, name: str, download: int = 0) -> FileResponse:
        project = _get(project_id)
        path = state.store.files(project).get(name)
        if path is None:
            raise HTTPException(404, "הקובץ לא נמצא")
        if download:
            return FileResponse(path, filename=f"{project.title}{path.suffix}")
        return FileResponse(path)

    @app.get("/api/projects/{project_id}/log.txt")
    def project_log(project_id: str) -> PlainTextResponse:
        project = _get(project_id)
        name = quote(f"{project.title} - יומן עבודה.txt")
        return PlainTextResponse("\n".join(project.log) + "\n", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

    @app.get("/api/projects/{project_id}/summary")
    def get_summary(project_id: str, format: str = "html", download: int = 0, print: int = 0) -> Response:
        """The lesson summary, built from the lesson analysis: html (designed page), md or docx."""
        from .summary import FORMATS as SUMMARY_FORMATS, SummaryError, project_summary, render_html, render_markdown, write_docx

        project = _get(project_id)
        if format not in SUMMARY_FORMATS and format != "json":
            raise HTTPException(400, "פורמט לא נתמך")
        try:
            model = project_summary(state.store, project)
        except SummaryError as exc:
            raise HTTPException(404, str(exc))
        if format == "json":  # the phone app draws the summary itself and keeps it for offline reading
            return JSONResponse(model)
        accent = ACCENTS.get(state.brand.prefs().get("accent", "teal"), ACCENTS["teal"])
        media_type, suffix = SUMMARY_FORMATS[format]
        name = quote(f"{project.title} - סיכום{suffix}")
        headers = {"Content-Disposition": f"{'attachment' if download else 'inline'}; filename*=UTF-8''{name}"}
        if format == "html":
            page = render_html(model, accent)
            if print:  # "PDF": open the print dialog with every answer shown; the user saves as PDF
                page = page.replace("</body>", "<script>addEventListener('load',()=>{document.querySelectorAll('details')"
                                               ".forEach(d=>d.open=true);setTimeout(()=>window.print(),300)})</script></body>")
            return Response(page, media_type=media_type, headers=headers)
        if format == "md":
            return Response(render_markdown(model), media_type=media_type, headers=headers)
        target = state.store.folder(project) / "summary.docx"
        write_docx(model, target, accent)
        return FileResponse(target, media_type=media_type, headers=headers)

    return app
