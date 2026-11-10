"""Exam mode exports: Markdown, Word, Anki, Moodle XML, GIFT and QTI 1.2 (Canvas).

Every exporter takes the finished exam (the app's result.json as a dict) and returns bytes.
Use :func:`export_exam` as the single entry point.
"""

from __future__ import annotations

import html
import io
import zipfile
from typing import Any, Callable, Dict, List, Tuple
from xml.sax.saxutils import escape as xml_escape, quoteattr

FORMATS: Dict[str, Tuple[str, str, str]] = {
    "md": ("text/markdown; charset=utf-8", ".md", "Markdown"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx", "Word"),
    "anki": ("text/plain; charset=utf-8", ".txt", "Anki (כרטיסיות)"),
    "moodle": ("application/xml; charset=utf-8", ".xml", "Moodle XML"),
    "gift": ("text/plain; charset=utf-8", ".gift.txt", "GIFT"),
    "qti": ("application/zip", ".zip", "QTI (Canvas)"),
}

HEB_LETTERS = "אבגדהוזחטי"
LATIN_LETTERS = "ABCDEFGHIJ"

LABELS = {
    "he": {
        "question": "שאלה", "answer_key": "מפתח תשובות", "correct": "תשובה נכונה", "explanation": "הסבר",
        "why_wrong": "למה המסיחים שגויים", "source": "מקור", "model_answer": "תשובה לדוגמה",
        "rubric": "מחוון", "open": "שאלה פתוחה", "points": "נק׳", "difficulty": "רמת קושי", "topic": "נושא",
    },
    "en": {
        "question": "Question", "answer_key": "Answer key", "correct": "Correct answer", "explanation": "Explanation",
        "why_wrong": "Why the other options are wrong", "source": "Source", "model_answer": "Model answer",
        "rubric": "Rubric", "open": "Open question", "points": "pts", "difficulty": "Difficulty", "topic": "Topic",
    },
}


# --------------------------------------------------------------------------- helpers

def _lang(result: Dict[str, Any]) -> str:
    lang = (result.get("options") or {}).get("language") or "he"
    return "en" if lang == "en" else "he"


def _rtl(result: Dict[str, Any]) -> bool:
    return _lang(result) == "he"


def _questions(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [dict(q, number=q.get("number") or i) for i, q in enumerate(result.get("questions") or [], 1)]


def _is_mcq(q: Dict[str, Any]) -> bool:
    return q.get("type", "mcq") != "open" and bool(q.get("options"))


def _letter(result: Dict[str, Any], index: int) -> str:
    letters = HEB_LETTERS if _rtl(result) else LATIN_LETTERS
    return letters[index] if index < len(letters) else str(index + 1)


def _correct_index(q: Dict[str, Any]) -> int:
    opts = q.get("options") or []
    for i, o in enumerate(opts):
        if o.get("correct"):
            return i
    ans = q.get("answer")
    for i, o in enumerate(opts):
        if ans and o.get("key") == ans:
            return i
    return 0


def _source_lines(result: Dict[str, Any], q: Dict[str, Any]) -> List[str]:
    titles = result.get("lecture_titles") or {}
    out = []
    for s in q.get("sources") or []:
        lec = s.get("lecture") or (s.get("section") or "").split(":")[0]
        title = titles.get(lec) or lec or ""
        parts = [p for p in (title, s.get("section_title"), s.get("time")) if p]
        if parts:
            out.append(" · ".join(parts))
    return out


def _topic(q: Dict[str, Any]) -> str:
    return q.get("topic_name") or q.get("topic") or ""


def _qid(q: Dict[str, Any]) -> str:
    return f"Q{q.get('number')}"


# --------------------------------------------------------------------------- Markdown

def to_markdown(result: Dict[str, Any]) -> bytes:
    t = LABELS[_lang(result)]
    qs = _questions(result)
    lines = [f"# {result.get('title') or ''}", ""]
    for q in qs:
        lines.append(f"## {t['question']} {q['number']}")
        lines.append("")
        lines.append(q.get("stem", ""))
        lines.append("")
        if _is_mcq(q):
            for i, o in enumerate(q["options"]):
                lines.append(f"{_letter(result, i)}. {o.get('text', '')}")
            lines.append("")
        else:
            lines.append(f"_{t['open']}_")
            lines.append("")
            lines.extend(["_____________________________________________"] * 4)
            lines.append("")
    lines += ["---", "", f"# {t['answer_key']}", ""]
    for q in qs:
        lines.append(f"## {t['question']} {q['number']}")
        lines.append("")
        if _is_mcq(q):
            ci = _correct_index(q)
            lines.append(f"**{t['correct']}:** {_letter(result, ci)} ({q['options'][ci].get('text', '')})")
            lines.append("")
            if q.get("explanation"):
                lines.append(f"**{t['explanation']}:** {q['explanation']}")
                lines.append("")
            wrong = [(i, o) for i, o in enumerate(q["options"]) if i != ci and o.get("feedback")]
            if wrong:
                lines.append(f"**{t['why_wrong']}:**")
                lines.append("")
                for i, o in wrong:
                    lines.append(f"- **{_letter(result, i)}:** {o['feedback']}")
                lines.append("")
        else:
            if q.get("model_answer"):
                lines.append(f"**{t['model_answer']}:** {q['model_answer']}")
                lines.append("")
            if q.get("rubric"):
                lines.append(f"**{t['rubric']}:**")
                lines.append("")
                for r in q["rubric"]:
                    lines.append(f"- {r.get('point', '')} ({r.get('weight', '')} {t['points']})")
                lines.append("")
            if q.get("explanation"):
                lines.append(f"**{t['explanation']}:** {q['explanation']}")
                lines.append("")
        for s in _source_lines(result, q):
            lines.append(f"_{t['source']}: {s}_")
            lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


# --------------------------------------------------------------------------- Word

def to_docx(result: Dict[str, Any]) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    t = LABELS[_lang(result)]
    rtl = _rtl(result)
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)

    def direction(paragraph) -> None:
        """Right-to-left paragraph and runs, each element where the schema puts it (Word is strict)."""
        if not rtl:
            return
        ppr = paragraph._p.get_or_add_pPr()
        if ppr.find(qn("w:bidi")) is None:
            ppr.insert_element_before(
                OxmlElement("w:bidi"), "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
                "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
                "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        for run in paragraph.runs:
            rpr = run._r.get_or_add_rPr()
            rpr.get_or_add_rFonts().set(qn("w:cs"), "Arial")
            if rpr.find(qn("w:rtl")) is None:
                rpr.insert_element_before(OxmlElement("w:rtl"), "w:cs", "w:em", "w:lang", "w:eastAsianLayout",
                                          "w:specVanish", "w:oMath")

    def para(text: str, bold_prefix: str = "", bold: bool = False):
        p = doc.add_paragraph()
        if bold_prefix:
            p.add_run(bold_prefix).bold = True
        r = p.add_run(text)
        r.bold = bold
        direction(p)
        return p

    def heading(text: str, level: int):
        h = doc.add_heading(text, level=level)
        direction(h)
        return h

    heading(result.get("title") or "", 0)
    qs = _questions(result)
    for q in qs:
        para(f"{q['number']}. {q.get('stem', '')}", bold=True)
        if _is_mcq(q):
            for i, o in enumerate(q["options"]):
                para(f"{_letter(result, i)}. {o.get('text', '')}")
        else:
            for _ in range(5):
                para("_" * 70)
        para("")

    brk = doc.add_paragraph()
    brk.add_run().add_break(WD_BREAK.PAGE)
    direction(brk)

    heading(t["answer_key"], 1)
    for q in qs:
        heading(f"{t['question']} {q['number']}", 2)
        if _is_mcq(q):
            ci = _correct_index(q)
            para(f"{_letter(result, ci)} ({q['options'][ci].get('text', '')})", bold_prefix=f"{t['correct']}: ")
            if q.get("explanation"):
                para(q["explanation"], bold_prefix=f"{t['explanation']}: ")
            wrong = [(i, o) for i, o in enumerate(q["options"]) if i != ci and o.get("feedback")]
            if wrong:
                para(t["why_wrong"], bold=True)
                for i, o in wrong:
                    para(o["feedback"], bold_prefix=f"{_letter(result, i)}: ")
        else:
            if q.get("model_answer"):
                para(q["model_answer"], bold_prefix=f"{t['model_answer']}: ")
            if q.get("rubric"):
                para(t["rubric"], bold=True)
                for r in q["rubric"]:
                    para(f"• {r.get('point', '')} ({r.get('weight', '')} {t['points']})")
            if q.get("explanation"):
                para(q["explanation"], bold_prefix=f"{t['explanation']}: ")
        for s in _source_lines(result, q):
            para(s, bold_prefix=f"{t['source']}: ")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- Anki

def _anki_field(text: str) -> str:
    """One Anki field: no tabs, newlines or double quotes (so no CSV quoting is needed)."""
    return text.replace("\t", " ").replace("\r", "").replace("\n", "<br>").replace('"', "&quot;")


def to_anki(result: Dict[str, Any]) -> bytes:
    t = LABELS[_lang(result)]
    rtl = _rtl(result)
    wrap_open = "<div dir='rtl'>" if rtl else "<div>"
    esc = lambda s: html.escape(str(s or ""), quote=True)  # noqa: E731
    lines = ["#separator:tab", "#html:true", "#tags column:3"]
    for q in _questions(result):
        front = f"{wrap_open}<p>{esc(q.get('stem'))}</p>"
        if _is_mcq(q):
            items = "".join(f"<li>{esc(_letter(result, i))}. {esc(o.get('text'))}</li>"
                            for i, o in enumerate(q["options"]))
            front += f"<ol style='list-style:none'>{items}</ol>"
        front += "</div>"
        back = wrap_open
        if _is_mcq(q):
            ci = _correct_index(q)
            back += (f"<p><b>{esc(t['correct'])}:</b> {esc(_letter(result, ci))}. "
                     f"{esc(q['options'][ci].get('text'))}</p>")
        else:
            if q.get("model_answer"):
                back += f"<p><b>{esc(t['model_answer'])}:</b> {esc(q['model_answer'])}</p>"
            if q.get("rubric"):
                items = "".join(f"<li>{esc(r.get('point'))} ({esc(r.get('weight'))})</li>" for r in q["rubric"])
                back += f"<p><b>{esc(t['rubric'])}:</b></p><ul>{items}</ul>"
        if q.get("explanation"):
            back += f"<p><b>{esc(t['explanation'])}:</b> {esc(q['explanation'])}</p>"
        srcs = _source_lines(result, q)
        if srcs:
            back += f"<p><small>{esc(t['source'])}: {esc('; '.join(srcs))}</small></p>"
        back += "</div>"
        tags = []
        if _topic(q):
            tags.append("_".join(_topic(q).split()))
        if q.get("difficulty"):
            tags.append(str(q["difficulty"]))
        lines.append("\t".join([_anki_field(front), _anki_field(back), _anki_field(" ".join(tags))]))
    return ("\n".join(lines) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- Moodle XML

def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _html_block(result: Dict[str, Any], text: str) -> str:
    body = html.escape(str(text or ""), quote=False).replace("\n", "<br>")
    return f'<div dir="rtl">{body}</div>' if _rtl(result) else f"<div>{body}</div>"


def to_moodle(result: Dict[str, Any]) -> bytes:
    t = LABELS[_lang(result)]
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<quiz>"]
    title = result.get("title") or "Exam"
    out += ["  <question type=\"category\">",
            f"    <category><text>{xml_escape('$course$/' + title)}</text></category>",
            "  </question>"]
    for q in _questions(result):
        name = f"{_qid(q)} {q.get('stem', '')}"[:80]
        qtype = "multichoice" if _is_mcq(q) else "essay"
        out.append(f'  <question type="{qtype}">')
        out.append(f"    <name><text>{xml_escape(name)}</text></name>")
        out.append(f'    <questiontext format="html"><text>{_cdata(_html_block(result, q.get("stem")))}</text></questiontext>')
        out.append(f'    <generalfeedback format="html"><text>{_cdata(_html_block(result, q.get("explanation")))}</text></generalfeedback>')
        out.append("    <defaultgrade>1</defaultgrade>")
        tags = [x for x in (_topic(q), q.get("difficulty")) if x]
        if qtype == "multichoice":
            ci = _correct_index(q)
            out.append("    <single>true</single>")
            out.append("    <shuffleanswers>1</shuffleanswers>")
            out.append("    <answernumbering>abc</answernumbering>")
            for i, o in enumerate(q["options"]):
                frac = "100" if i == ci else "0"
                out.append(f'    <answer fraction="{frac}" format="html">')
                out.append(f"      <text>{_cdata(_html_block(result, o.get('text')))}</text>")
                out.append(f'      <feedback format="html"><text>{_cdata(_html_block(result, o.get("feedback")))}</text></feedback>')
                out.append("    </answer>")
        else:
            info = str(q.get("model_answer") or "")
            if q.get("rubric"):
                info += "\n\n" + t["rubric"] + ":\n" + "\n".join(
                    f"- {r.get('point', '')} ({r.get('weight', '')})" for r in q["rubric"])
            out.append("    <responseformat>editor</responseformat>")
            out.append("    <responsefieldlines>15</responsefieldlines>")
            out.append(f'    <graderinfo format="html"><text>{_cdata(_html_block(result, info))}</text></graderinfo>')
            out.append('    <responsetemplate format="html"><text></text></responsetemplate>')
        if tags:
            out.append("    <tags>" + "".join(f"<tag><text>{xml_escape(str(x))}</text></tag>" for x in tags) + "</tags>")
        out.append("  </question>")
    out.append("</quiz>")
    return ("\n".join(out) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- GIFT

_GIFT_SPECIAL = "\\~=#{}:"


def gift_escape(text: Any) -> str:
    s = str(text or "")
    out = []
    for ch in s:
        if ch in _GIFT_SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out).replace("\r", "").replace("\n", "\\n")


def to_gift(result: Dict[str, Any]) -> bytes:
    lines = [f"// {gift_escape(result.get('title') or '')}", ""]
    for q in _questions(result):
        meta = " | ".join(x for x in (_topic(q), q.get("difficulty")) if x)
        lines.append(f"// {meta}" if meta else "//")
        head = f"::{_qid(q)}::{gift_escape(q.get('stem'))}"
        if _is_mcq(q):
            ci = _correct_index(q)
            answers = []
            for i, o in enumerate(q["options"]):
                mark = "=" if i == ci else "~"
                ans = f"\t{mark}{gift_escape(o.get('text'))}"
                if o.get("feedback"):
                    ans += f"#{gift_escape(o['feedback'])}"
                answers.append(ans)
            fb = f"\t####{gift_escape(q['explanation'])}" if q.get("explanation") else None
            lines.append(head + " {")
            lines.extend(answers)
            if fb:
                lines.append(fb)
            lines.append("}")
        else:
            lines.append(head + " {}")
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


# --------------------------------------------------------------------------- QTI 1.2

def _mattext(result: Dict[str, Any], text: str) -> str:
    return f'<material><mattext texttype="text/html">{xml_escape(_html_block(result, text))}</mattext></material>'


def to_qti(result: Dict[str, Any]) -> bytes:
    title = result.get("title") or "Exam"
    ident = "exam_" + "".join(c for c in str(result.get("id") or "1") if c.isalnum())
    items = []
    for q in _questions(result):
        qid = f"item_{q['number']}"
        if _is_mcq(q):
            ci = _correct_index(q)
            labels = "".join(
                f'<response_label ident="{qid}_a{i}">{_mattext(result, o.get("text"))}</response_label>'
                for i, o in enumerate(q["options"]))
            items.append(
                f'<item ident="{qid}" title={quoteattr(_qid(q))}>'
                "<itemmetadata><qtimetadata>"
                "<qtimetadatafield><fieldlabel>question_type</fieldlabel><fieldentry>multiple_choice_question</fieldentry></qtimetadatafield>"
                "<qtimetadatafield><fieldlabel>points_possible</fieldlabel><fieldentry>1</fieldentry></qtimetadatafield>"
                "</qtimetadata></itemmetadata>"
                f"<presentation>{_mattext(result, q.get('stem'))}"
                f'<response_lid ident="response1" rcardinality="Single"><render_choice>{labels}</render_choice></response_lid>'
                "</presentation>"
                '<resprocessing><outcomes><decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/></outcomes>'
                '<respcondition continue="No"><conditionvar>'
                f'<varequal respident="response1">{qid}_a{ci}</varequal>'
                '</conditionvar><setvar action="Set" varname="SCORE">100</setvar></respcondition>'
                "</resprocessing>"
                + (f'<itemfeedback ident="general_fb"><flow_mat>{_mattext(result, q["explanation"])}</flow_mat></itemfeedback>'
                   if q.get("explanation") else "")
                + "</item>")
        else:
            items.append(
                f'<item ident="{qid}" title={quoteattr(_qid(q))}>'
                "<itemmetadata><qtimetadata>"
                "<qtimetadatafield><fieldlabel>question_type</fieldlabel><fieldentry>essay_question</fieldentry></qtimetadatafield>"
                "<qtimetadatafield><fieldlabel>points_possible</fieldlabel><fieldentry>1</fieldentry></qtimetadatafield>"
                "</qtimetadata></itemmetadata>"
                f"<presentation>{_mattext(result, q.get('stem'))}"
                '<response_str ident="response1" rcardinality="Single"><render_fib><response_label ident="answer1" rshuffle="No"/></render_fib></response_str>'
                "</presentation>"
                '<resprocessing><outcomes><decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/></outcomes>'
                '<respcondition continue="No"><conditionvar><other/></conditionvar></respcondition></resprocessing>'
                + (f'<itemfeedback ident="general_fb"><flow_mat>{_mattext(result, q["model_answer"])}</flow_mat></itemfeedback>'
                   if q.get("model_answer") else "")
                + "</item>")
    assessment = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.imsglobal.org/xsd/ims_qtiasiv1p2 http://www.imsglobal.org/xsd/ims_qtiasiv1p2p1.xsd">'
        f'<assessment ident="{ident}" title={quoteattr(str(title))}>'
        '<qtimetadata><qtimetadatafield><fieldlabel>cc_maxattempts</fieldlabel><fieldentry>1</fieldentry></qtimetadatafield></qtimetadata>'
        f'<section ident="root_section">{"".join(items)}</section>'
        "</assessment></questestinterop>\n")
    href = f"{ident}/{ident}.xml"
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<manifest identifier="{ident}_manifest" xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<metadata><schema>IMS Content</schema><schemaversion>1.1.3</schemaversion></metadata>"
        "<organizations/>"
        f'<resources><resource identifier="{ident}" type="imsqti_xmlv1p2" href="{href}">'
        f'<file href="{href}"/></resource></resources>'
        "</manifest>\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("imsmanifest.xml", manifest.encode("utf-8"))
        z.writestr(href, assessment.encode("utf-8"))
    return buf.getvalue()


# --------------------------------------------------------------------------- entry point

_EXPORTERS: Dict[str, Callable[[Dict[str, Any]], bytes]] = {
    "md": to_markdown, "docx": to_docx, "anki": to_anki, "moodle": to_moodle, "gift": to_gift, "qti": to_qti,
}


def export_exam(result: Dict[str, Any], fmt: str) -> Tuple[bytes, str, str]:
    """Export a finished exam; returns (content, media type, file extension)."""
    if fmt not in _EXPORTERS:
        raise ValueError(f"unknown export format: {fmt!r}")
    media_type, ext, _label = FORMATS[fmt]
    return _EXPORTERS[fmt](result), media_type, ext
