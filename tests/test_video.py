"""The slideshow video: timeline, slide drawing and the ffmpeg command (ffmpeg stubbed)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from src.video import SlideStyle, draw_slide, plan_slides, render_video
from src.video.ai_images import ImageGenError, generate_images, image_prompts
from src.video.slideshow import Slide

CHAPTERS = [{"title": "פתיחה", "start": 3.0}, {"title": "המיטוכונדריה", "start": 20.0}, {"title": "סיכום", "start": 50.0}]


def test_images_rotate_and_every_chapter_starts_on_time():
    slides = plan_slides(60.0, image_count=3, rotate_seconds=8, chapters=CHAPTERS)
    assert slides[0].intro and slides[0].end == 3.0
    assert slides[-1].end == 60.0
    assert all(a.end == pytest.approx(b.start) for a, b in zip(slides, slides[1:]))  # no gaps
    starts = {s.start for s in slides}
    assert {20.0, 50.0} <= starts  # a cut exactly at each chapter
    assert all(s.duration <= 8.0 + 1e-6 for s in slides[1:])
    images = [s.image for s in slides[1:]]
    assert set(images) == {0, 1, 2} and images[:3] == [0, 1, 2]  # cycles in the chosen order
    assert [s.chapter for s in slides if s.start == 20.0] == ["המיטוכונדריה"]


def test_without_images_the_chapters_still_change():
    slides = plan_slides(30.0, image_count=0, rotate_seconds=5, chapters=CHAPTERS[:2])
    assert all(s.image is None for s in slides)
    assert [s.chapter for s in slides[1:]] == ["פתיחה", "המיטוכונדריה"]  # identical neighbours merged


def test_rotation_is_clamped():
    assert len(plan_slides(100.0, 2, rotate_seconds=0.1, chapters=[])) < 40


def test_slides_draw_hebrew_and_english(tmp_path):
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (800, 600), (120, 160, 200)).save(photo)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 255, 255, 0)).save(logo)
    style = SlideStyle(logo=logo)
    for slide, image in ((Slide(0, 3, None, intro=True), None), (Slide(3, 9, 0, "המיטוכונדריה: תחנת הכוח"), photo)):
        picture = draw_slide(slide, image, "מבנה התא", style)
        assert picture.size == (1920, 1080)
    english = draw_slide(Slide(3, 9, 0, "Why it memorizes"), photo, "Overfitting", SlideStyle(rtl=False))
    assert english.size == (1920, 1080)


def test_render_builds_one_png_per_distinct_slide_and_calls_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/bin/{name}")
    images = []
    for i, color in enumerate(((200, 0, 0), (0, 200, 0))):
        images.append(tmp_path / f"{i}.png")
        Image.new("RGB", (640, 360), color).save(images[-1])
    commands = []

    def run(command, **_):
        commands.append(command)
        Path(command[-1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(command, 0, "", "")

    out = render_video(tmp_path / "a.mp3", tmp_path / "out" / "video.mp4", images, CHAPTERS, "מבנה התא", SlideStyle(),
                       rotate_seconds=10, run=run, duration=60.0)
    assert out.read_bytes() == b"mp4"
    command = commands[0]
    assert command[command.index("-f") + 1] == "concat" and "-shortest" in command
    listing = (tmp_path / "out" / "slides" / "slides.ffconcat").read_text(encoding="utf-8")
    pngs = list((tmp_path / "out" / "slides").glob("slide_*.png"))
    assert listing.count("duration") >= len(pngs) >= 3  # reused slides are drawn once


def test_ffmpeg_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/bin/{name}")
    from src.video import VideoError

    def run(command, **_):
        return subprocess.CompletedProcess(command, 1, "", "Unknown encoder 'libx264'")

    with pytest.raises(VideoError, match="libx264"):
        render_video(tmp_path / "a.mp3", tmp_path / "v.mp4", [], [], "t", SlideStyle(), run=run, duration=10.0)


def test_ai_image_prompts_follow_the_plan_and_forbid_text():
    plan = {"title": "מבנה התא", "segments": [
        {"kind": "intro", "title": "פתיחה"},
        {"kind": "body", "title": "הגרעין", "goal": "מה הגרעין שומר", "concepts": ["DNA"]},
        {"kind": "body", "title": "המיטוכונדריה", "goal": "איך נוצרת אנרגיה", "concepts": ["ATP"]},
    ]}
    prompts = image_prompts(plan, "x", 3, "photo")
    assert len(prompts) == 3 and "הגרעין" in prompts[0] and "המיטוכונדריה" in prompts[1]
    assert "different moment" in prompts[2]  # more images than segments: a new angle, not a duplicate
    assert all("no text" in p.lower() for p in prompts) and "photograph" in prompts[0]


def test_one_failed_image_does_not_stop_the_rest():
    class Maker:
        def __init__(self):
            self.n = 0

        def make(self, prompt):
            self.n += 1
            if self.n == 1:
                raise ImageGenError("המודל לא החזיר תמונה")
            return b"png", ".png"

    saved = []
    assert generate_images(Maker(), ["a", "b", "c"], save=lambda d, s, p: saved.append(p)) == 2
    assert saved == ["b", "c"]

    class NoQuota:
        def make(self, prompt):
            raise ImageGenError("Gemini הגביל את קצב יצירת התמונות או שהמכסה נגמרה.")

    with pytest.raises(ImageGenError):
        generate_images(NoQuota(), ["a", "b"], save=lambda *a: None)
