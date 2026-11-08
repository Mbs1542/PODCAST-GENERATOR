"""Podcast video: the episode's audio over a slideshow of chosen images.

Images rotate every few seconds; the current chapter's title and the episode title sit
over the image in the chosen fonts, and the logo sits in a corner. Each distinct slide
is drawn once with Pillow and ffmpeg's concat demuxer holds it for its duration, so a
30-minute episode renders in a minute or two without a video toolkit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from ..utils.text_utils import shape_rtl

SIZE = (1920, 1080)
MARGIN = 96
INTRO_SECONDS = 3.0           # the title card while the intro music plays
MIN_ROTATE, MAX_ROTATE = 3.0, 60.0
ACCENTS: Dict[str, Tuple[int, int, int]] = {
    "teal": (45, 190, 178), "blue": (66, 133, 244), "purple": (151, 98, 236),
    "orange": (245, 140, 50), "rose": (236, 84, 122), "gold": (226, 178, 60),
}

Log = Callable[[str], None]
Runner = Callable[..., subprocess.CompletedProcess]


class VideoError(RuntimeError):
    """A message written for the user."""


@dataclass
class SlideStyle:
    title_font: Optional[Path] = None   # chapter titles and the title card
    body_font: Optional[Path] = None    # the small episode line (English text uses it too)
    logo: Optional[Path] = None
    accent: Tuple[int, int, int] = ACCENTS["teal"]
    rtl: bool = True


@dataclass
class Slide:
    start: float
    end: float
    image: Optional[int]       # index into the image list; None = a plain title card
    chapter: str = ""
    intro: bool = False
    key: Tuple = field(default=())

    @property
    def duration(self) -> float:
        return self.end - self.start


# --------------------------------------------------------------------------- timeline

def plan_slides(
    duration: float,
    image_count: int,
    rotate_seconds: float,
    chapters: Sequence[Dict],
    intro_seconds: float = INTRO_SECONDS,
) -> List[Slide]:
    """Cut the episode into slides: a title card, then images rotating every
    `rotate_seconds`, with a cut at every chapter start so the title changes on time."""
    rotate = min(max(float(rotate_seconds or 8), MIN_ROTATE), MAX_ROTATE)
    intro = min(intro_seconds, duration)
    slides = [Slide(0.0, intro, None, intro=True)] if intro > 0 else []

    chapter_starts = sorted((float(c.get("start", 0)), str(c.get("title", ""))) for c in chapters)

    def chapter_at(t: float) -> str:
        title = ""
        for start, name in chapter_starts:
            if start <= t + 1e-6:
                title = name
        return title

    cuts = {intro, duration}
    t = intro
    while t < duration:
        cuts.add(t)
        t += rotate
    cuts.update(s for s, _ in chapter_starts if intro < s < duration)
    points = sorted(c for c in cuts if intro <= c <= duration)

    index = 0
    for start, end in zip(points, points[1:]):
        if end - start < 0.05:
            continue
        # A new chapter shows a new image even if the rotation interval has not passed
        at_rotation = abs((start - intro) / rotate - round((start - intro) / rotate)) < 1e-6
        at_chapter = any(abs(start - s) < 1e-6 for s, _ in chapter_starts)
        if slides and not slides[-1].intro and (at_rotation or at_chapter):
            index += 1
        image = (index % image_count) if image_count else None
        slides.append(Slide(start, end, image, chapter_at(start)))

    # Merge neighbours that would look identical (same image and title)
    merged: List[Slide] = []
    for slide in slides:
        if merged and not slide.intro and not merged[-1].intro and merged[-1].image == slide.image \
                and merged[-1].chapter == slide.chapter:
            merged[-1].end = slide.end
        else:
            merged.append(slide)
    for slide in merged:
        slide.key = ("intro",) if slide.intro else (slide.image, slide.chapter)
    return merged


# --------------------------------------------------------------------------- drawing

def _font(path: Optional[Path], size: int, bold: bool = True) -> ImageFont.ImageFont:
    candidates = [str(path)] if path else []
    candidates += ["arialbd.ttf" if bold else "arial.ttf", "tahomabd.ttf" if bold else "tahoma.ttf", "DejaVuSans.ttf"]
    for candidate in candidates:
        try:
            font = ImageFont.truetype(candidate, size)
        except OSError:
            continue
        if bold:
            # Variable fonts (Heebo, Rubik...) open at their default weight; titles want bold
            try:
                axes = font.get_variation_axes()
                font.set_variation_by_axes([min(700, a["maximum"]) if i == 0 else a["default"] for i, a in enumerate(axes)])
            except Exception:
                pass
        return font
    return ImageFont.load_default()


def _is_rtl_text(text: str) -> bool:
    return any("֐" <= ch <= "׿" or "؀" <= ch <= "ۿ" for ch in text)


def _visual(text: str) -> str:
    return shape_rtl(text) if _is_rtl_text(text) else text


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int = 2) -> List[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,.") + "…"
    return lines


def _cover(image: Image.Image, size=SIZE) -> Image.Image:
    return ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"), size, Image.LANCZOS, centering=(0.5, 0.45))


def _gradient(accent: Tuple[int, int, int], size=SIZE) -> Image.Image:
    top = tuple(int(c * 0.22) for c in accent)
    bottom = (10, 12, 18)
    column = Image.new("RGB", (1, size[1]))
    for y in range(size[1]):
        r = y / (size[1] - 1)
        column.putpixel((0, y), tuple(int(top[i] * (1 - r) + bottom[i] * r) for i in range(3)))
    return column.resize(size)


def _shade(canvas: Image.Image, start: float = 0.45, strength: int = 215) -> Image.Image:
    """Darken the lower part so white text stays readable on any photo."""
    h = canvas.height
    mask = Image.new("L", (1, h))
    for y in range(h):
        r = (y / h - start) / (1 - start)
        mask.putpixel((0, y), int(max(0.0, min(1.0, r)) ** 1.3 * strength))
    black = Image.new("RGB", canvas.size, (0, 0, 0))
    return Image.composite(black, canvas, mask.resize(canvas.size))


def _paste_logo(canvas: Image.Image, logo_path: Optional[Path], height: int, rtl: bool, center: bool = False,
                top: int = MARGIN // 2) -> None:
    if not logo_path:
        return
    try:
        logo = ImageOps.exif_transpose(Image.open(logo_path)).convert("RGBA")
    except OSError:
        return
    width = max(1, round(logo.width * height / logo.height))
    logo = logo.resize((width, height), Image.LANCZOS)
    if logo.getextrema()[3][0] == 255:  # no transparency (JPEG): round the corners so it looks placed, not cut
        mask = Image.new("L", logo.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *logo.size), radius=height // 6, fill=255)
        logo.putalpha(mask)
    if center:
        x = (canvas.width - width) // 2
    else:
        # The logo sits opposite the text's starting edge
        x = MARGIN // 2 if rtl else canvas.width - width - MARGIN // 2
    canvas.paste(logo, (x, top), logo)


def draw_slide(
    slide: Slide,
    image_path: Optional[Path],
    episode_title: str,
    style: SlideStyle,
    size: Tuple[int, int] = SIZE,
) -> Image.Image:
    rtl = style.rtl
    if slide.intro or image_path is None:
        canvas = _gradient(style.accent, size)
        if image_path is not None and not slide.intro:
            pass
    else:
        try:
            canvas = _shade(_cover(Image.open(image_path), size))
        except OSError:
            canvas = _gradient(style.accent, size)
    draw = ImageDraw.Draw(canvas)
    width = size[0] - 2 * MARGIN

    if slide.intro:
        _paste_logo(canvas, style.logo, 200, rtl, center=True, top=size[1] // 2 - 300)
        title_font = _font(style.title_font, 96)
        lines = _wrap(draw, episode_title, title_font, width, max_lines=3)
        y = size[1] // 2 - 40 + (0 if style.logo else -80)
        for line in lines:
            draw.text((size[0] // 2, y), _visual(line), font=title_font, fill=(255, 255, 255), anchor="ma")
            y += int(title_font.size * 1.25)
        bar_y = y + 30
        draw.rounded_rectangle((size[0] // 2 - 90, bar_y, size[0] // 2 + 90, bar_y + 10), radius=5, fill=style.accent)
        return canvas

    _paste_logo(canvas, style.logo, 110, rtl)
    x = size[0] - MARGIN if rtl else MARGIN
    anchor = "rs" if rtl else "ls"
    chapter_font = _font(style.title_font, 72)
    episode_font = _font(style.body_font or style.title_font, 38, bold=False)

    y = size[1] - MARGIN
    chapter_lines = _wrap(draw, slide.chapter, chapter_font, width) if slide.chapter else []
    for line in reversed(chapter_lines):
        draw.text((x, y), _visual(line), font=chapter_font, fill=(255, 255, 255), anchor=anchor)
        y -= int(chapter_font.size * 1.2)
    # A short accent bar and the episode name above the chapter title
    bar_w = 120
    bar_x0 = x - bar_w if rtl else x
    draw.rounded_rectangle((bar_x0, y - 8, bar_x0 + bar_w, y), radius=4, fill=style.accent)
    y -= 26
    draw.text((x, y), _visual(episode_title), font=episode_font, fill=(225, 228, 235), anchor=anchor)
    return canvas


# --------------------------------------------------------------------------- render

def probe_duration(audio: Path, run: Runner = subprocess.run) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    result = run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(audio)],
                 capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        raise VideoError("לא הצלחתי לקרוא את אורך הפודקאסט (ffprobe).")


def render_video(
    audio: Path,
    output: Path,
    images: Sequence[Path],
    chapters: Sequence[Dict],
    episode_title: str,
    style: SlideStyle,
    rotate_seconds: float = 8.0,
    work_dir: Optional[Path] = None,
    log: Optional[Log] = None,
    run: Runner = subprocess.run,
    duration: Optional[float] = None,
    progress: Optional[Callable[[float], None]] = None,
) -> Path:
    log = log or (lambda _m: None)
    progress = progress or (lambda _f: None)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VideoError("ffmpeg חסר. הרץ את 'תיקון התקנה'.")
    total = duration if duration is not None else probe_duration(audio, run)
    slides = plan_slides(total, len(images), rotate_seconds, chapters)
    work = Path(work_dir or output.parent / "slides")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    files: Dict[Tuple, Path] = {}
    distinct = len({s.key for s in slides})
    for slide in slides:
        if slide.key in files:
            continue
        image = images[slide.image] if slide.image is not None else None
        path = work / f"slide_{len(files):04d}.png"
        draw_slide(slide, image, episode_title, style).save(path, optimize=False, compress_level=1)
        files[slide.key] = path
        progress(0.4 * len(files) / max(1, distinct))
    log(f"Drew {len(files)} slides for {len(slides)} cuts ({total / 60:.1f} min)")

    lines = ["ffconcat version 1.0"]
    for slide in slides:
        lines += [f"file '{files[slide.key].name}'", f"duration {slide.duration:.3f}"]
    lines.append(f"file '{files[slides[-1].key].name}'")  # the concat demuxer needs the last file twice
    (work / "slides.ffconcat").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (work / "timeline.json").write_text(json.dumps(
        [{"start": s.start, "end": s.end, "image": s.image, "chapter": s.chapter} for s in slides], ensure_ascii=False), encoding="utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.stem + ".partial.mp4")
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(work / "slides.ffconcat"),
        "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(temp),
    ]
    log("Encoding the video")
    result = run(command, capture_output=True, text=True)
    if getattr(result, "returncode", 1) != 0 or not temp.exists():
        detail = (getattr(result, "stderr", "") or "").strip().splitlines()[-1:] or ["unknown error"]
        raise VideoError(f"יצירת הווידאו נכשלה (ffmpeg): {detail[0][:200]}")
    temp.replace(output)
    progress(1.0)
    return output
