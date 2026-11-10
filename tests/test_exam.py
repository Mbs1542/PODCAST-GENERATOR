"""Exam mode: rules, blueprint, the whole engine with a fake model, and the app's routes."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.engine.exam import ExamBuilder, ExamOptions, Lecture  # noqa: E402
from src.engine.exam_rules import assign_positions, blocking, check_item, forms  # noqa: E402
from src.engine.llm import EngineLLMs, LLMClient  # noqa: E402
from src.web.projects import ProjectStore  # noqa: E402
from src.web.server import create_app  # noqa: E402
from web_fakes import fake_runner_factory, write_fake_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MARK = "נכונה"  # the fake writer puts this word in every correct answer, so the fake solver can find it
WORDS = ["שונות", "מודל", "תחזית", "שגיאה", "נתונים", "משתנה", "קשר", "מדגם", "ממוצע", "פיזור", "הסבר", "ניבוי",
         "השערה", "מבחן", "תוצאה", "גרף", "שיפוע", "חותך", "יחס", "סטייה"]


def _phrase(seed: int, size: int = 4) -> str:
    """Distinct wording per question, as real questions have (templated text would look like repeats)."""
    return " ".join(WORDS[(seed * 7 + i * 3) % len(WORDS)] for i in range(size))


def _lecture(label: str, concepts: List[str], misconceptions: List[str]) -> Lecture:
    sections = [{"id": f"S{i + 1}", "title": f"חלק {i + 1}", "start": 60.0 * i, "end": 60.0 * (i + 1),
                 "text": f"{name} הוא מושג מרכזי בשיעור. המרצה הסביר את {name} עם דוגמה מהחיים. " * 6}
                for i, name in enumerate(concepts)]
    profile = {"title": label, "concepts": [{"name": name, "definition": f"הגדרה של {name}", "importance": 3 - (i % 3),
                                             "section_ids": [f"S{i + 1}"], "depends_on": []} for i, name in enumerate(concepts)],
               "misconceptions": misconceptions, "arguments": []}
    return Lecture(label=label, title=f"שיעור {label}", sections=sections, profile=profile)


class FakeExamLLM(LLMClient):
    """Writes deterministic questions; the first one of the first call breaks a rule, to exercise repair."""

    def __init__(self) -> None:
        super().__init__(model="gemini-3.8-flash", name="cloud")
        self.calls: List[str] = []
        self.check_prompts: List[str] = []
        self.broke_one = False
        self.levels: Dict[str, str] = {}  # question id -> the Bloom level the fake checker reports
        self._level_queue: List[str] = []

    def chat_json(self, task: str, system: str, user: str, max_tokens: int = 2000, temperature: float = 0.3) -> Dict[str, Any]:
        self.calls.append(task)
        if task == "exam_topics":
            concepts = json.loads(user.split("CONCEPTS:\n", 1)[1])
            ids = [c["id"] for c in concepts]
            names = {c["id"]: c["name"] for c in concepts}
            topics: Dict[str, List[str]] = {}
            for concept_id in ids:
                topics.setdefault(names[concept_id], []).append(concept_id)
            return {"topics": [{"name": n, "definition": f"הגדרה של {n}", "members": m} for n, m in topics.items()]}
        if task in ("exam_write", "exam_repair"):
            topic = re.search(r"TOPIC: (.+)", user).group(1) if task == "exam_write" else "מושג"
            section = re.search(r"\[(L\d+:S\d+)\]", user).group(1)
            quote = re.search(r"\[L\d+:S\d+\][^\n]*\n(.{0,40})", user).group(1).split(".")[0]
            wanted = re.findall(r"^\d+\. type=(\w+), difficulty=(\w+)", user, re.M) or [("mcq", "medium")]
            questions = []
            for n, (kind, level) in enumerate(wanted):
                bloom = {"easy": "remember", "medium": "apply", "hard": "analyze"}[level]
                self._level_queue.append(bloom)
                opener = ["מהו", "איך מסבירים את", "מה תפקידו של", "מתי משתמשים ב", "מה מבדיל את"][(n + len(self.calls)) % 5]
                seed = len(self.calls) * 10 + n
                base = {"type": kind, "difficulty": level, "bloom": bloom, "stem": f"{opener} {topic} כש{_phrase(seed)}?",
                        "sources": [{"section": section, "quote": quote}], "explanation": f"כי {topic} מוגדר כך בשיעור"}
                if kind == "open":
                    base.update(model_answer=f"{topic} מוסבר בשיעור עם דוגמה.", rubric=[{"point": "הגדרה", "weight": 5},
                                                                                         {"point": "דוגמה", "weight": 5}])
                else:
                    distractors = [{"text": _phrase(seed * 3 + k * 11, 3), "error": "misconception", "why_wrong": "לא כך בשיעור"}
                                   for k in range(1, 4)]
                    if task == "exam_write" and not self.broke_one:
                        distractors[2] = {"text": "כל התשובות נכונות", "error": "-", "why_wrong": "-"}
                        self.broke_one = True
                    base.update(correct_answer=f"{MARK}: {_phrase(seed + 5, 3)}", distractors=distractors)
                questions.append(base)
            if task == "exam_repair":
                self._level_queue.pop()  # a rewrite keeps its question's id, not a new place in the queue
                return questions[0]
            return {"questions": questions}
        if task == "exam_check":
            self.check_prompts.append(user)
            shown = json.loads(re.search(r"QUESTIONS:\n(.*)\n\nSOURCES:", user, re.S).group(1))
            for q in shown:  # the engine numbers written questions w1, w2... in writing order
                number = int(q["id"][1:]) - 1 if q["id"].startswith("w") and q["id"][1:].isdigit() else -1
                if q["id"] not in self.levels and 0 <= number < len(self._level_queue):
                    self.levels[q["id"]] = self._level_queue[number]
            reviews = []
            for q in shown:
                if q["type"] == "mcq":
                    letter = next(k for k, text in q["options"].items() if MARK in text)
                    reviews.append({"id": q["id"], "defensible": [letter], "section": "L1:S1", "equivalent": [],
                                    "bloom": self.levels.get(q["id"], "apply"), "issues": []})
                else:
                    reviews.append({"id": q["id"], "answerable": True, "answer_supported": True,
                                    "bloom": self.levels.get(q["id"], "analyze"), "issues": []})
            return {"reviews": reviews}
        raise AssertionError(f"unexpected task {task}")


# ------------------------------------------------------------------ rules

def test_rules_catch_classic_item_writing_flaws_in_hebrew():
    good = {"type": "mcq", "stem": "מה מודד מקדם ההסבר במודל רגרסיה?", "correct_answer": "את חלק השונות שהמודל מסביר",
            "distractors": [{"text": "את ממוצע ריבועי השגיאות"}, {"text": "את הקשר הסיבתי בין המשתנים"}, {"text": "את שיפוע קו הרגרסיה"}],
            "sources": [{"section": "L1:S1", "quote": "מקדם ההסבר"}]}
    assert check_item(good, "he", {"L1:S1"}, {"L1:S1": "מקדם ההסבר מודד את חלק השונות"}) == []
    cases = {
        "R4": dict(good, distractors=[{"text": "כל התשובות נכונות"}, {"text": "ב"}, {"text": "ג"}]),
        "R5": dict(good, distractors=[{"text": "א ו-ב"}, {"text": "שגיאה"}, {"text": "שיפוע"}]),
        "R18": dict(good, correct_answer="נכון", distractors=[{"text": "לא נכון"}, {"text": "כן"}, {"text": "לא"}]),
        "R10": dict(good, stem="איזה מהבאים אינו מדד של התאמה?"),
        "R2": dict(good, sources=[{"section": "L7:S9", "quote": "x"}]),
        "R7": dict(good, correct_answer="את חלק השונות במשתנה התלוי שהמודל מסביר, בין אפס לאחד, כפי שהמרצה הראה בדוגמת הדירות"),
    }
    for rule, item in cases.items():
        assert rule in [f["rule"] for f in check_item(item, "he", {"L1:S1"})], rule
    assert forms("בשונות") == {"בשונות", "שונות"}  # ש here is part of the root: never "ונות"


def test_the_app_owns_answer_positions_and_balances_them():
    items = [{"type": "mcq", "correct_answer": f"נכון {i}", "distractors": [{"text": f"א{i}"}, {"text": f"ב{i}"}, {"text": f"ג{i}"}]}
             for i in range(40)]
    assign_positions(items, seed=3)
    counts = {k: sum(1 for i in items if i["answer"] == k) for k in "ABCD"}
    assert max(counts.values()) - min(counts.values()) <= 1
    keys = "".join(i["answer"] for i in items)
    assert not re.search(r"(.)\1\1\1", keys)  # never four in a row
    assert all(sum(o["correct"] for o in i["options"]) == 1 for i in items)


# ------------------------------------------------------------------ engine

def _build(tmp_path, **options):
    llm = FakeExamLLM()
    lectures = [_lecture("L1", ["רגרסיה", "מתאם", "שונות"], ["מתאם הוא סיבתיות"]),
                _lecture("L2", ["רגרסיה", "התאמת יתר"], ["עוד משתנים תמיד עדיף"])]
    builder = ExamBuilder(llm, tmp_path / "engine")
    exam = builder.run(lectures, ExamOptions.from_dict({"count": 8, "difficulty": "mixed", "mcq_share": 0.75, **options}))
    return llm, exam


def test_engine_writes_checks_repairs_and_assembles_a_grounded_exam(tmp_path):
    llm, exam = _build(tmp_path)
    questions = exam["questions"]
    stats = exam["stats"]
    assert len(questions) == 8 and stats["by_type"] == {"mcq": 6, "open": 2}
    assert stats["by_difficulty"] == {"easy": 2, "medium": 4, "hard": 2}
    assert stats["repaired"] == 1 and stats["dropped"] == 0  # the "all of the above" item was caught and rewritten
    assert "רגרסיה" in [t["name"] for t in exam["topics"]] and len([t for t in exam["topics"] if t["name"] == "רגרסיה"]) == 1
    assert next(t for t in exam["topics"] if t["name"] == "רגרסיה")["lectures"] == ["L1", "L2"]  # merged across lectures
    for q in questions:
        assert q["sources"] and all(s.get("time") is not None for s in q["sources"])
        if q["type"] == "mcq":
            assert sum(o["correct"] for o in q["options"]) == 1 and q["answer"] in "ABCD"
            assert all(o["feedback"] for o in q["options"] if not o["correct"])
            assert not any("כל התשובות" in o["text"] for o in q["options"])
        else:
            assert q["model_answer"] and len(q["rubric"]) == 2
    # The solver never sees which option is correct
    assert llm.check_prompts and not any("correct_answer" in p or "why_wrong" in p for p in llm.check_prompts)

    calls = len(llm.calls)
    again = ExamBuilder(llm, tmp_path / "engine").run(
        [_lecture("L1", ["רגרסיה", "מתאם", "שונות"], ["מתאם הוא סיבתיות"]), _lecture("L2", ["רגרסיה", "התאמת יתר"], ["עוד משתנים תמיד עדיף"])],
        ExamOptions.from_dict({"count": 8, "difficulty": "mixed", "mcq_share": 0.75}))
    assert len(llm.calls) == calls and again["stats"] == stats  # saved stages are reused: no new model calls


def test_blueprint_follows_focus_exclusion_and_importance(tmp_path):
    _, exam = _build(tmp_path, exclude=["שונות"], focus=["התאמת יתר"], count=6, difficulty="easy", mcq_share=1)
    topics = {q["topic_name"] for q in exam["questions"]}
    assert "שונות" not in topics and "התאמת יתר" in topics
    assert {q["difficulty"] for q in exam["questions"]} == {"easy"} and {q["type"] for q in exam["questions"]} == {"mcq"}


# ------------------------------------------------------------------ app

@pytest.fixture
def client(tmp_path):
    saved_env = dict(os.environ)
    root = tmp_path / "app"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REPO / "docs" / "guide.he.html", root / "docs")
    shutil.copy(REPO / "LOGO.JPEG", root)
    write_fake_env(root, tmp_path / "outputs")
    app = create_app(root, runner_factory=fake_runner_factory())
    state = app.state.mbs
    state.allowed_hosts = {"testserver"}
    fake = FakeExamLLM()
    state.exam_runner.llms_factory = lambda settings, costs: EngineLLMs(cloud=fake)
    with TestClient(app) as test_client:
        test_client.headers["X-MBS-Token"] = state.token
        test_client.mbs = state
        yield test_client
    os.environ.clear()
    os.environ.update(saved_env)


def _project(store: ProjectStore, lecture: Lecture) -> str:
    project = store.create(lecture.title, "transcript", "x.srt", {})
    engine = store.engine_dir(project)
    engine.mkdir(parents=True, exist_ok=True)
    (engine / "01_clean.json").write_text(json.dumps({"sections": lecture.sections}, ensure_ascii=False), encoding="utf-8")
    (engine / "02_profile.json").write_text(json.dumps(lecture.profile, ensure_ascii=False), encoding="utf-8")
    return project.id


def test_exam_routes_create_follow_export_and_delete(client):
    store = client.mbs.store
    ids = [_project(store, _lecture("L1", ["רגרסיה", "מתאם"], ["מתאם הוא סיבתיות"])),
           _project(store, _lecture("L2", ["התאמת יתר"], ["עוד משתנים תמיד עדיף"]))]
    bare = store.create("לא נותח", "transcript", "y.srt", {}).id
    listing = client.get("/api/exams").json()
    assert {p["id"]: p["ready"] for p in listing["projects"]}[bare] is False
    assert client.post("/api/exams", json={"projects": [bare]}).status_code == 409  # explains it needs analysis first

    estimate = client.get("/api/exams/estimate", params={"projects": ",".join(ids), "count": 10}).json()
    assert 0 <= estimate["low"] <= estimate["high"] and "ils" in estimate

    created = client.post("/api/exams", json={"projects": ids, "options": {"count": 5, "difficulty": "medium"}}).json()
    deadline = time.time() + 20
    while time.time() < deadline:
        exam = client.get(f"/api/exams/{created['id']}").json()
        if not exam["running"]:
            break
        time.sleep(0.05)
    assert exam["status"] == "done", exam.get("error")
    assert len(exam["result"]["questions"]) == 5 and exam["stats"]["final"] == 5

    sheet = client.get(f"/api/exams/{created['id']}/export", params={"format": "html"}).text
    key = client.get(f"/api/exams/{created['id']}/export", params={"format": "html", "answers": 1}).text
    assert 'dir="rtl"' in sheet and "תשובה:" not in sheet and MARK in sheet  # options shown, no answer key
    assert "תשובה:" in key and "בשיעור:" in key
    data = client.get(f"/api/exams/{created['id']}/export", params={"format": "json", "download": 1})
    assert data.status_code == 200 and json.loads(data.text)["questions"]
    assert client.delete(f"/api/exams/{created['id']}").json() == {"ok": True}


def test_a_sound_question_whose_level_disagrees_is_kept_and_relabelled(tmp_path):
    llm = FakeExamLLM()
    llm.levels = {"w1": "evaluate"}  # the checker insists the first (medium) question is really hard
    exam = ExamBuilder(llm, tmp_path / "e").run([_lecture("L1", ["רגרסיה"], ["x"])],
                                                ExamOptions.from_dict({"count": 2, "difficulty": "medium"}))
    assert exam["stats"]["final"] == 2 and exam["stats"]["dropped"] == 0
    relabelled = [q for q in exam["questions"] if q["difficulty"] == "hard"]
    assert relabelled and any(f["rule"] == "D1" and f["severity"] == "warn" for f in relabelled[0]["flags"])


def test_a_loosely_copied_quote_is_a_warning_not_a_reason_to_drop():
    text = {"L1:S1": "מוצר נתונים הוא יישום שמקבל קלט חדש ומפיק תחזית באופן אוטומטי בעזרת מודל."}
    item = {"type": "mcq", "stem": "מה מאפיין מוצר נתונים לפי השיעור?", "correct_answer": "יישום שמפיק תחזיות",
            "distractors": [{"text": "דוח סטטי למנהלים"}, {"text": "מאגר טבלאות לשאילתות"}, {"text": "שלב ניקוי נתונים"}]}
    close = dict(item, sources=[{"section": "L1:S1", "quote": "מוצר נתונים הוא יישום שמקבל קלט, ומפיק תחזית אוטומטית"}])
    invented = dict(item, sources=[{"section": "L1:S1", "quote": "המרצה אמר שהבנק המרכזי מעלה את הריבית בכל שנה"}])
    assert not blocking(check_item(close, "he", {"L1:S1"}, text))
    assert any(f["rule"] == "R2" for f in blocking(check_item(invented, "he", {"L1:S1"}, text)))
