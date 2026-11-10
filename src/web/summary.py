"""Lesson summary: the lesson analysis (engine stage 2) as a study page.

Everything comes from 02_profile.json, the analysis the script was written from, so the
summary costs no extra model call and says nothing the analysis did not find in the lecture.
The podcast's chapters and the lesson facts (length, sources) are added around it.

  build_summary   the content, as plain data
  render_html     a self-contained, designed page (light/dark, RTL, prints to PDF)
  render_markdown plain Markdown
  write_docx      a Word document with right-to-left paragraphs
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..engine.coerce import as_mapping

FORMATS = {"html": ("text/html; charset=utf-8", ".html"), "md": ("text/markdown; charset=utf-8", ".md"),
           "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")}

LABELS = {
    "he": {
        "kicker": "סיכום שיעור", "tldr": "בקצרה", "objectives": "מה לומדים בשיעור", "concepts": "מושגי מפתח",
        "facts": "עובדות וטענות חשובות", "examples": "דוגמאות מהשיעור", "illustrates": "ממחיש",
        "misconceptions": "טעויות נפוצות", "arguments": "טענות ודיון", "raised_by": "הועלה על ידי",
        "support": "נימוק", "objections": "התנגדויות", "chapters": "פרקי הפודקאסט", "selfcheck": "בדקו את עצמכם",
        "selfcheck_q": "מה המשמעות של המושג {name}?", "show": "הצג תשובה", "open": "שאלות פתוחות להמשך",
        "lecture": "אורך ההקלטה", "podcast": "אורך הפודקאסט", "minutes": "דק׳", "sources": "מקורות",
        "level": {"intro": "מבוא", "intermediate": "בינוני", "advanced": "מתקדם"},
        "importance": {3: "ליבה", 2: "חשוב", 1: "רקע"},
        "print": "הדפסה / שמירה כ-PDF", "footer": "נוצר ב-M.B.S Studio מתוך ניתוח השיעור. התוכן מבוסס על ההקלטה בלבד.",
        "domain": "תחום",
    },
    "en": {
        "kicker": "Lesson summary", "tldr": "In short", "objectives": "What the lesson teaches", "concepts": "Key concepts",
        "facts": "Key facts and claims", "examples": "Examples from the lesson", "illustrates": "Illustrates",
        "misconceptions": "Common mistakes", "arguments": "Claims and debate", "raised_by": "Raised by",
        "support": "Support", "objections": "Objections", "chapters": "Podcast chapters", "selfcheck": "Check yourself",
        "selfcheck_q": "What does {name} mean?", "show": "Show answer", "open": "Open questions",
        "lecture": "Recording", "podcast": "Podcast", "minutes": "min", "sources": "Sources",
        "level": {"intro": "Intro", "intermediate": "Intermediate", "advanced": "Advanced"},
        "importance": {3: "Core", 2: "Important", 1: "Background"},
        "print": "Print / save as PDF", "footer": "Made with M.B.S Studio from the lesson analysis. Based on the recording only.",
        "domain": "Field",
    },
}

SELF_CHECK_COUNT = 6


class SummaryError(RuntimeError):
    """A message written for the user."""


# --------------------------------------------------------------------------- content

def item_text(item: Any, *keys: str) -> str:
    """The analysis gives some lists as plain strings and some as objects ({"question": ...}): always text."""
    mapping = as_mapping(item)
    if mapping is not None:
        return " ".join(str(mapping.get(k) or "").strip() for k in keys if mapping.get(k)).strip()
    return str(item or "").strip()


def misconception_text(item: Any, language: str = "he") -> str:
    """A common mistake and its correction as one line ({"misconception", "correction"} or a plain string)."""
    if as_mapping(item) is None:
        return str(item or "").strip()
    wrong, right = item_text(item, "misconception"), item_text(item, "correction")
    if wrong and right:
        return f"{wrong} — {'In fact' if language == 'en' else 'בפועל'}: {right}"
    return wrong or right


def build_summary(
    profile: Dict[str, Any],
    *,
    title: str,
    language: str = "he",
    plan: Optional[Dict[str, Any]] = None,
    chapters: Sequence[Dict[str, Any]] = (),
    lecture_minutes: Optional[float] = None,
    podcast_minutes: Optional[float] = None,
    sources: Sequence[str] = (),
    created: Optional[str] = None,
) -> Dict[str, Any]:
    if not profile or not profile.get("concepts"):
        raise SummaryError("הסיכום יהיה מוכן אחרי שלב ניתוח השיעור.")
    language = "en" if language == "en" else "he"
    concepts = sorted(
        [c for c in profile.get("concepts", []) if c.get("name")],
        key=lambda c: -int(c.get("importance") or 2),
    )
    if not chapters and plan:  # before the audio exists, the plan's segment titles stand in
        chapters = [{"title": s.get("title", ""), "start": None} for s in plan.get("segments", []) if s.get("title")]
    return {
        "language": language,
        "dir": "ltr" if language == "en" else "rtl",
        "title": profile.get("title") or title,
        "project_title": title,
        "domain": profile.get("domain", ""),
        "level": profile.get("level", ""),
        "summary": profile.get("summary", ""),
        "objectives": [o for o in profile.get("learning_objectives", []) if o],
        "concepts": [{"name": c["name"], "definition": c.get("definition", ""), "importance": int(c.get("importance") or 2)}
                     for c in concepts],
        "facts": [f["claim"] for f in profile.get("key_facts", []) if f.get("claim")],
        "examples": [{"description": e["description"], "illustrates": e.get("illustrates", "")}
                     for e in profile.get("examples", []) if e.get("description")],
        "misconceptions": [m for m in (misconception_text(x, language) for x in profile.get("misconceptions", [])) if m],
        "arguments": [{"claim": a["claim"], "raised_by": a.get("raised_by", ""), "support": a.get("support", ""),
                       "objections": [o for o in a.get("objections", []) if o]}
                      for a in profile.get("arguments", []) if a.get("claim")],
        "open_questions": [q for q in (item_text(x, "question") for x in profile.get("open_questions", [])) if q],
        "self_check": [{"name": c["name"], "answer": c.get("definition", "")}
                       for c in concepts if c.get("definition")][:SELF_CHECK_COUNT],
        "chapters": [{"title": c.get("title", ""), "start": c.get("start")} for c in chapters if c.get("title")],
        "lecture_minutes": round(lecture_minutes) if lecture_minutes else None,
        "podcast_minutes": round(podcast_minutes) if podcast_minutes else None,
        "sources": [s for s in sources if s],
        "created": created or datetime.now().strftime("%d/%m/%Y"),
    }


def _clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = int(round(float(seconds)))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


# --------------------------------------------------------------------------- HTML

_CSS = """
:root{--accent:%(accent)s;--accent-soft:%(accent)s1f;--bg:#f6f7f9;--card:#fff;--text:#16181d;--muted:#5d6472;
--border:#e3e6eb;--warn:#b4541a;--warn-soft:#fff3e8;--shadow:0 1px 2px rgba(16,24,40,.05),0 4px 16px rgba(16,24,40,.06)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--text:#eceef2;--muted:#a3a9b6;--border:#2a2f3a;
--warn:#ffb27a;--warn-soft:#2b1f16;--shadow:none}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%%}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.7 "Heebo","Assistant","Segoe UI","Arial Hebrew",Arial,system-ui,sans-serif}
.wrap{max-width:920px;margin:0 auto;padding:24px 16px 56px}
.toolbar{display:flex;justify-content:flex-end;margin-bottom:12px}
.toolbar button{font:inherit;font-size:14px;border:1px solid var(--border);background:var(--card);color:var(--text);
border-radius:10px;padding:8px 14px;cursor:pointer}.toolbar button:hover{border-color:var(--accent)}
.hero{position:relative;overflow:hidden;border-radius:20px;padding:32px 28px;color:#fff;
background:linear-gradient(135deg,var(--accent),#1d2433 85%%);box-shadow:var(--shadow)}
.hero .kicker{font-size:13px;letter-spacing:.06em;opacity:.85;font-weight:600}
.hero h1{margin:6px 0 14px;font-size:clamp(26px,4.5vw,38px);line-height:1.25}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.22);
border-radius:999px;padding:4px 12px;font-size:13px}
section{margin-top:22px}.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px 22px;box-shadow:var(--shadow)}
h2{font-size:20px;margin:0 0 12px;display:flex;align-items:center;gap:10px}
h2::before{content:"";width:6px;height:22px;border-radius:3px;background:var(--accent)}
.tldr{font-size:18px;line-height:1.8;border-inline-start:4px solid var(--accent)}
ul.clean{list-style:none;margin:0;padding:0;display:grid;gap:10px}
ul.clean li{display:flex;gap:10px;align-items:flex-start}
.tick{flex:none;width:22px;height:22px;border-radius:7px;background:var(--accent-soft);color:var(--accent);
display:grid;place-items:center;font-size:13px;font-weight:700;margin-top:3px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.concept{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px;break-inside:avoid}
.concept b{display:block;font-size:17px;margin-bottom:4px}.concept p{margin:0;color:var(--muted)}
.tag{display:inline-block;font-size:12px;border-radius:999px;padding:1px 9px;margin-bottom:6px;border:1px solid var(--border);color:var(--muted)}
.tag.i3{background:var(--accent);border-color:var(--accent);color:#fff}.tag.i2{border-color:var(--accent);color:var(--accent)}
.warn li .tick{background:var(--warn-soft);color:var(--warn)}
.arg{border-top:1px solid var(--border);padding-top:12px;margin-top:12px}.arg:first-of-type{border-top:0;margin-top:0;padding-top:0}
.arg .meta{color:var(--muted);font-size:14px}
.chapters{list-style:none;margin:0;padding:0;counter-reset:ch}
.chapters li{display:flex;gap:12px;align-items:baseline;padding:8px 0;border-bottom:1px dashed var(--border)}
.chapters li:last-child{border-bottom:0}.chapters li::before{counter-increment:ch;content:counter(ch);flex:none;width:26px;height:26px;
border-radius:50%%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-size:13px;font-weight:700}
.chapters li>span:first-of-type{flex:1}.time{color:var(--muted);font-variant-numeric:tabular-nums;unicode-bidi:isolate}
details{border:1px solid var(--border);border-radius:12px;padding:10px 14px;background:var(--card)}
details+details{margin-top:8px}summary{cursor:pointer;font-weight:600}details p{margin:8px 0 0;color:var(--muted)}
.muted{color:var(--muted)}footer{margin-top:28px;text-align:center;font-size:13px;color:var(--muted)}
@media print{body{background:#fff;color:#000;font-size:12pt}.toolbar{display:none}.wrap{max-width:none;padding:0}
.hero{box-shadow:none;-webkit-print-color-adjust:exact;print-color-adjust:exact}.card,.concept,details{box-shadow:none;break-inside:avoid}
details{border-color:#ccc}details p{display:block}section{break-inside:auto}h2{break-after:avoid}}
"""


def render_html(model: Dict[str, Any], accent: Tuple[int, int, int] = (45, 190, 178)) -> str:
    t = LABELS[model["language"]]
    e = html.escape
    accent_hex = "#%02x%02x%02x" % tuple(int(c) for c in accent[:3])

    chips = []
    if model["domain"]:
        chips.append(f'{e(t["domain"])}: {e(model["domain"])}')
    if model["level"] in t["level"]:
        chips.append(e(t["level"][model["level"]]))
    if model["lecture_minutes"]:
        chips.append(f'{e(t["lecture"])}: {model["lecture_minutes"]} {e(t["minutes"])}')
    if model["podcast_minutes"]:
        chips.append(f'{e(t["podcast"])}: {model["podcast_minutes"]} {e(t["minutes"])}')
    chips.append(e(model["created"]))

    def section(key: str, body: str, card: bool = True, extra: str = "") -> str:
        inner = f'<div class="card {extra}">{body}</div>' if card else body
        return f'<section><h2>{e(t[key])}</h2>{inner}</section>'

    def ticks(items: List[str], mark: str = "✓", cls: str = "") -> str:
        rows = "".join(f'<li><span class="tick">{mark}</span><span>{e(i)}</span></li>' for i in items)
        return f'<ul class="clean {cls}">{rows}</ul>'

    parts = []
    if model["summary"]:
        parts.append(section("tldr", f'<div class="tldr">{e(model["summary"])}</div>'))
    if model["objectives"]:
        parts.append(section("objectives", ticks(model["objectives"])))
    if model["concepts"]:
        cards = "".join(
            f'<div class="concept"><span class="tag i{c["importance"]}">{e(t["importance"].get(c["importance"], ""))}</span>'
            f'<b>{e(c["name"])}</b><p>{e(c["definition"])}</p></div>' for c in model["concepts"])
        parts.append(section("concepts", f'<div class="grid">{cards}</div>', card=False))
    if model["facts"]:
        parts.append(section("facts", ticks(model["facts"], "•")))
    if model["examples"]:
        rows = [f'{ex["description"]}' + (f' — {t["illustrates"]}: {ex["illustrates"]}' if ex["illustrates"] else "")
                for ex in model["examples"]]
        parts.append(section("examples", ticks(rows, "◆")))
    if model["misconceptions"]:
        parts.append(section("misconceptions", ticks(model["misconceptions"], "!", "warn")))
    if model["arguments"]:
        blocks = "".join(
            f'<div class="arg"><b>{e(a["claim"])}</b>'
            + (f'<div class="meta">{e(t["raised_by"])}: {e(a["raised_by"])}</div>' if a["raised_by"] else "")
            + (f'<div>{e(t["support"])}: {e(a["support"])}</div>' if a["support"] else "")
            + (f'<div class="meta">{e(t["objections"])}: {e("; ".join(a["objections"]))}</div>' if a["objections"] else "")
            + "</div>" for a in model["arguments"])
        parts.append(section("arguments", blocks))
    if model["chapters"]:
        rows = "".join(f'<li><span>{e(c["title"])}</span><span class="time">{e(_clock(c["start"]))}</span></li>'
                       for c in model["chapters"])
        parts.append(section("chapters", f'<ol class="chapters">{rows}</ol>'))
    if model["self_check"]:
        rows = "".join(f'<details><summary>{e(t["selfcheck_q"].format(name=q["name"]))}</summary><p>{e(q["answer"])}</p></details>'
                       for q in model["self_check"])
        parts.append(section("selfcheck", rows, card=False))
    if model["open_questions"]:
        parts.append(section("open", ticks(model["open_questions"], "?")))
    if model["sources"]:
        parts.append(section("sources", ticks(model["sources"], "▸")))

    return f"""<!doctype html>
<html lang="{model['language']}" dir="{model['dir']}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(t['kicker'])}: {e(model['title'])}</title>
<style>{_CSS % {"accent": accent_hex}}</style></head>
<body><div class="wrap">
<div class="toolbar"><button type="button" onclick="document.querySelectorAll('details').forEach(d=>d.open=true);window.print()">{e(t['print'])}</button></div>
<header class="hero"><div class="kicker">{e(t['kicker'])}</div><h1>{e(model['title'])}</h1>
<div class="chips">{''.join(f'<span class="chip">{c}</span>' for c in chips)}</div></header>
{''.join(parts)}
<footer>{e(t['footer'])}</footer>
</div></body></html>
"""


# --------------------------------------------------------------------------- Markdown

def render_markdown(model: Dict[str, Any]) -> str:
    t = LABELS[model["language"]]
    lines = [f"# {model['title']}", "", f"*{t['kicker']} · {model['created']}*", ""]

    def block(key: str, items: List[str], bullet: str = "-") -> None:
        if items:
            lines.extend([f"## {t[key]}", "", *[f"{bullet} {i}" for i in items], ""])

    if model["summary"]:
        lines.extend([f"## {t['tldr']}", "", model["summary"], ""])
    block("objectives", model["objectives"], "- [ ]")
    if model["concepts"]:
        lines.extend([f"## {t['concepts']}", ""])
        lines.extend(f"- **{c['name']}** ({t['importance'].get(c['importance'], '')}): {c['definition']}" for c in model["concepts"])
        lines.append("")
    block("facts", model["facts"])
    block("examples", [ex["description"] + (f" ({t['illustrates']}: {ex['illustrates']})" if ex["illustrates"] else "")
                       for ex in model["examples"]])
    block("misconceptions", model["misconceptions"])
    if model["arguments"]:
        lines.extend([f"## {t['arguments']}", ""])
        for a in model["arguments"]:
            lines.append(f"- **{a['claim']}**")
            if a["raised_by"]:
                lines.append(f"  - {t['raised_by']}: {a['raised_by']}")
            if a["support"]:
                lines.append(f"  - {t['support']}: {a['support']}")
            if a["objections"]:
                lines.append(f"  - {t['objections']}: {'; '.join(a['objections'])}")
        lines.append("")
    if model["chapters"]:
        lines.extend([f"## {t['chapters']}", ""])
        lines.extend(f"{n}. {c['title']}" + (f" ({_clock(c['start'])})" if c["start"] is not None else "")
                     for n, c in enumerate(model["chapters"], start=1))
        lines.append("")
    if model["self_check"]:
        lines.extend([f"## {t['selfcheck']}", ""])
        for q in model["self_check"]:
            lines.extend([f"<details><summary>{t['selfcheck_q'].format(name=q['name'])}</summary>", "", q["answer"], "", "</details>", ""])
    block("open", model["open_questions"])
    block("sources", model["sources"])
    lines.append(f"---\n{t['footer']}")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- Word

def write_docx(model: Dict[str, Any], path: Path, accent: Tuple[int, int, int] = (45, 190, 178)) -> Path:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    t = LABELS[model["language"]]
    rtl = model["dir"] == "rtl"
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)

    def direction(paragraph) -> None:
        """Right-to-left paragraph and runs, with each element where the schema puts it (Word is strict)."""
        if not rtl:
            return
        ppr = paragraph._p.get_or_add_pPr()
        if ppr.find(qn("w:bidi")) is None:
            ppr.insert_element_before(
                OxmlElement("w:bidi"), "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
                "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
                "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
        for run in paragraph.runs:
            rpr = run._r.get_or_add_rPr()
            rpr.get_or_add_rFonts().set(qn("w:cs"), "Arial")
            if rpr.find(qn("w:rtl")) is None:
                rpr.insert_element_before(OxmlElement("w:rtl"), "w:cs", "w:em", "w:lang", "w:eastAsianLayout",
                                          "w:specVanish", "w:oMath")

    def para(text: str, bold_prefix: str = "", style_name: Optional[str] = None):
        p = doc.add_paragraph(style=style_name)
        if bold_prefix:
            p.add_run(bold_prefix).bold = True
        p.add_run(text)
        direction(p)
        return p

    def heading(text: str, level: int) -> None:
        h = doc.add_heading(text, level=level)
        if level <= 1:
            for run in h.runs:
                run.font.color.rgb = RGBColor(*accent[:3])
        direction(h)

    heading(model["title"], 0)
    para(f"{t['kicker']} · {model['created']}")
    if model["summary"]:
        heading(t["tldr"], 1)
        para(model["summary"])
    sections = [
        ("objectives", model["objectives"]), ("facts", model["facts"]),
        ("examples", [ex["description"] + (f" ({t['illustrates']}: {ex['illustrates']})" if ex["illustrates"] else "")
                      for ex in model["examples"]]),
        ("misconceptions", model["misconceptions"]),
    ]
    if model["concepts"]:
        heading(t["concepts"], 1)
        for c in model["concepts"]:
            para(f" {c['definition']}", f"{c['name']} ({t['importance'].get(c['importance'], '')}):", "List Bullet")
    for key, items in sections:
        if items:
            heading(t[key], 1)
            for item in items:
                para(item, style_name="List Bullet")
    if model["arguments"]:
        heading(t["arguments"], 1)
        for a in model["arguments"]:
            para(a["claim"], style_name="List Bullet").runs[0].bold = True
            for label, value in ((t["raised_by"], a["raised_by"]), (t["support"], a["support"]),
                                 (t["objections"], "; ".join(a["objections"]))):
                if value:
                    para(value, f"{label}: ")
    if model["chapters"]:
        heading(t["chapters"], 1)
        for c in model["chapters"]:
            para(c["title"] + (f"  ({_clock(c['start'])})" if c["start"] is not None else ""), style_name="List Number")
    if model["self_check"]:
        heading(t["selfcheck"], 1)
        for q in model["self_check"]:
            para(q["answer"], f"{t['selfcheck_q'].format(name=q['name'])} ")
    for key in ("open_questions", "sources"):
        if model[key]:
            heading(t["open" if key == "open_questions" else "sources"], 1)
            for item in model[key]:
                para(item, style_name="List Bullet")
    para(t["footer"])
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


# --------------------------------------------------------------------------- project glue

def audio_minutes(path: Optional[Path]) -> Optional[float]:
    if not path or not path.exists():
        return None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                              str(path)], capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out) / 60 if out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def project_summary(store: Any, project: Any) -> Dict[str, Any]:
    """Build the summary model for a project from the files its jobs left behind."""
    engine = store.engine_dir(project)
    profile = store.read_json(engine / "02_profile.json") or {}
    plan = store.read_json(engine / "03_plan.json") or {}
    clean = store.read_json(engine / "01_clean.json") or {}
    timing = store.read_json(store.folder(project) / "chapters.json") or {}
    paths = store.run_paths(project)
    dialogue = store.read_json(paths.dialogue_path) or {}
    meta = dialogue.get("metadata", {})
    lecture = (clean.get("duration_seconds") or 0) / 60 or None
    podcast = audio_minutes(paths.final_audio_path) or meta.get("estimated_duration_minutes")
    language = meta.get("language") or project.options.get("language") or "he"
    created = project.created[:10] if getattr(project, "created", "") else None
    if created and len(created) == 10 and created[4] == "-":
        created = f"{created[8:10]}/{created[5:7]}/{created[0:4]}"
    return build_summary(profile, title=project.title, language=language, plan=plan,
                         chapters=timing.get("chapters", []), lecture_minutes=lecture, podcast_minutes=podcast,
                         sources=project.options.get("sources") or [], created=created)


def dumps(model: Dict[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, indent=2)
