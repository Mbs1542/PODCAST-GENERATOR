"""Exam mode exports: Markdown, Word, Anki, Moodle XML, GIFT and QTI."""

from __future__ import annotations

import io
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web.exam_export import FORMATS, export_exam, gift_escape  # noqa: E402


def _src(lec="L1", time="34:12"):
    return [{"section": f"{lec}:S3", "quote": "ציטוט", "lecture": lec, "time": time, "start": 2052.0,
             "section_title": "Regression"}]


SAMPLE = {
    "title": "מבחן בסטטיסטיקה",
    "version": "1.0",
    "options": {"language": "he", "num_options": 4},
    "lecture_titles": {"L1": "רגרסיה לינארית", "L2": "Machine Learning"},
    "topics": [{"id": "T1", "name": "רגרסיה לינארית", "definition": "", "importance": "high", "lectures": ["L1"]}],
    "questions": [
        {"number": 1, "id": "q1", "type": "mcq", "difficulty": "easy", "bloom": "understand", "topic": "T1",
         "topic_name": "רגרסיה לינארית",
         "stem": "מה המשמעות של R² = 0.8 במודל {x}?",
         "options": [
             {"key": "A", "text": "80% מהשונות מוסברת", "correct": True, "feedback": ""},
             {"key": "B", "text": "המתאם הוא 0.8", "correct": False, "feedback": "המתאם הוא שורש R², לא R² עצמו"},
             {"key": "C", "text": "a:b יחס של 8:10", "correct": False, "feedback": "זה לא יחס #1"},
             {"key": "D", "text": "<b>שום דבר</b>", "correct": False, "feedback": "יש משמעות {ברורה}"},
         ],
         "answer": "A", "explanation": "R² הוא שיעור השונות המוסברת = 0.8", "sources": _src(), "flags": []},
        {"number": 2, "id": "q2", "type": "mcq", "difficulty": "hard", "bloom": "apply", "topic": "T1",
         "topic_name": "רגרסיה לינארית",
         "stem": "איזה Overfitting מתרחש כאשר #1 גדל?",
         "options": [
             {"key": "A", "text": "variance נמוך", "correct": False, "feedback": "ההפך"},
             {"key": "B", "text": "bias נמוך", "correct": False, "feedback": "לא קשור"},
             {"key": "C", "text": "variance גבוה", "correct": True, "feedback": ""},
             {"key": "D", "text": "אין שינוי", "correct": False, "feedback": "יש שינוי"},
         ],
         "answer": "C", "explanation": "מודל מורכב מדי -> variance גבוה", "sources": _src("L2", "12:05"), "flags": []},
        {"number": 3, "id": "q3", "type": "open", "difficulty": "medium", "bloom": "evaluate", "topic": "T1",
         "topic_name": "רגרסיה לינארית",
         "stem": "הסבירו את ההבדל בין <b>bias</b> ל-variance: {x}",
         "model_answer": "bias = טעות שיטתית; variance = רגישות לנתונים",
         "rubric": [{"point": "הגדרת bias", "weight": 2}, {"point": "הגדרת variance", "weight": 2}],
         "explanation": "", "sources": _src(), "flags": []},
    ],
    "stats": {},
}

STEMS = [q["stem"] for q in SAMPLE["questions"]]


def test_formats_table():
    assert set(FORMATS) == {"md", "docx", "anki", "moodle", "gift", "qti"}
    assert FORMATS["anki"][2] == "Anki (כרטיסיות)"
    assert FORMATS["qti"] == ("application/zip", ".zip", "QTI (Canvas)")


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_every_format_exports(fmt):
    data, media, ext = export_exam(SAMPLE, fmt)
    assert isinstance(data, bytes) and data
    assert (media, ext) == FORMATS[fmt][:2]


def test_unknown_format():
    with pytest.raises(ValueError):
        export_exam(SAMPLE, "pdfx")


def test_input_not_mutated():
    import copy
    before = copy.deepcopy(SAMPLE)
    for fmt in FORMATS:
        export_exam(SAMPLE, fmt)
    assert SAMPLE == before


def test_markdown():
    text = export_exam(SAMPLE, "md")[0].decode("utf-8")
    for stem in STEMS:
        assert stem in text
    key = text.split("מפתח תשובות", 1)[1]
    assert "תשובה נכונה:** א" in key  # q1 -> A -> א
    assert "תשובה נכונה:** ג" in key  # q2 -> C -> ג
    assert "המתאם הוא שורש R²" in key  # why-wrong feedback
    assert "רגרסיה לינארית" in key and "34:12" in key  # source lecture + time
    assert "bias = טעות שיטתית" in key and "הגדרת variance" in key


def test_markdown_english_letters():
    en = dict(SAMPLE, options={"language": "en"})
    text = export_exam(en, "md")[0].decode("utf-8")
    assert "Correct answer:** A" in text and "Correct answer:** C" in text


def test_docx_rtl():
    from docx import Document
    from docx.oxml.ns import qn

    data = export_exam(SAMPLE, "docx")[0]
    doc = Document(io.BytesIO(data))
    first = doc.paragraphs[0]
    ppr = first._p.pPr
    assert ppr is not None and ppr.find(qn("w:bidi")) is not None
    # schema order: bidi before jc
    tags = [child.tag for child in ppr]
    assert tags.index(qn("w:bidi")) < tags.index(qn("w:jc"))
    run_rpr = first.runs[0]._r.rPr
    assert run_rpr.find(qn("w:rtl")) is not None
    text = "\n".join(p.text for p in doc.paragraphs)
    for stem in STEMS:
        assert stem in text
    assert "א. 80% מהשונות מוסברת" in text
    assert "מפתח תשובות" in text
    assert 'w:type="page"' in doc.element.xml


def test_anki():
    text = export_exam(SAMPLE, "anki")[0].decode("utf-8")
    lines = text.rstrip("\n").split("\n")
    assert lines[:3] == ["#separator:tab", "#html:true", "#tags column:3"]
    notes = [ln for ln in lines if not ln.startswith("#")]
    assert len(notes) == 3
    for note in notes:
        assert len(note.split("\t")) == 3
    front, back, tags = notes[0].split("\t")
    assert front.startswith("<div dir='rtl'>") and "<li>" in front
    assert "&lt;b&gt;" in front  # option text escaped as HTML
    assert "R² הוא שיעור" in back
    assert tags.split() == ["רגרסיה_לינארית", "easy"]
    open_back = notes[2].split("\t")[1]
    assert "bias = טעות שיטתית" in open_back and "הגדרת bias" in open_back


def test_moodle():
    data = export_exam(SAMPLE, "moodle")[0]
    root = ET.fromstring(data)
    assert root.tag == "quiz"
    mc = root.findall("question[@type='multichoice']")
    essay = root.findall("question[@type='essay']")
    assert len(mc) == 2 and len(essay) == 1
    for q in mc:
        fr = [a.get("fraction") for a in q.findall("answer")]
        assert fr.count("100") == 1 and len(fr) == 4
        assert q.findtext("single") == "true"
        assert q.findtext("shuffleanswers") == "1"
        assert q.find("questiontext").get("format") == "html"
        assert q.findtext("questiontext/text").startswith('<div dir="rtl">')
        assert q.findtext("generalfeedback/text")
    q1 = mc[0]
    assert "R² = 0.8" in q1.findtext("questiontext/text")
    fbs = [a.findtext("feedback/text") for a in q1.findall("answer")]
    assert any("שורש R²" in f for f in fbs)
    assert "&lt;b&gt;" in q1.findall("answer")[3].findtext("text")
    assert "bias = טעות שיטתית" in essay[0].findtext("graderinfo/text")


def test_gift():
    text = export_exam(SAMPLE, "gift")[0].decode("utf-8")
    assert r"R² \= 0.8" in text
    assert r"\{x\}" in text
    assert r"\#1" in text
    assert r"a\:b" in text
    assert "// רגרסיה לינארית | easy" in text
    blocks = re.findall(r"^::Q(\d)::.*?(?=^::Q|\Z)", text, re.S | re.M)
    assert len(blocks) == 3
    mcq_bodies = re.findall(r"^::Q[12]::.*? \{\n(.*?)\n\}$", text, re.S | re.M)
    assert len(mcq_bodies) == 2
    for body in mcq_bodies:
        ans = [ln for ln in body.split("\n") if ln.startswith("\t=") or ln.startswith("\t~")]
        assert sum(1 for ln in ans if ln.startswith("\t=")) == 1
        assert sum(1 for ln in ans if ln.startswith("\t~")) == 3
    assert re.search(r"^::Q3::.* \{\}$", text, re.M)
    assert gift_escape("a\\b") == "a\\\\b"


def test_qti():
    data, media, ext = export_exam(SAMPLE, "qti")
    assert media == "application/zip" and ext == ".zip"
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert "imsmanifest.xml" in names
    others = [n for n in names if n != "imsmanifest.xml" and n.endswith(".xml")]
    assert len(others) == 1
    manifest = ET.fromstring(z.read("imsmanifest.xml"))
    ns = "{http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1}"
    hrefs = [f.get("href") for f in manifest.iter(f"{ns}file")]
    assert hrefs == others
    ans = ET.fromstring(z.read(others[0]))
    qns = "{http://www.imsglobal.org/xsd/ims_qtiasiv1p2}"
    items = list(ans.iter(f"{qns}item"))
    assert len(items) == 3
    types = [next(e.text for e in it.iter(f"{qns}fieldentry")) for it in items]
    assert types == ["multiple_choice_question", "multiple_choice_question", "essay_question"]
    for it, correct in zip(items[:2], (0, 2)):
        labels = [l.get("ident") for l in it.iter(f"{qns}response_label")]
        conds = [c for c in it.iter(f"{qns}respcondition")
                 if c.find(f"{qns}setvar") is not None and c.find(f"{qns}setvar").text == "100"]
        assert len(conds) == 1
        assert conds[0].find(f".//{qns}varequal").text == labels[correct]
