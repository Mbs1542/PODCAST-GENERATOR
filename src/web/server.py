"""HTTP API and static files for the browser UI.

Runs on 127.0.0.1 only. Because any website the user visits could send requests to a
local port, every /api call must carry a per-launch token that only our own page
knows, and the Host header must be the local address (blocks DNS rebinding).
"""

from __future__ import annotations

import json
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from ..audio.dialogue_tts import DIALOGUE_PROVIDERS, GEMINI_VOICE_STYLES, DialogueTTSError, list_elevenlabs_voices
from ..engine.planner import SEGMENT_KINDS, assign_word_budget
from ..engine.prompts import FORMATS
from ..transcribe import MEDIA_EXTENSIONS
from ..video import ACCENTS, SlideStyle, draw_slide
from ..video.slideshow import Slide
from .brand import BrandError, BrandStore
from .gallery import Gallery, GalleryError
from .jobs import JobError, JobRunner
from .projects import Project, ProjectStore
from .settings_store import VOICE_SAMPLE, SettingsStore

APP_ID = "mbs-studio"
STATIC_DIR = Path(__file__).resolve().parent / "static"
TRANSCRIPT_EXTENSIONS = {".txt", ".srt", ".vtt", ".md"}
MAX_TITLE = 80
_SAFE_NAME_RE = re.compile(r"[^\w֐-׿ .()\-]+")


class AppState:
    def __init__(self, root: Path, runner_factory: Optional[Callable[[ProjectStore, SettingsStore], JobRunner]] = None):
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
        self.runner.recover_interrupted()
        self.last_ping = time.time()
        self.allowed_hosts: set = set()


def _project_view(state: AppState, project: Project, full: bool = False) -> Dict[str, Any]:
    view = {
        "id": project.id, "title": project.title, "created": project.created, "status": project.status,
        "running": project.running, "stage": project.stage, "progress": project.progress,
        "error": project.error, "notice": project.notice, "source_kind": project.source_kind,
        "source_name": project.source_name, "options": project.options,
        "files": sorted(state.store.files(project)),
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


def create_app(root: Path, runner_factory=None) -> FastAPI:
    state = AppState(root, runner_factory)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.mbs = state

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if state.allowed_hosts and host not in state.allowed_hosts:
            return PlainTextResponse("Forbidden host", status_code=403)
        path = request.url.path
        if path.startswith("/api/") and path != "/api/hello":
            token = request.headers.get("x-mbs-token") or request.query_params.get("t")
            if not token or not secrets.compare_digest(token, state.token):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    # -------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return html.replace("__MBS_TOKEN__", state.token)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
        return {"ok": True, "busy": state.runner.busy}

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

    return app
