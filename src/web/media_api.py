"""HTTP routes for the Tools screen: downloads, transcriptions, cookies, and 'make a podcast'."""

from __future__ import annotations

import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from ..media import QUALITIES, DownloadError
from ..media.downloader import impersonation_available
from ..transcribe import TRANSCRIBE_LANGUAGES
from .media import MediaItem, is_transcribable, parse_urls, validate_download_options


def _view(item: MediaItem, full: bool = False) -> Dict[str, Any]:
    data = asdict(item)
    data["options"] = {k: v for k, v in item.options.items() if k != "media"}  # no local paths in the UI
    if not full:
        data.pop("log")
    else:
        data["log"] = item.log[-120:]
    return data


def register_media_routes(app: FastAPI, state: Any, project_options: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    store, runner = state.media_store, state.media_runner

    def _item(item_id: str) -> MediaItem:
        item = store.get(item_id)
        if item is None:
            raise HTTPException(404, "הפריט לא נמצא")
        return item

    @app.get("/api/media")
    def media_list() -> Dict[str, Any]:
        return {
            "items": [_view(i) for i in store.list()],
            # A list, not an object: browsers put number-like keys ("720", "1080") first
            "qualities": [[k, v["label"]] for k, v in QUALITIES.items()],
            "languages": TRANSCRIBE_LANGUAGES,
            "cookies": store.cookies(),
            "impersonation": impersonation_available(),
        }

    @app.get("/api/media/{item_id}")
    def media_item(item_id: str) -> Dict[str, Any]:
        return _view(_item(item_id), full=True)

    @app.post("/api/media/download")
    async def media_download(request: Request) -> Dict[str, Any]:
        body = await request.json()
        urls = parse_urls(str(body.get("urls", "")))
        if not urls:
            raise HTTPException(400, "הדבק לפחות קישור אחד שמתחיל ב-https://")
        options = validate_download_options(body)
        if options["cookies"] and store.cookie_path(options["cookies"]) is None:
            raise HTTPException(400, "קובץ העוגיות שנבחר לא נמצא")
        items = [runner.submit_download(url, dict(options)) for url in urls]
        return {"items": [_view(i) for i in items]}

    @app.post("/api/media/upload")
    async def media_upload(request: Request) -> Dict[str, Any]:
        filename = request.query_params.get("filename", "")
        if not is_transcribable(filename):
            raise HTTPException(400, "סוג קובץ לא נתמך לתמלול. אפשר וידאו או שמע (MP4, MKV, MP3, M4A, WAV...).")
        language = request.query_params.get("language", "he")
        item, target = runner.prepare_upload(filename, language if language in TRANSCRIBE_LANGUAGES else "he")
        try:
            with target.open("wb") as handle:
                async for chunk in request.stream():
                    handle.write(chunk)
            if target.stat().st_size == 0:
                raise HTTPException(400, "הקובץ ריק")
        except BaseException:
            store.delete(item.id, force=True)
            raise
        return _view(runner.enqueue_upload(item.id, target))

    @app.post("/api/media/{item_id}/transcribe")
    async def media_transcribe(item_id: str, request: Request) -> Dict[str, Any]:
        item = _item(item_id)
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        language = body.get("language") if body.get("language") in TRANSCRIBE_LANGUAGES else "he"
        if item.kind != "download" or item.status != "done":
            raise HTTPException(409, "אפשר לתמלל אחרי שההורדה הסתיימה")
        media = [store.folder(item.id) / f["name"] for f in item.files if f["kind"] in ("video", "audio")]
        created = [runner.submit_transcription(path, path.stem, language, parent=item.id) for path in media if path.exists()]
        if not created:
            raise HTTPException(409, "אין בהורדה קובץ וידאו או שמע לתמלול")
        return {"items": [_view(i) for i in created]}

    @app.post("/api/media/{item_id}/podcast")
    async def media_to_podcast(item_id: str, request: Request) -> Dict[str, Any]:
        """Turn a finished transcription into a podcast project; the project picks up at its plan."""
        item = _item(item_id)
        srt = next((store.folder(item.id) / f["name"] for f in item.files if f["kind"] == "subtitles"), None)
        if item.kind != "transcription" or item.status != "done" or srt is None or not srt.exists():
            raise HTTPException(409, "אפשר ליצור פודקאסט מתמלול שהסתיים")
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        title = str(body.get("title") or item.title).strip()[:80] or "פודקאסט מתמלול"
        options = {**project_options(body), "title_given": True}
        project = state.store.create(title, "transcript", srt.name, options)
        shutil.copy2(srt, state.store.source_dir(project) / srt.name)
        shutil.copy2(srt, state.store.folder(project) / "transcript.srt")
        state.store.update(project.id, status="transcribed")
        state.runner.submit(project.id, "prepare")
        store.update(item.id, project=project.id)
        return {"project": project.id}

    @app.post("/api/media/{item_id}/cancel")
    def media_cancel(item_id: str) -> Dict[str, Any]:
        runner.cancel(_item(item_id).id)
        return {"ok": True}

    @app.post("/api/media/{item_id}/retry")
    def media_retry(item_id: str) -> Dict[str, Any]:
        try:
            return _view(runner.retry(_item(item_id).id))
        except KeyError:
            raise HTTPException(409, "הפריט עדיין רץ")

    @app.delete("/api/media/{item_id}")
    def media_delete(item_id: str) -> Dict[str, Any]:
        if not store.delete(_item(item_id).id):
            raise HTTPException(409, "אי אפשר למחוק בזמן שהעבודה רצה. בטל אותה קודם.")
        return {"ok": True}

    @app.get("/api/media/{item_id}/files/{index}")
    def media_file(item_id: str, index: int, download: int = 0) -> FileResponse:
        item = _item(item_id)
        path = store.file_path(item, index)
        if path is None:
            raise HTTPException(404, "הקובץ לא נמצא")
        return FileResponse(path, filename=path.name if download else None)

    @app.post("/api/media/cookies")
    async def cookies_save(request: Request) -> Dict[str, Any]:
        text = (await request.body()).decode("utf-8-sig", errors="replace")
        try:
            store.save_cookies(request.query_params.get("name", ""), text)
        except DownloadError as exc:
            raise HTTPException(400, str(exc))
        return {"cookies": store.cookies()}

    @app.delete("/api/media/cookies/{cookie_id}")
    def cookies_delete(cookie_id: str) -> Dict[str, Any]:
        try:
            store.delete_cookies(cookie_id)
        except DownloadError as exc:
            raise HTTPException(404, str(exc))
        return {"cookies": store.cookies()}
