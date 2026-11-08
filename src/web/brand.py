"""Look and branding: app theme, logos, and the fonts used in the video.

Everything lives under <outputs>/brand so it survives reinstalling the app:
  brand.json          the choices (theme, accent, logo, Hebrew and English fonts)
  logos/<file>        uploaded logos
  fonts/<file>        uploaded fonts and free fonts downloaded on request
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from PIL import Image, ImageFont

from ..video import ACCENTS

THEMES = ("auto", "light", "dark")
MAX_LOGO_BYTES = 10 * 1024 * 1024
MAX_FONT_BYTES = 12 * 1024 * 1024
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FONT_EXTENSIONS = {".ttf", ".otf"}
GOOGLE_FONTS_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"

# Free fonts (SIL Open Font License) the app can download on request.
# scripts: which output languages the font supports.
FONT_CATALOG: Dict[str, Dict[str, Any]] = {
    "heebo": {"name": "Heebo", "path": "heebo/Heebo%5Bwght%5D.ttf", "scripts": ["he", "en"], "note": "נקי ומודרני"},
    "rubik": {"name": "Rubik", "path": "rubik/Rubik%5Bwght%5D.ttf", "scripts": ["he", "en"], "note": "עגלגל וידידותי"},
    "assistant": {"name": "Assistant", "path": "assistant/Assistant%5Bwght%5D.ttf", "scripts": ["he", "en"], "note": "קריא במיוחד"},
    "secularone": {"name": "Secular One", "path": "secularone/SecularOne-Regular.ttf", "scripts": ["he", "en"], "note": "כותרות בולטות"},
    "suezone": {"name": "Suez One", "path": "suezone/SuezOne-Regular.ttf", "scripts": ["he", "en"], "note": "כותרות עם סריף"},
    "frankruhllibre": {"name": "Frank Ruhl Libre", "path": "frankruhllibre/FrankRuhlLibre%5Bwght%5D.ttf", "scripts": ["he", "en"], "note": "קלאסי, ספרותי"},
    "notosanshebrew": {"name": "Noto Sans Hebrew", "path": "notosanshebrew/NotoSansHebrew%5Bwdth,wght%5D.ttf", "scripts": ["he"], "note": "ניטרלי"},
    "alef": {"name": "Alef", "path": "alef/Alef-Bold.ttf", "scripts": ["he", "en"], "note": "פשוט ומודגש"},
    "varelaround": {"name": "Varela Round", "path": "varelaround/VarelaRound-Regular.ttf", "scripts": ["he", "en"], "note": "רך ועגול"},
    "karantina": {"name": "Karantina", "path": "karantina/Karantina-Bold.ttf", "scripts": ["he", "en"], "note": "צר ודרמטי"},
    "amaticsc": {"name": "Amatic SC", "path": "amaticsc/AmaticSC-Bold.ttf", "scripts": ["he", "en"], "note": "כתב יד"},
    "inter": {"name": "Inter", "path": "inter/Inter%5Bopsz,wght%5D.ttf", "scripts": ["en"], "note": "English UI classic"},
    "montserrat": {"name": "Montserrat", "path": "montserrat/Montserrat%5Bwght%5D.ttf", "scripts": ["en"], "note": "Geometric headlines"},
    "poppins": {"name": "Poppins", "path": "poppins/Poppins-SemiBold.ttf", "scripts": ["en"], "note": "Friendly geometric"},
    "playfairdisplay": {"name": "Playfair Display", "path": "playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf", "scripts": ["en"], "note": "Elegant serif"},
}

# Windows fonts that are usually present; only the ones found on the machine are offered
SYSTEM_FONTS = [
    ("arialbd.ttf", "Arial Bold", ["he", "en"]),
    ("arial.ttf", "Arial", ["he", "en"]),
    ("segoeuib.ttf", "Segoe UI Bold", ["he", "en"]),
    ("segoeui.ttf", "Segoe UI", ["he", "en"]),
    ("tahomabd.ttf", "Tahoma Bold", ["he", "en"]),
    ("davidbd.ttf", "David Bold", ["he", "en"]),
    ("david.ttf", "David", ["he", "en"]),
    ("frank.ttf", "FrankRuehl", ["he", "en"]),
    ("gisha.ttf", "Gisha", ["he", "en"]),
    ("georgiab.ttf", "Georgia Bold", ["en"]),
    ("verdanab.ttf", "Verdana Bold", ["en"]),
    ("calibrib.ttf", "Calibri Bold", ["en"]),
]

DEFAULT_PREFS = {"theme": "auto", "accent": "teal", "logo": "default", "font_he": "auto", "font_en": "auto"}


class BrandError(ValueError):
    """A message written for the user."""


def _windows_font_dirs() -> List[Path]:
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return dirs


def _safe_name(filename: str, allowed: set) -> str:
    stem, suffix = os.path.splitext(Path(filename).name)
    suffix = suffix.lower()
    if suffix not in allowed:
        raise BrandError(f"סוג קובץ לא נתמך ({suffix or 'ללא סיומת'}). אפשר: {', '.join(sorted(allowed))}")
    stem = re.sub(r"[^\w\-]+", "_", stem, flags=re.ASCII).strip("_")[:40] or "file"
    return f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"


class BrandStore:
    def __init__(self, outputs_dir: Path, app_root: Path, http: Any = None) -> None:
        self.dir = Path(outputs_dir) / "brand"
        self.logos_dir = self.dir / "logos"
        self.fonts_dir = self.dir / "fonts"
        for folder in (self.logos_dir, self.fonts_dir):
            folder.mkdir(parents=True, exist_ok=True)
        self.default_logo = Path(app_root) / "LOGO.JPEG"
        self.http = http or requests
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ choices

    def prefs(self) -> Dict[str, str]:
        try:
            saved = json.loads((self.dir / "brand.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        prefs = {**DEFAULT_PREFS, **{k: v for k, v in saved.items() if k in DEFAULT_PREFS and isinstance(v, str)}}
        # A choice whose file was deleted falls back to the default instead of failing later
        if prefs["logo"] not in ("default", "none") and not (self.logos_dir / prefs["logo"]).exists():
            prefs["logo"] = "default"
        for key in ("font_he", "font_en"):
            if prefs[key] != "auto" and self.font_path(prefs[key]) is None:
                prefs[key] = "auto"
        return prefs

    def update(self, values: Dict[str, Any]) -> Dict[str, str]:
        prefs = self.prefs()
        for key, value in values.items():
            if key not in DEFAULT_PREFS:
                continue
            value = str(value)
            if key == "theme" and value not in THEMES:
                raise BrandError("ערכת נושא לא מוכרת")
            if key == "accent" and value not in ACCENTS:
                raise BrandError("צבע לא מוכר")
            if key == "logo" and value not in ("default", "none") and not (self.logos_dir / Path(value).name).exists():
                raise BrandError("הלוגו לא נמצא")
            if key in ("font_he", "font_en") and value != "auto" and self.font_path(value) is None:
                raise BrandError("הגופן לא מותקן")
            prefs[key] = Path(value).name if key == "logo" else value
        with self._lock:
            (self.dir / "brand.json").write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
        return prefs

    # ------------------------------------------------------------------ logos

    def logos(self) -> List[Dict[str, str]]:
        items = [{"id": "default", "name": "הלוגו של M.B.S"}] if self.default_logo.exists() else []
        for path in sorted(self.logos_dir.iterdir(), key=lambda p: p.stat().st_mtime):
            if path.suffix.lower() in LOGO_EXTENSIONS:
                items.append({"id": path.name, "name": path.stem.rsplit("-", 1)[0]})
        return items

    def logo_path(self, logo_id: Optional[str] = None) -> Optional[Path]:
        logo_id = logo_id or self.prefs()["logo"]
        if logo_id == "none":
            return None
        if logo_id == "default":
            return self.default_logo if self.default_logo.exists() else None
        path = self.logos_dir / Path(logo_id).name
        return path if path.exists() else None

    def add_logo(self, filename: str, data: bytes) -> Dict[str, str]:
        if len(data) > MAX_LOGO_BYTES:
            raise BrandError("הלוגו גדול מדי (עד 10MB)")
        name = _safe_name(filename, LOGO_EXTENSIONS)
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception:
            raise BrandError("הקובץ אינו תמונה תקינה")
        (self.logos_dir / name).write_bytes(data)
        return {"id": name, "name": name.rsplit("-", 1)[0]}

    def delete_logo(self, logo_id: str) -> None:
        path = self.logos_dir / Path(logo_id).name
        if not path.exists():
            raise BrandError("הלוגו לא נמצא")
        path.unlink()

    # ------------------------------------------------------------------ fonts

    def fonts(self) -> List[Dict[str, Any]]:
        """Every font the user can pick: installed ones first, then free ones to download."""
        items: List[Dict[str, Any]] = []
        for path in sorted(self.fonts_dir.glob("up-*")):
            items.append({"id": f"u:{path.name}", "name": _font_name(path), "scripts": ["he", "en"],
                          "source": "upload", "installed": True})
        for key, spec in FONT_CATALOG.items():
            installed = (self.fonts_dir / f"g-{key}.ttf").exists()
            items.append({"id": f"g:{key}", "name": spec["name"], "scripts": spec["scripts"], "note": spec["note"],
                          "source": "google", "installed": installed})
        for filename, name, scripts in SYSTEM_FONTS:
            if self._system_font(filename):
                items.append({"id": f"s:{filename}", "name": name, "scripts": scripts, "source": "system", "installed": True})
        return items

    def _system_font(self, filename: str) -> Optional[Path]:
        for folder in _windows_font_dirs():
            path = folder / filename
            if path.exists():
                return path
        return None

    def font_path(self, font_id: str) -> Optional[Path]:
        kind, _, name = font_id.partition(":")
        name = Path(name).name
        if kind == "u":
            path = self.fonts_dir / name
        elif kind == "g" and name in FONT_CATALOG:
            path = self.fonts_dir / f"g-{name}.ttf"
        elif kind == "s" and any(name == f for f, _, _ in SYSTEM_FONTS):
            return self._system_font(name)
        else:
            return None
        return path if path.exists() else None

    def resolve_font(self, language: str) -> Optional[Path]:
        """The font for titles in the given output language ('auto' picks a good installed one)."""
        prefs = self.prefs()
        chosen = prefs["font_en" if language == "en" else "font_he"]
        if chosen != "auto":
            return self.font_path(chosen)
        if language == "en" and prefs["font_he"] != "auto":
            return self.font_path(prefs["font_he"])  # Hebrew fonts here all carry Latin too
        for candidate in ("g:heebo", "g:rubik", "s:arialbd.ttf", "s:segoeuib.ttf", "s:arial.ttf"):
            path = self.font_path(candidate)
            if path:
                return path
        return None

    def download_font(self, key: str) -> Dict[str, Any]:
        spec = FONT_CATALOG.get(key)
        if spec is None:
            raise BrandError("גופן לא מוכר")
        target = self.fonts_dir / f"g-{key}.ttf"
        if not target.exists():
            try:
                response = self.http.get(GOOGLE_FONTS_BASE + spec["path"], timeout=60)
                response.raise_for_status()
            except Exception as exc:
                raise BrandError(f"ההורדה נכשלה. בדוק את החיבור לאינטרנט ({exc.__class__.__name__}).")
            data = response.content
            if len(data) > MAX_FONT_BYTES:
                raise BrandError("קובץ הגופן גדול מהצפוי")
            _check_font(data)
            temp = target.with_suffix(".part")
            temp.write_bytes(data)
            temp.replace(target)
        return {"id": f"g:{key}", "name": spec["name"]}

    def add_font(self, filename: str, data: bytes) -> Dict[str, Any]:
        if len(data) > MAX_FONT_BYTES:
            raise BrandError("קובץ הגופן גדול מדי (עד 12MB)")
        name = "up-" + _safe_name(filename, FONT_EXTENSIONS)
        _check_font(data)
        path = self.fonts_dir / name
        path.write_bytes(data)
        return {"id": f"u:{name}", "name": _font_name(path)}

    def delete_font(self, font_id: str) -> None:
        kind, _, name = font_id.partition(":")
        if kind not in ("u", "g"):
            raise BrandError("אפשר למחוק רק גופנים שהעלית או הורדת")
        path = self.font_path(font_id)
        if path is None:
            raise BrandError("הגופן לא נמצא")
        path.unlink()

    def view(self) -> Dict[str, Any]:
        return {"prefs": self.prefs(), "logos": self.logos(), "fonts": self.fonts(), "accents": {
            key: "#%02x%02x%02x" % rgb for key, rgb in ACCENTS.items()}}


def _check_font(data: bytes) -> None:
    try:
        ImageFont.truetype(io.BytesIO(data), 40)
    except Exception:
        raise BrandError("הקובץ אינו גופן תקין (TTF או OTF)")


def _font_name(path: Path) -> str:
    try:
        family, style = ImageFont.truetype(str(path), 20).getname()
        return f"{family} {style}".replace(" Regular", "").strip()
    except Exception:
        return path.stem
