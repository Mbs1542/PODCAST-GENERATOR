"""Key concepts across lessons, spaced-repetition settings, and lesson lists saved as objects."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.engine.coerce import as_mapping, str_list  # noqa: E402
from src.engine.exam import Lecture  # noqa: E402
from src.web.concepts import build_glossary, render_html, render_markdown  # noqa: E402
from src.web.review import ReviewDeck  # noqa: E402
from src.web.summary import build_summary, render_markdown as summary_markdown  # noqa: E402
from test_exam import _project, client  # noqa: E402,F401


def _lesson(label: str, concepts, examples=(), title=None) -> Lecture:
    sections = [{"id": f"S{i}", "title": f"חלק {i}", "start": 60.0 * i, "end": 60.0 * (i + 1), "text": "טקסט"} for i in range(1, 5)]
    return Lecture(label=label, title=title or f"שיעור {label}", sections=sections, profile={
        "concepts": [{"name": n, "definition": d, "importance": imp, "section_ids": ids, "depends_on": []}
                     for n, d, imp, ids in concepts],
        "examples": [{"description": text, "illustrates": name} for text, name in examples]})


def test_glossary_merges_lessons_keeps_each_definition_and_orders_by_importance():
    one = _lesson("L1", [("Data Set", "טבלה של n שורות ו-p עמודות", 3, ["S2"]), ("EDA", "ניתוח גישוש", 2, ["S3", "S1"]),
                         ("Big Data", "נפח, מגוון ומהירות", 1, [])], [("טבלת מטופלים", "data set")])
    two = _lesson("L2", [("data  set", "אוסף נתונים מובנה", 2, ["S4"]), ("Machine Learning", "מודל שלומד מנתונים", 3, ["S1"])])
    glossary = build_glossary([one, two], ["p1", "p2"])
    names = [c["name"] for c in glossary["concepts"]]
    assert names == ["Data Set", "Machine Learning", "EDA", "Big Data"]  # core first, then lesson order
    data_set = glossary["concepts"][0]
    assert data_set["importance"] == 3 and [d["lesson"] for d in data_set["definitions"]] == ["שיעור L1", "שיעור L2"]
    assert [l["project"] for l in data_set["lessons"]] == ["p1", "p2"] and data_set["lessons"][0]["moments"][0]["time"] == "2:00"
    assert data_set["examples"] == [{"text": "טבלת מטופלים", "lesson": "שיעור L1"}]
    assert [m["time"] for m in glossary["concepts"][2]["lessons"][0]["moments"]] == ["1:00", "3:00"]  # in lesson order
    assert glossary["counts"] == {3: 2, 2: 1, 1: 1}

    md = render_markdown(glossary, "מושגי מפתח")
    assert "- **Data Set** (ליבה): טבלה של n שורות ו-p עמודות" in md and "  - שיעור L2: אוסף נתונים מובנה" in md
    assert "- **Big Data** (רקע): נפח, מגוון ומהירות" in md
    hostile = build_glossary([_lesson("L1", [("<script>alert(1)</script>", "x & y", 3, [])])], ["p"])
    page = render_html(hostile, "t")
    assert "<script>alert" not in page and "&lt;script&gt;" in page and 'dir="rtl"' in page  # model text is escaped


def test_concept_routes_list_and_export(client):  # noqa: F811
    store = client.mbs.store
    ids = [_project(store, _lesson("L1", [("רגרסיה", "קו שמתאר קשר", 3, ["S1"])], title="שיעור א")),
           _project(store, _lesson("L1", [("מתאם", "עוצמת הקשר", 2, ["S2"])], title="שיעור ב"))]
    data = client.get("/api/concepts", params={"projects": ",".join(ids)}).json()
    assert [c["name"] for c in data["concepts"]] == ["רגרסיה", "מתאם"] and len(data["lessons"]) == 2
    assert client.get("/api/concepts").status_code == 400
    bare = store.create("לא נותח", "transcript", "y.srt", {}).id
    assert client.get("/api/concepts", params={"projects": bare}).status_code == 409
    md = client.get("/api/concepts/export", params={"projects": ids[0], "format": "md"})
    assert md.status_code == 200 and "**רגרסיה** (ליבה): קו שמתאר קשר" in md.text and "attachment" in md.headers["content-disposition"]
    docx = client.get("/api/concepts/export", params={"projects": ",".join(ids), "format": "docx"})
    assert docx.status_code == 200 and docx.content[:2] == b"PK"  # a real Word file
    page = client.get("/api/concepts/export", params={"projects": ids[0], "format": "html", "print": 1, "download": 0})
    assert "window.print" in page.text and "inline" in page.headers["content-disposition"]
    assert client.get("/api/concepts/export", params={"projects": ids[0], "format": "exe"}).status_code == 400


def test_review_settings_are_saved_clamped_and_used(client, tmp_path):  # noqa: F811
    view = client.get("/api/review/settings").json()
    assert view["settings"] == view["defaults"] == {"retention": 0.9, "new_per_day": 20, "max_days": 365, "relearn_minutes": 10}
    saved = client.put("/api/review/settings", json={"retention": 0.99, "new_per_day": "5", "max_days": "x", "evil": 1}).json()["settings"]
    assert saved == {"retention": 0.95, "new_per_day": 5, "max_days": 365, "relearn_minutes": 10}  # clamped, invalid kept, unknown dropped
    assert client.get("/api/review/settings").json()["settings"] == saved

    deck = ReviewDeck(tmp_path)
    deck.save_settings({"retention": 0.8, "new_per_day": 2, "relearn_minutes": 30})
    assert deck.scheduler.retention == 0.8 and deck.scheduler.relearn.total_seconds() == 1800
    deck.add_exam("e", {"questions": [{"id": f"q{i}"} for i in range(5)]})
    assert len(deck.due()) == 2  # the new-per-day setting is applied


def test_lists_saved_as_objects_read_as_text_everywhere():
    saved = "{'misconception': 'מדעי הנתונים הם סטטיסטיקה', 'correction': 'הם מחפשים דפוסים בלי השערה מראש'}"
    assert as_mapping(saved) == {"misconception": "מדעי הנתונים הם סטטיסטיקה", "correction": "הם מחפשים דפוסים בלי השערה מראש"}
    assert as_mapping("{__import__('os').system('x')}") is None  # parsed as data only, never run
    assert str_list([{"question": "למה?"}, "רגיל", None]) == ["למה?", "רגיל"]
    profile = {"concepts": [{"name": "Data Science", "definition": "מדע", "importance": 3}], "misconceptions": [saved],
               "open_questions": ["{'question': 'איך מנתחים p גדול מ-n?'}"]}
    md = summary_markdown(build_summary(profile, title="t"))
    assert "{'" not in md and "- מדעי הנתונים הם סטטיסטיקה — בפועל: הם מחפשים דפוסים בלי השערה מראש" in md
    assert "- איך מנתחים p גדול מ-n?" in md
