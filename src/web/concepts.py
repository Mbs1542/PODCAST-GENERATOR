"""Key concepts: every concept of the chosen lessons with its explanation, as in the lesson summary.

A concept that several lessons share (same name) is shown once, with the most important rating it got and
each lesson's own definition when they differ. The examples a lesson gave for a concept, and the moments in
the lesson where it is discussed, come with it. Exports: Markdown, Word, and a printable page (PDF).
"""

from __future__ import annotations

import html
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.background import BackgroundTask

from ..engine.exam import Lecture
from .exams import ExamError, load_lectures

IMPORTANCE = {"he": {3: "ליבה", 2: "חשוב", 1: "רקע"}, "en": {3: "core", 2: "important", 1: "background"}}
FORMATS = {"md": ("text/markdown; charset=utf-8", ".md"), "html": ("text/html; charset=utf-8", ".html"),
           "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")}
MAX_LESSONS = 30


def _key(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


def _clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"


def build_glossary(lectures: Sequence[Lecture], project_ids: Sequence[str]) -> Dict[str, Any]:
    """The concepts of these lessons, merged by name, most important first (then in lesson order)."""
    merged: Dict[str, Dict[str, Any]] = {}
    order = 0
    for lecture, project_id in zip(lectures, project_ids):
        starts = {s.get("id"): s.get("start") for s in lecture.sections}
        examples: Dict[str, List[str]] = {}
        for example in lecture.profile.get("examples", []):
            if isinstance(example, dict) and example.get("description") and example.get("illustrates"):
                examples.setdefault(_key(example["illustrates"]), []).append(str(example["description"]))
        for concept in lecture.profile.get("concepts", []):
            name = str(concept.get("name") or "").strip()
            if not name:
                continue
            try:
                importance = max(1, min(3, int(concept.get("importance") or 2)))
            except (TypeError, ValueError):
                importance = 2
            entry = merged.get(_key(name))
            if entry is None:
                order += 1
                entry = merged[_key(name)] = {"name": name, "importance": importance, "order": order,
                                             "definitions": [], "lessons": [], "examples": [], "depends_on": []}
            entry["importance"] = max(entry["importance"], importance)
            definition = str(concept.get("definition") or "").strip()
            if definition and all(_key(d["text"]) != _key(definition) for d in entry["definitions"]):
                entry["definitions"].append({"text": definition, "lesson": lecture.title})
            times = [starts.get(s) for s in concept.get("section_ids", []) if starts.get(s) is not None]
            entry["lessons"].append({"project": project_id, "title": lecture.title,
                                     "moments": [{"start": t, "time": _clock(t)} for t in sorted(times)[:4]]})
            entry["examples"] += [{"text": e, "lesson": lecture.title} for e in examples.get(_key(name), [])]
            entry["depends_on"] += [d for d in concept.get("depends_on", []) if d and d not in entry["depends_on"]]
    concepts = sorted(merged.values(), key=lambda c: (-c["importance"], c["order"]))
    for concept in concepts:
        concept.pop("order", None)
    return {"lessons": [{"project": p, "title": l.title} for l, p in zip(lectures, project_ids)],
            "concepts": concepts,
            "counts": {level: sum(1 for c in concepts if c["importance"] == level) for level in (3, 2, 1)}}


# --------------------------------------------------------------------------- exports

def render_markdown(glossary: Dict[str, Any], title: str, language: str = "he") -> str:
    labels = IMPORTANCE["en" if language == "en" else "he"]
    lines = [f"# {title}", "", f"*{'Lessons' if language == 'en' else 'שיעורים'}: {' · '.join(l['title'] for l in glossary['lessons'])}*", ""]
    for c in glossary["concepts"]:
        definitions = c["definitions"] or [{"text": "", "lesson": ""}]
        first = definitions[0]["text"]
        lines.append(f"- **{c['name']}** ({labels[c['importance']]}): {first}".rstrip(": "))
        for other in definitions[1:]:
            lines.append(f"  - {other['lesson']}: {other['text']}")
        for example in c["examples"][:2]:
            lines.append(f"  - {'Example' if language == 'en' else 'דוגמה מהשיעור'}: {example['text']}")
    lines += ["", "---", "נוצר ב-M.B.S Studio מתוך ניתוח השיעורים." if language != "en" else "Made with M.B.S Studio from the lesson analyses."]
    return "\n".join(lines) + "\n"


def render_html(glossary: Dict[str, Any], title: str, language: str = "he", autoprint: bool = False) -> str:
    labels = IMPORTANCE["en" if language == "en" else "he"]
    e = html.escape
    rows = []
    for c in glossary["concepts"]:
        several = len(c["definitions"]) > 1
        definitions = "".join("<p>" + e(d["text"]) + (f" <small>({e(d['lesson'])})</small>" if several else "") + "</p>"
                              for d in c["definitions"])
        examples = "".join(f"<li>{e(x['text'])}</li>" for x in c["examples"][:2])
        rows.append(f"""<section class="concept imp{c['importance']}"><h2>{e(c['name'])} <span class="badge">{e(labels[c['importance']])}</span></h2>
          {definitions}{f'<ul class="ex">{examples}</ul>' if examples else ''}</section>""")
    direction = "ltr" if language == "en" else "rtl"
    script = "<script>addEventListener('load',()=>setTimeout(()=>window.print(),300))</script>" if autoprint else ""
    return f"""<!doctype html><html lang="{e(language)}" dir="{direction}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)}</title><style>
body{{font-family:-apple-system,"Segoe UI",Arial,sans-serif;max-width:820px;margin:32px auto;padding:0 16px;color:#1c1c1e;line-height:1.6}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#64646b;margin:0 0 24px}}
.concept{{border:1px solid #e3e3de;border-radius:10px;padding:12px 16px;margin:0 0 12px;break-inside:avoid}}
.concept h2{{font-size:18px;margin:0 0 6px}} .concept p{{margin:4px 0}} .concept small{{color:#64646b}}
.badge{{font-size:12px;font-weight:600;border-radius:999px;padding:2px 8px;background:#e3f2ef;color:#0b5f58;vertical-align:middle}}
.imp1 .badge{{background:#f1f1ed;color:#64646b}} .imp3{{border-inline-start:4px solid #0f766e}}
.ex{{margin:6px 0 0;color:#3a3a40;font-size:14px}} .ex li::marker{{content:"• "}}
@media print{{body{{margin:0}} .concept{{border-color:#ccc}}}}</style></head><body>
<h1>{e(title)}</h1><p class="sub">{e(' · '.join(l['title'] for l in glossary['lessons']))} · {len(glossary['concepts'])} {'concepts' if language == 'en' else 'מושגים'}</p>
{''.join(rows)}{script}</body></html>"""


def write_docx(glossary: Dict[str, Any], title: str, path: Path, language: str = "he") -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    labels = IMPORTANCE["en" if language == "en" else "he"]
    rtl = language != "en"
    document = Document()

    def paragraph(text: str = "", style: Optional[str] = None, bold_prefix: str = ""):
        p = document.add_paragraph(style=style)
        if bold_prefix:
            p.add_run(bold_prefix).bold = True
        p.add_run(text)
        if rtl:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            props = p._p.get_or_add_pPr()
            bidi = OxmlElement("w:bidi")
            bidi.set(qn("w:val"), "1")
            props.append(bidi)
            for run in p.runs:
                run_props = run._r.get_or_add_rPr()
                run_props.append(OxmlElement("w:rtl"))
        return p

    heading = document.add_heading(title, level=0)
    if rtl:
        heading.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    paragraph(" · ".join(l["title"] for l in glossary["lessons"]))
    for c in glossary["concepts"]:
        definitions = c["definitions"] or [{"text": "", "lesson": ""}]
        paragraph(definitions[0]["text"], bold_prefix=f"{c['name']} ({labels[c['importance']]}): ")
        for other in definitions[1:]:
            paragraph(other["text"], style="List Bullet", bold_prefix=f"{other['lesson']}: ")
        for example in c["examples"][:2]:
            paragraph(example["text"], style="List Bullet", bold_prefix="Example: " if not rtl else "דוגמה: ")
    document.save(path)
    return path


# --------------------------------------------------------------------------- routes

def register_concept_routes(app: FastAPI, state: Any) -> None:
    def _lessons(projects: str):
        ids = [p for p in dict.fromkeys(projects.split(",")) if p][:MAX_LESSONS]
        if not ids:
            raise HTTPException(400, "בחר לפחות שיעור אחד")
        try:
            return ids, load_lectures(state.store, ids)
        except ExamError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/concepts")
    def concepts(projects: str = "") -> Dict[str, Any]:
        ids, lectures = _lessons(projects)
        return build_glossary(lectures, ids)

    @app.get("/api/concepts/export")
    def concepts_export(projects: str = "", format: str = "md", language: str = "he", download: int = 1, print: int = 0) -> Response:
        if format not in FORMATS:
            raise HTTPException(400, "פורמט לא נתמך")
        ids, lectures = _lessons(projects)
        glossary = build_glossary(lectures, ids)
        language = "en" if language == "en" else "he"
        title = ("Key concepts" if language == "en" else "מושגי מפתח") + (f": {lectures[0].title}" if len(lectures) == 1 else f" ({len(lectures)} שיעורים)")
        media_type, suffix = FORMATS[format]
        headers = {"Content-Disposition": f"{'attachment' if download else 'inline'}; filename*=UTF-8''{quote(title + suffix)}"}
        if format == "md":
            return Response(render_markdown(glossary, title, language), media_type=media_type, headers=headers)
        if format == "html":
            return HTMLResponse(render_html(glossary, title, language, bool(print)), headers=headers)
        folder = Path(state.exam_store.root) / "_concepts"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{uuid.uuid4().hex}.docx"  # one file per request: two downloads never share it
        write_docx(glossary, title, target, language)
        return FileResponse(target, media_type=media_type, headers=headers,
                            background=BackgroundTask(lambda: target.unlink(missing_ok=True)))
