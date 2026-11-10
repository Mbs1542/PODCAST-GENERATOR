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
from ..transcribe import TRANSCRIBE_LANGUAGES, TRANSCRIBE_QUALITIES, normalize_quality
from .chain import ChainError, combine_transcripts, pick_folder, recommend_length, resolve_save_folder, reveal, transcript_stats
from .media import MediaItem, MediaStore, is_transcribable, parse_urls, validate_download_options

MAX_PODCAST_SOURCES = 12
LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _view(item: MediaItem, store: MediaStore, full: bool = False) -> Dict[str, Any]:
    data = asdict(item)
    data["options"] = {k: v for k, v in item.options.items() if k != "media"}
    for entry in data["files"]:
        entry.pop("path", None)  # full paths stay on the server; the folder is shown once, below
    folder = store.output_dir(item)
    data["folder"] = str(folder) if folder else ""
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

    def _default_quality() -> str:
        return normalize_quality(state.settings_store.read_env().get("TRANSCRIBE_QUALITY"))

    def _quality(value: Any) -> str:
        return normalize_quality(value) if value else _default_quality()

    def _local(request: Request) -> bool:
        """Folders on disk are chosen only by someone sitting at this computer, never over the network."""
        return not getattr(state, "access_key", "") and (request.client.host if request.client else "") in LOCAL_CLIENTS

    def _require_local(request: Request) -> None:
        if not _local(request):
            raise HTTPException(403, "בחירת תיקייה זמינה רק במחשב שעליו האפליקציה רצה")

    def _finished_transcriptions(ids: Any) -> list:
        if not isinstance(ids, list) or not ids:
            raise HTTPException(400, "בחר לפחות תמלול אחד")
        items = []
        for item_id in dict.fromkeys(str(i) for i in ids[:MAX_PODCAST_SOURCES]):
            item = _item(item_id)
            if item.kind != "transcription" or item.status != "done" or store.subtitles_path(item) is None:
                raise HTTPException(409, f"\"{item.title}\": אפשר להשתמש רק בתמלול שהסתיים")
            items.append(item)
        return items

    @app.get("/api/media")
    def media_list(request: Request) -> Dict[str, Any]:
        return {
            "items": [_view(i, store) for i in store.list()],
            # A list, not an object: browsers put number-like keys ("720", "1080") first
            "qualities": [[k, v["label"]] for k, v in QUALITIES.items()],
            "languages": TRANSCRIBE_LANGUAGES,
            "cookies": store.cookies(),
            "impersonation": impersonation_available(),
            "local": _local(request),
            "transcribe_qualities": [[k, v["label"], v["note"]] for k, v in TRANSCRIBE_QUALITIES.items()],
            "default_quality": _default_quality(),
            "parallel": runner.limit(),
        }

    # Fixed paths before /api/media/{item_id}, which would otherwise catch them
    @app.post("/api/media/pick-folder")
    async def media_pick_folder(request: Request) -> Dict[str, Any]:
        _require_local(request)
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        import asyncio

        try:  # the dialog blocks until the user answers; keep the server responsive meanwhile
            chosen = await asyncio.get_running_loop().run_in_executor(None, pick_folder, str(body.get("start") or ""))
        except ChainError as exc:
            raise HTTPException(501, str(exc))
        if not chosen:
            return {"folder": ""}
        try:
            return {"folder": str(resolve_save_folder(chosen))}
        except ChainError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/media/estimate")
    def media_estimate(items: str = "") -> Dict[str, Any]:
        """Length recommendation for a podcast made of these finished transcriptions (comma-separated ids)."""
        chosen = _finished_transcriptions([i for i in items.split(",") if i])
        stats = [transcript_stats(store.subtitles_path(i).read_text(encoding="utf-8-sig")) for i in chosen]
        minutes = sum(s["minutes"] for s in stats)
        return {**recommend_length(minutes, len(chosen)),
                "parts": [{"id": i.id, "title": i.title, **s} for i, s in zip(chosen, stats)]}

    @app.post("/api/media/podcast")
    async def media_podcast_many(request: Request) -> Dict[str, Any]:
        """One podcast from one or more finished transcriptions, in the order given."""
        body = await request.json()
        return _make_podcast(_finished_transcriptions(body.get("items")), body)

    @app.get("/api/media/{item_id}")
    def media_item(item_id: str) -> Dict[str, Any]:
        return _view(_item(item_id), store, full=True)

    @app.post("/api/media/download")
    async def media_download(request: Request) -> Dict[str, Any]:
        body = await request.json()
        urls = parse_urls(str(body.get("urls", "")))
        if not urls:
            raise HTTPException(400, "הדבק לפחות קישור אחד שמתחיל ב-https://")
        options = validate_download_options(body)
        options["transcribe_quality"] = _quality(body.get("transcribe_quality"))
        if options["cookies"] and store.cookie_path(options["cookies"]) is None:
            raise HTTPException(400, "קובץ העוגיות שנבחר לא נמצא")
        if str(body.get("folder") or "").strip():
            _require_local(request)
            try:
                options["folder"] = str(resolve_save_folder(str(body["folder"])))
            except ChainError as exc:
                raise HTTPException(400, str(exc))
        items = [runner.submit_download(url, dict(options)) for url in urls]
        return {"items": [_view(i, store) for i in items]}

    @app.post("/api/media/{item_id}/reveal")
    def media_reveal(item_id: str, request: Request) -> Dict[str, Any]:
        _require_local(request)
        item = _item(item_id)
        existing = [p for p in (store.path_of(item, f) for f in item.files) if p.exists()]
        target = existing[0] if existing else store.output_dir(item) or store.folder(item.id)
        try:
            reveal(target)
        except ChainError as exc:
            raise HTTPException(501, str(exc))
        return {"ok": True}

    @app.post("/api/media/upload")
    async def media_upload(request: Request) -> Dict[str, Any]:
        filename = request.query_params.get("filename", "")
        if not is_transcribable(filename):
            raise HTTPException(400, "סוג קובץ לא נתמך לתמלול. אפשר וידאו או שמע (MP4, MKV, MP3, M4A, WAV...).")
        language = request.query_params.get("language", "he")
        item, target = runner.prepare_upload(filename, language if language in TRANSCRIBE_LANGUAGES else "he",
                                             _quality(request.query_params.get("quality")))
        try:
            with target.open("wb") as handle:
                async for chunk in request.stream():
                    handle.write(chunk)
            if target.stat().st_size == 0:
                raise HTTPException(400, "הקובץ ריק")
        except BaseException:
            store.delete(item.id, force=True)
            raise
        return _view(runner.enqueue_upload(item.id, target), store)

    @app.post("/api/media/{item_id}/transcribe")
    async def media_transcribe(item_id: str, request: Request) -> Dict[str, Any]:
        """Send a finished download on to transcription (all its media, or the files picked by index)."""
        item = _item(item_id)
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        language = body.get("language") if body.get("language") in TRANSCRIBE_LANGUAGES else "he"
        if item.kind != "download" or item.status != "done":
            raise HTTPException(409, "אפשר לתמלל אחרי שההורדה הסתיימה")
        media = store.media_paths(item)
        picked = body.get("files")
        if isinstance(picked, list) and picked:
            media = [media[i] for i in dict.fromkeys(int(i) for i in picked if str(i).isdigit()) if i < len(media)]
        quality = _quality(body.get("quality"))
        created = [runner.submit_transcription(path, path.stem, language, parent=item.id, quality=quality)
                   for path in media if path.exists()]
        if not created:
            raise HTTPException(409, "אין בהורדה קובץ וידאו או שמע לתמלול")
        return {"items": [_view(i, store) for i in created]}

    @app.post("/api/media/{item_id}/podcast")
    async def media_to_podcast(item_id: str, request: Request) -> Dict[str, Any]:
        """Turn a finished transcription into a podcast project; the project picks up at its plan."""
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        return _make_podcast(_finished_transcriptions([item_id]), body)

    def _make_podcast(items: list, body: Dict[str, Any]) -> Dict[str, Any]:
        options = project_options(body)
        parts = [(i.title, store.subtitles_path(i).read_text(encoding="utf-8-sig")) for i in items]
        default_title = items[0].title if len(items) == 1 else f"{items[0].title} ועוד {len(items) - 1}"
        title = str(body.get("title") or default_title).strip()[:80] or "פודקאסט מתמלול"
        # The podcast and its summary are also saved where the lessons are, when that is the user's own folder
        export_dir = next((str(d) for d in (store.output_dir(i) or _parent_dir(i) for i in items) if d), "")
        # The lecture files and transcripts go into the podcast's own folder at the end
        source_media = [str(i.options["media"]) for i in items if i.options.get("media")]
        source_transcripts = [str(store.path_of(i, f)) for i in items for f in i.files if f["kind"] in ("subtitles", "text")]
        options = {**options, "title_given": True, "sources": [i.title for i in items], "export_dir": export_dir,
                   "source_media": source_media, "source_transcripts": source_transcripts}
        source_name = store.subtitles_path(items[0]).name if len(items) == 1 else f"{len(items)} תמלולים"
        project = state.store.create(title, "transcript", source_name, options)
        for item in items:
            srt = store.subtitles_path(item)
            shutil.copy2(srt, state.store.source_dir(project) / srt.name)
        combined = combine_transcripts(parts, options.get("language", "he"))
        (state.store.folder(project) / "transcript.srt").write_text(combined, encoding="utf-8")
        state.store.update(project.id, status="transcribed")
        state.runner.submit(project.id, "prepare")
        for item in items:
            store.update(item.id, project=project.id)
        return {"project": project.id}

    def _parent_dir(item: MediaItem):
        """A transcription's folder when it was saved next to a video in the user's own folder."""
        parent = store.get(item.parent) if item.parent else None
        return store.output_dir(parent) if parent else None

    @app.post("/api/media/{item_id}/cancel")
    def media_cancel(item_id: str) -> Dict[str, Any]:
        runner.cancel(_item(item_id).id)
        return {"ok": True}

    @app.post("/api/media/{item_id}/retry")
    def media_retry(item_id: str) -> Dict[str, Any]:
        try:
            return _view(runner.retry(_item(item_id).id), store)
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
