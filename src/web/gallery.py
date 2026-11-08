"""A project's image gallery: the user's own images and AI-made ones, and which of them
the video shows, in what order and how often they change.

<project>/gallery/
  gallery.json      {"items": [...], "rotate_seconds": 8}; items are in the video's order
  <id>.<ext>        the image
  thumbs/<id>.jpg   a small copy for the browser
"""

from __future__ import annotations

import io
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_ITEMS = 80
DEFAULT_ROTATE = 8.0
THUMB_WIDTH = 480


class GalleryError(ValueError):
    """A message written for the user."""


class Gallery:
    def __init__(self, project_folder: Path) -> None:
        self.dir = Path(project_folder) / "gallery"
        self.thumbs = self.dir / "thumbs"
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ state

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads((self.dir / "gallery.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        items = [i for i in data.get("items", []) if isinstance(i, dict) and (self.dir / str(i.get("file", ""))).exists()]
        return {"items": items, "rotate_seconds": float(data.get("rotate_seconds") or DEFAULT_ROTATE)}

    def _write(self, data: Dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        temp = self.dir / "gallery.json.tmp"
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.dir / "gallery.json")

    def view(self) -> Dict[str, Any]:
        with self._lock:
            return self._read()

    def selected_paths(self) -> List[Path]:
        return [self.dir / i["file"] for i in self.view()["items"] if i.get("selected", True)]

    def path(self, item_id: str, thumb: bool = False) -> Optional[Path]:
        item = next((i for i in self.view()["items"] if i["id"] == item_id), None)
        if item is None:
            return None
        path = self.thumbs / f"{item['id']}.jpg" if thumb else self.dir / item["file"]
        return path if path.exists() else None

    # ------------------------------------------------------------------ changes

    def add(self, filename: str, data: bytes, source: str = "upload", prompt: str = "") -> Dict[str, Any]:
        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in IMAGE_EXTENSIONS:
            raise GalleryError("סוג קובץ לא נתמך. אפשר PNG, JPG או WEBP.")
        if len(data) > MAX_IMAGE_BYTES:
            raise GalleryError("התמונה גדולה מדי (עד 25MB)")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                thumb = ImageOps.exif_transpose(image).convert("RGB")
        except Exception:
            raise GalleryError("הקובץ אינו תמונה תקינה")
        with self._lock:
            state = self._read()
            if len(state["items"]) >= MAX_ITEMS:
                raise GalleryError(f"בגלריה יש כבר {MAX_ITEMS} תמונות. מחק כמה כדי להוסיף.")
            item_id = uuid.uuid4().hex[:10]
            self.thumbs.mkdir(parents=True, exist_ok=True)
            (self.dir / f"{item_id}{suffix}").write_bytes(data)
            thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
            thumb.save(self.thumbs / f"{item_id}.jpg", quality=82)
            item = {"id": item_id, "file": f"{item_id}{suffix}", "source": source, "prompt": prompt[:500],
                    "selected": True, "created": datetime.now().isoformat(timespec="seconds")}
            state["items"].append(item)
            self._write(state)
            return item

    def update(self, items: Optional[List[Dict[str, Any]]] = None, rotate_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Reorder and select: `items` lists ids in the new order with their `selected` flag."""
        with self._lock:
            state = self._read()
            if items is not None:
                by_id = {i["id"]: i for i in state["items"]}
                ordered = []
                for entry in items:
                    item = by_id.pop(str(entry.get("id")), None) if isinstance(entry, dict) else None
                    if item is not None:
                        item["selected"] = bool(entry.get("selected", True))
                        ordered.append(item)
                state["items"] = ordered + list(by_id.values())  # anything not mentioned keeps its place at the end
            if rotate_seconds is not None:
                try:
                    state["rotate_seconds"] = round(min(max(float(rotate_seconds), 3.0), 60.0), 1)
                except (TypeError, ValueError):
                    raise GalleryError("זמן החלפה לא תקין")
            self._write(state)
            return state

    def delete(self, item_id: str) -> None:
        with self._lock:
            state = self._read()
            item = next((i for i in state["items"] if i["id"] == item_id), None)
            if item is None:
                raise GalleryError("התמונה לא נמצאה")
            state["items"].remove(item)
            (self.dir / item["file"]).unlink(missing_ok=True)
            (self.thumbs / f"{item_id}.jpg").unlink(missing_ok=True)
            self._write(state)
