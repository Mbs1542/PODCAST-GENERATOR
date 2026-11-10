"""Five guards that the episode's quality does not regress.

1. Hebrew titles with English and numbers are drawn in reading order, with no stray direction marks.
2. The rendered video is as long as the podcast, full HD, with sound (real ffmpeg, no fakes).
3. Joining several lessons keeps every word, in order, on one running clock; the length advice is the engine's.
4. The lesson summary carries everything the analysis found, in all three formats, escaped and right-to-left.
5. The whole chain (download to a chosen folder -> transcription next to the video -> podcast from two
   lessons -> summary) ends in one folder per podcast holding the lectures, transcripts, podcast and
   summary, and never writes outside the folders it should.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

import src  # noqa: E402,F401  (puts the bundled ffmpeg on PATH)
from src.engine.planner import target_minutes  # noqa: E402
from src.transcribe import TranscriptSegment  # noqa: E402
from src.utils.text_utils import shape_rtl  # noqa: E402
from src.web.chain import combine_transcripts, read_cues, recommend_length, transcript_stats  # noqa: E402
from src.web.media import MediaRunner  # noqa: E402
from src.web.server import create_app  # noqa: E402
from src.web.summary import build_summary, render_html, render_markdown, write_docx  # noqa: E402
from web_fakes import fake_runner_factory, write_fake_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BIDI_MARKS = set("‎‏‪‫‬‭‮⁦⁧⁨⁩")

PROFILE = {
    "title": "רגרסיה לינארית", "summary": "השיעור מציג רגרסיה לינארית ומדדי התאמה.", "domain": "מדע הנתונים",
    "level": "intro", "learning_objectives": ["להבין מה רגרסיה מנבאת", "לפרש את R²"],
    "concepts": [
        {"name": "רגרסיה לינארית", "definition": "קו ישר שמתאר משתנה תלוי.", "importance": 3},
        {"name": "MSE", "definition": "ממוצע ריבועי השגיאות.", "importance": 2},
        {"name": "<b>Overfit</b>", "definition": "<script>alert(1)</script> התאמת יתר.", "importance": 1},
    ],
    "examples": [{"description": "מחיר דירה לפי שטח", "illustrates": "רגרסיה פשוטה"}],
    "key_facts": [{"claim": "R² של 0.8 מסביר 80% מהשונות."}],
    "misconceptions": ["מתאם אינו סיבתיות."], "open_questions": ["מתי מודל לא לינארי עדיף?"],
    "arguments": [{"claim": "עוד משתנים תמיד עוזרים", "raised_by": "סטודנט", "support": "R² עולה", "objections": ["התאמת יתר"]}],
}


# ------------------------------------------------------------------ 1. titles

def test_mixed_hebrew_titles_are_drawn_in_reading_order():
    cases = {
        "פרק ניסיון – M.B.S Studio": "M.B.S Studio – ןויסינ קרפ",
        "פרק 3: מבוא": "אובמ :3 קרפ",
        "ניסיון עם GPT-4o ו-3 דוגמאות.": ".תואמגוד 3-ו GPT-4o םע ןויסינ",
        "שיעור 12 מתוך 20": "20 ךותמ 12 רועיש",
    }
    for logical, visual in cases.items():
        shaped = shape_rtl(logical)
        assert shaped == visual, logical
        assert not BIDI_MARKS & set(shaped), f"direction marks would be drawn as boxes: {logical}"


# ------------------------------------------------------------------ 2. video

@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_video_is_as_long_as_the_podcast_full_hd_with_sound(tmp_path):
    from PIL import Image

    from src.video import SlideStyle, render_video

    audio = tmp_path / "episode.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6.5",
                    "-codec:a", "libmp3lame", str(audio)], check=True)
    images = []
    for i, color in enumerate([(30, 80, 140), (140, 60, 50)]):
        images.append(tmp_path / f"{i}.png")
        Image.new("RGB", (1280, 720), color).save(images[-1])
    video = render_video(audio=audio, output=tmp_path / "episode.mp4", images=images,
                         chapters=[{"start": 0, "title": "פתיחה"}, {"start": 3, "title": "פרק 2: GPT-4o בכיתה"}],
                         episode_title="פרק ניסיון – M.B.S Studio", style=SlideStyle(), rotate_seconds=2,
                         work_dir=tmp_path / "slides")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                                       "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout)
    kinds = {s["codec_type"] for s in probe["streams"]}
    picture = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert kinds == {"video", "audio"}
    assert (picture["width"], picture["height"]) == (1920, 1080)
    assert abs(float(probe["format"]["duration"]) - 6.5) < 0.3


# ------------------------------------------------------------------ 3. joining lessons

def _srt(lines, step=3.0):
    return "\n".join(f"{i}\n00:00:{i * step - step:06.3f} --> 00:00:{i * step:06.3f}\n{text}\n".replace(".", ",")
                     for i, text in enumerate(lines, start=1))


def test_joining_lessons_keeps_every_word_in_order_on_one_clock():
    parts = [("שיעור 1", _srt(["שלום לכולם", "היום נלמד על התא"])),
             ("שיעור 2", _srt(["ממשיכים מהשיעור הקודם", "המיטוכונדריה מייצרת אנרגיה", "סיכום"])),
             ("Lesson 3", "plain text transcript without timing, still kept")]
    combined = combine_transcripts(parts)
    cues = read_cues(combined)
    texts = [t for _, _, t in cues]
    originals = [t for _, raw in parts[:2] for _, _, t in read_cues(raw)] + [parts[2][1]]
    content = [t for t in texts if not t.startswith("חלק ")]
    assert content == originals  # nothing lost, nothing reordered, nothing reworded
    assert [t for t in texts if t.startswith("חלק ")] == ["חלק 1 מתוך 3: שיעור 1.", "חלק 2 מתוך 3: שיעור 2.", "חלק 3 מתוך 3: Lesson 3."]
    starts = [s for s, _, _ in cues]
    assert starts == sorted(starts) and all(e > s for s, e, _ in cues)  # one running clock
    total = sum(transcript_stats(raw)["minutes"] for _, raw in parts)
    advice = recommend_length(total, len(parts))
    assert advice["recommended"] == target_minutes(total) and advice["files"] == 3
    assert recommend_length(200)["recommended"] == 30 and recommend_length(4)["recommended"] <= 4


# ------------------------------------------------------------------ 4. summary

def test_lesson_summary_is_complete_escaped_and_right_to_left(tmp_path):
    from docx import Document

    model = build_summary(PROFILE, title="שיעור 3", chapters=[{"title": "פתיחה", "start": 0}, {"title": "R²", "start": 95}],
                          lecture_minutes=90, podcast_minutes=25, sources=["שיעור 3"])
    page = render_html(model)
    markdown = render_markdown(model)
    docx_path = write_docx(model, tmp_path / "summary.docx")
    doc = Document(str(docx_path))
    word = "\n".join(p.text for p in doc.paragraphs)

    expected = ([c["definition"].split(">")[-1].strip() for c in PROFILE["concepts"]] + PROFILE["learning_objectives"]
                + [f["claim"] for f in PROFILE["key_facts"]] + PROFILE["misconceptions"] + PROFILE["open_questions"]
                + ["רגרסיה לינארית", "MSE", "מחיר דירה לפי שטח", "עוד משתנים תמיד עוזרים", "פתיחה"])
    for text in expected:
        assert text in page and text in markdown and text in word, text
    assert "<script>alert" not in page and "&lt;script&gt;" in page and "<b>Overfit" not in page
    assert '<html lang="he" dir="rtl">' in page and "1:35" in page
    assert doc.paragraphs[0]._p.pPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}bidi") is not None
    english = render_html(build_summary(PROFILE, title="x", language="en"))
    assert 'dir="ltr"' in english and "Key concepts" in english


# ------------------------------------------------------------------ 5. the whole chain

class _Transcriber:
    def __init__(self, language, log):
        pass

    def transcribe(self, media, progress=None):
        word = Path(media).stem
        return [TranscriptSegment(0.0, 4.0, f"שיעור {word} מתחיל."), TranscriptSegment(4.5, 9.0, f"סוף {word}.")]


def _download(request, progress=None, log=None, cancel=None):
    request.folder.mkdir(parents=True, exist_ok=True)
    path = request.folder / f"{request.url.rsplit('/', 1)[-1]}.mp4"
    path.write_bytes(b"video")
    return [path]


@pytest.fixture
def chain(tmp_path):
    saved_env = dict(os.environ)
    root = tmp_path / "app"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REPO / "docs" / "guide.he.html", root / "docs")
    shutil.copy(REPO / "LOGO.JPEG", root)
    write_fake_env(root, tmp_path / "outputs")
    app = create_app(root, runner_factory=fake_runner_factory(),
                     media_runner_factory=lambda store: MediaRunner(store, downloader=_download, transcriber_factory=_Transcriber))
    state = app.state.mbs
    state.allowed_hosts = {"testserver"}
    with TestClient(app) as client:
        client.headers["X-MBS-Token"] = state.token
        client.mbs = state
        yield client, tmp_path
    os.environ.clear()
    os.environ.update(saved_env)


def _wait(fetch, ready, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fetch()
        if ready(value):
            return value
        time.sleep(0.05)
    raise AssertionError("did not finish in time")


def test_whole_chain_keeps_files_together(chain):
    client, tmp = chain
    course = tmp / "My Course"
    created = client.post("/api/media/download", json={
        "urls": "https://example.com/l1\nhttps://example.com/l2", "folder": str(course)}).json()["items"]
    downloads = [_wait(lambda i=i: client.get(f"/api/media/{i['id']}").json(), lambda d: d["status"] == "done") for i in created]
    assert sorted(p.name for p in course.iterdir()) == ["l1.mp4", "l2.mp4"]
    assert all(d["folder"] == str(course.resolve()) and "path" not in d["files"][0] for d in downloads)
    assert not any((tmp / "outputs" / "media").rglob("*.mp4"))  # nothing copied into the app's folder

    for d in downloads:  # step 2, as confirmed in the dialog: the first file, in Hebrew
        assert client.post(f"/api/media/{d['id']}/transcribe", json={"language": "he", "files": [0]}).status_code == 200
    transcriptions = _wait(lambda: [i for i in client.get("/api/media").json()["items"] if i["kind"] == "transcription"],
                           lambda items: len(items) == 2 and all(i["status"] == "done" for i in items))
    assert {p.name for p in course.iterdir()} >= {"l1 - תמלול.srt", "l1 - תמלול.txt", "l2 - תמלול.srt", "l2 - תמלול.txt"}

    ids = [t["id"] for t in sorted(transcriptions, key=lambda t: t["title"])]
    estimate = client.get("/api/media/estimate", params={"items": ",".join(ids)}).json()
    assert estimate["files"] == 2 and estimate["recommended"] == target_minutes(estimate["lecture_minutes"])

    project = client.post("/api/media/podcast", json={"items": ids, "format": "brief", "title": "שני שיעורים"}).json()["project"]
    p = _wait(lambda: client.get(f"/api/projects/{project}").json(), lambda p: not p["running"])
    assert p["status"] == "plan_ready" and p["options"]["export_dir"] == str(course.resolve())
    transcript = client.get(f"/api/projects/{project}/files/transcript_srt").text
    assert transcript.index("שיעור l1 מתחיל") < transcript.index("שיעור l2 מתחיל")  # both lessons, in the chosen order

    for job in ("write", "audio"):
        assert client.post(f"/api/projects/{project}/jobs/{job}", json={}).status_code == 200
        p = _wait(lambda: client.get(f"/api/projects/{project}").json(), lambda p: not p["running"])
    assert p["status"] == "audio_ready" and "summary_html" in p["files"]
    output = course / "שני שיעורים"  # the podcast's own folder, next to its lessons
    exported = {f.name for f in output.iterdir()}
    assert {"l1.mp4", "l2.mp4", "l1 - תמלול.srt", "l1 - תמלול.txt", "l2 - תמלול.srt", "l2 - תמלול.txt",
            "שני שיעורים - סיכום.html", "שני שיעורים - סיכום.docx"} <= exported
    assert any(n.startswith("שני שיעורים - פודקאסט") for n in exported)
    assert (output / "l1.mp4").stat().st_ino == (course / "l1.mp4").stat().st_ino  # linked, not copied
    assert client.get(f"/api/projects/{project}").json()["options"]["output_folder"] == str(output)
    for fmt in ("html", "md", "docx"):
        assert client.get(f"/api/projects/{project}/summary", params={"format": fmt}).status_code == 200

    client.mbs.access_key = "a-network-password-1234"  # over the network nobody may pick folders on this disk
    client.cookies.clear()
    refused = client.post("/api/media/download", json={"urls": "https://example.com/l3", "folder": str(tmp / "x")})
    assert refused.status_code in (401, 403) and not (tmp / "x").exists()
