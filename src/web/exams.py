"""Exam mode in the app: saved exams, a background runner, and the printable export.

  <outputs>/exams/<id>/exam.json     settings, chosen projects, status, progress, log
  <outputs>/exams/<id>/result.json   the finished exam (questions, answer key, statistics)
  <outputs>/exams/<id>/engine/       the engine's saved stages (a stopped run resumes)
"""

from __future__ import annotations

import html
import json
import queue
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..engine.exam import ExamBuilder, ExamOptions, Lecture
from ..engine.llm import LLMError
from ..utils.costs import CostTracker
from ..utils.keep_awake import keep_awake
from ..utils.logging import get_logger

MAX_LOG_LINES = 300
_PART_RE = re.compile(r"Exam (E\d): (?:writing|checking|rewriting) (?:part )?(\d+)/(\d+)")
STAGE_LABELS = {"E1": "אוסף את החומר", "E2": "מאחד נושאים בין השיעורים", "E3": "בונה את מבנה המבחן",
                "E4": "כותב שאלות", "E5": "בודק את השאלות (פותר עיוור)", "E6": "משפר שאלות שנפסלו", "E7": "מרכיב את המבחן"}
HEBREW_KEYS = "אבגדה"


_MATH_RE = re.compile(r"(?<![\d$])\$(?!\s)([^$\n]{1,40}?)(?<!\s)\$(?![\d$])")
_TEX = {r"\cdot": "·", r"\times": "×", r"\leq": "≤", r"\geq": "≥", r"\le": "≤", r"\ge": "≥", r"\neq": "≠", r"\approx": "≈", "^2": "²", "^3": "³"}


def plain_math(value: Any) -> Any:
    """Models sometimes write LaTeX ($n$, $p > n$) although asked not to; show it as plain text everywhere."""
    if isinstance(value, str):
        def clean(m: "re.Match[str]") -> str:
            text = m.group(1)
            for tex, plain in _TEX.items():
                text = text.replace(tex, plain)
            return text.replace("\\", "").strip()
        return _MATH_RE.sub(clean, value) if "$" in value else value
    if isinstance(value, list):
        return [plain_math(v) for v in value]
    if isinstance(value, dict):
        return {k: plain_math(v) for k, v in value.items()}
    return value


class ExamError(RuntimeError):
    """A message written for the user."""


@dataclass
class Exam:
    id: str
    title: str
    created: str
    projects: List[str]
    options: Dict[str, Any]
    status: str = "queued"          # queued | running | done | error
    stage: str = ""
    progress: Optional[float] = None
    stage_started: float = 0.0
    error: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)
    log: List[str] = field(default_factory=list)


class ExamStore:
    def __init__(self, outputs_dir: Path) -> None:
        self.root = Path(outputs_dir) / "exams"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def folder(self, exam_id: str) -> Path:
        return self.root / exam_id

    def create(self, title: str, projects: List[str], options: Dict[str, Any]) -> Exam:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        exam = Exam(id=f"{stamp}-{uuid.uuid4().hex[:6]}", title=title[:120], created=datetime.now().isoformat(timespec="seconds"),
                    projects=projects, options=options)
        self.folder(exam.id).mkdir(parents=True)
        self.save(exam)
        return exam

    def get(self, exam_id: str) -> Optional[Exam]:
        if not re.fullmatch(r"[\w\-]+", exam_id or ""):
            return None
        try:
            return Exam(**json.loads((self.folder(exam_id) / "exam.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def list(self) -> List[Exam]:
        exams = [self.get(p.name) for p in self.root.iterdir() if (p / "exam.json").exists()]
        return sorted((e for e in exams if e), key=lambda e: e.created, reverse=True)

    def save(self, exam: Exam) -> None:
        with self._lock:
            exam.log = exam.log[-MAX_LOG_LINES:]
            path = self.folder(exam.id) / "exam.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(asdict(exam), ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)

    def update(self, exam_id: str, **changes: Any) -> Exam:
        with self._lock:
            exam = self.get(exam_id)
            if exam is None:
                raise KeyError(exam_id)
            if "stage" in changes and re.sub(r"\d+", "#", changes["stage"] or "") != re.sub(r"\d+", "#", exam.stage or ""):
                exam.stage_started = time.time() if changes["stage"] else 0.0
            for key, value in changes.items():
                setattr(exam, key, value)
            self.save(exam)
            return exam

    def append_log(self, exam_id: str, line: str) -> None:
        with self._lock:
            exam = self.get(exam_id)
            if exam:
                exam.log.append(f"{datetime.now():%H:%M:%S}  {line}")
                self.save(exam)

    def result(self, exam_id: str) -> Optional[Dict[str, Any]]:
        try:
            return plain_math(json.loads((self.folder(exam_id) / "result.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def delete(self, exam_id: str) -> bool:
        exam = self.get(exam_id)
        if exam is None or exam.status in ("queued", "running"):
            return False
        import shutil

        shutil.rmtree(self.folder(exam_id), ignore_errors=True)
        return True


class ExamRunner:
    """One exam at a time, in the background, keeping the computer awake; spend goes to the credit ledger."""

    def __init__(self, store: ExamStore, projects: Any, settings_store: Any,
                 llms_factory: Optional[Callable[[Any, CostTracker], Any]] = None) -> None:
        self.store = store
        self.projects = projects
        self.settings_store = settings_store
        self.llms_factory = llms_factory
        self.credits: Any = None
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._busy = threading.Event()
        self.logger = get_logger(self.__class__.__name__)
        threading.Thread(target=self._worker, name="mbs-exam", daemon=True).start()

    @property
    def busy(self) -> bool:
        return self._busy.is_set() or not self._queue.empty()

    def submit(self, exam_id: str) -> Exam:
        exam = self.store.update(exam_id, status="queued", error="", stage="ממתין בתור", progress=None)
        self._queue.put(exam_id)
        return exam

    def recover_interrupted(self) -> None:
        for exam in self.store.list():
            if exam.status in ("queued", "running"):
                self.store.update(exam.id, status="error", stage="", progress=None,
                                  error="העבודה הופסקה כשהאפליקציה נסגרה. אפשר להפעיל אותה שוב.")

    def lectures(self, project_ids: List[str]) -> List[Lecture]:
        lectures = []
        for number, project_id in enumerate(project_ids, start=1):
            project = self.projects.get(project_id)
            if project is None:
                raise ExamError("אחד הפרויקטים שנבחרו כבר לא קיים")
            engine = self.projects.engine_dir(project)
            clean = self.projects.read_json(engine / "01_clean.json")
            profile = self.projects.read_json(engine / "02_profile.json")
            if not clean or not profile:
                raise ExamError(f"\"{project.title}\" עוד לא עבר ניתוח שיעור. צריך להריץ קודם את שלב התוכנית בפרויקט.")
            lectures.append(Lecture(label=f"L{number}", title=project.title, sections=clean.get("sections", []), profile=profile))
        return lectures

    def _log(self, exam_id: str) -> Callable[[str], None]:
        def log(message: str) -> None:
            self.store.append_log(exam_id, message)
            match = _PART_RE.search(message)
            if match:
                stage, done, total = match.group(1), int(match.group(2)), int(match.group(3))
                self.store.update(exam_id, stage=f"{STAGE_LABELS[stage]} (חלק {done} מתוך {total})", progress=(done - 1) / total)
                return
            stage = re.match(r"Exam (E\d):", message)
            if stage:
                self.store.update(exam_id, stage=STAGE_LABELS[stage.group(1)], progress=None)
        return log

    def _worker(self) -> None:
        while True:
            exam_id = self._queue.get()
            self._busy.set()
            costs = CostTracker()
            try:
                with keep_awake():
                    self._run(exam_id, costs)
            except (ExamError, LLMError, ValueError) as exc:
                self.store.append_log(exam_id, f"שגיאה: {exc}")
                self.store.update(exam_id, status="error", stage="", progress=None, error=str(exc))
            except Exception as exc:  # never let one exam take the worker down
                self.logger.exception("Exam %s failed", exam_id)
                self.store.update(exam_id, status="error", stage="", progress=None, error=f"{exc.__class__.__name__}: {exc}")
            finally:
                self._record_spend(exam_id, costs)
                self._busy.clear()

    def _record_spend(self, exam_id: str, costs: CostTracker) -> None:
        if self.credits is None:
            return
        try:
            from .credits import google_spend_usd

            usd = google_spend_usd(costs)
            if usd:
                self.credits.record("google", usd, f"exam:{exam_id}", "מבחן")
                self.store.append_log(exam_id, f"עלות המבחן: ${usd:.3f}")
        except Exception:
            self.logger.exception("Could not record exam spend")

    def _run(self, exam_id: str, costs: CostTracker) -> None:
        exam = self.store.update(exam_id, status="running", stage="מתחיל", progress=None)
        lectures = self.lectures(exam.projects)
        settings, error = self.settings_store.load_settings()
        if settings is None and self.llms_factory is None:
            raise ExamError(error or "חסרות הגדרות לכותב (Gemini / Azure)")
        if self.llms_factory is not None:
            llms = self.llms_factory(settings, costs)
        else:
            from ..engine.llm import build_engine_llms

            llms = build_engine_llms(settings, costs)
        log = self._log(exam_id)
        for client in (llms.cloud, llms.local):
            if client is not None:
                client.on_retry = log
        builder = ExamBuilder(llms.cloud, self.store.folder(exam_id) / "engine", log=log)
        result = builder.run(lectures, ExamOptions.from_dict(exam.options))
        result["title"] = exam.title
        result["lecture_titles"] = {l.label: l.title for l in lectures}
        (self.store.folder(exam_id) / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.update(exam_id, status="done", stage="", progress=None, stats=result["stats"])


# --------------------------------------------------------------------------- attempts

PASS_SHARE = 0.7  # below this share a topic counts as weak and is practised again


def score_attempt(result: Dict[str, Any], answers: Dict[str, Any], self_scores: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Score one attempt: multiple-choice exactly; open questions by the student's own 0-10 score against the rubric.

    Returns per-question results and shares per topic and per difficulty; weak topics are those under PASS_SHARE.
    """
    self_scores = self_scores or {}
    rows, by_topic, by_level = [], {}, {}
    for q in result.get("questions", []):
        if q["type"] == "mcq":
            given = str(answers.get(q["id"]) or "").strip().upper()[:1]
            score = 1.0 if given == q.get("answer") else 0.0
            rows.append({"id": q["id"], "given": given, "correct": q.get("answer"), "score": score})
        else:
            raw = self_scores.get(q["id"])
            score = max(0.0, min(10.0, float(raw))) / 10 if raw not in (None, "") else None
            rows.append({"id": q["id"], "given": str(answers.get(q["id"]) or "")[:4000], "score": score})
        if rows[-1]["score"] is None:
            continue
        for bucket, key in ((by_topic, q.get("topic_name") or q.get("topic")), (by_level, q.get("difficulty"))):
            entry = bucket.setdefault(key, [0.0, 0])
            entry[0] += rows[-1]["score"]
            entry[1] += 1
    scored = [r["score"] for r in rows if r["score"] is not None]
    share = lambda pair: round(pair[0] / pair[1], 3) if pair[1] else None  # noqa: E731
    topics = {k: {"share": share(v), "count": v[1]} for k, v in by_topic.items()}
    return {
        "submitted": datetime.now().isoformat(timespec="seconds"),
        "score": round(sum(scored) / len(scored), 3) if scored else None,
        "answered": sum(1 for r in rows if r["given"]), "total": len(rows),
        "questions": rows, "topics": topics,
        "levels": {k: {"share": share(v), "count": v[1]} for k, v in by_level.items()},
        "weak_topics": [k for k, v in topics.items() if v["share"] is not None and v["share"] < PASS_SHARE],
    }


def save_attempt(store: "ExamStore", exam_id: str, attempt: Dict[str, Any]) -> int:
    folder = store.folder(exam_id) / "attempts"
    folder.mkdir(parents=True, exist_ok=True)
    number = len(list(folder.glob("*.json"))) + 1
    (folder / f"{number}.json").write_text(json.dumps(attempt, ensure_ascii=False, indent=2), encoding="utf-8")
    return number


def list_attempts(store: "ExamStore", exam_id: str) -> List[Dict[str, Any]]:
    folder = store.folder(exam_id) / "attempts"
    attempts = []
    for path in sorted(folder.glob("*.json"), key=lambda p: int(p.stem)) if folder.exists() else []:
        try:
            attempts.append({"number": int(path.stem), **json.loads(path.read_text(encoding="utf-8"))})
        except (OSError, ValueError):
            continue
    return attempts


def combined_profile(lectures: List[Lecture]) -> Dict[str, Any]:
    """One lesson profile for several lectures (the study summary), concepts merged by name, no model call."""
    def unique(values):
        return list(dict.fromkeys(v for v in values if v))

    concepts: Dict[str, Dict[str, Any]] = {}
    for lecture in lectures:
        for concept in lecture.profile.get("concepts", []):
            name = concept.get("name", "")
            key = " ".join(name.split()).lower()
            if key and (key not in concepts or int(concept.get("importance") or 2) > concepts[key]["importance"]):
                concepts[key] = {"name": name, "definition": concept.get("definition", ""),
                                 "importance": int(concept.get("importance") or 2)}
    profiles = [l.profile for l in lectures]
    return {
        "title": " · ".join(l.title for l in lectures) if len(lectures) <= 3 else f"{lectures[0].title} ועוד {len(lectures) - 1}",
        "summary": " ".join(p.get("summary", "") for p in profiles if p.get("summary")),
        "domain": unique(p.get("domain", "") for p in profiles)[0] if profiles else "",
        "level": profiles[0].get("level", "") if profiles else "",
        "learning_objectives": unique(o for p in profiles for o in p.get("learning_objectives", [])),
        "concepts": list(concepts.values()),
        "examples": [e for p in profiles for e in p.get("examples", []) if e.get("description")],
        "key_facts": [f for p in profiles for f in p.get("key_facts", []) if f.get("claim")],
        "misconceptions": unique(m for p in profiles for m in p.get("misconceptions", [])),
        "open_questions": unique(q for p in profiles for q in p.get("open_questions", [])),
        "arguments": [a for p in profiles for a in p.get("arguments", []) if a.get("claim")],
    }


# --------------------------------------------------------------------------- export

_EXAM_CSS = """
:root{--accent:#0f766e;--text:#16181d;--muted:#5d6472;--border:#dfe2e7;--bg:#fff;--soft:#f3f5f7}
@media (prefers-color-scheme:dark){:root{--text:#eceef2;--muted:#a3a9b6;--border:#2a2f3a;--bg:#14161b;--soft:#1c2028;--accent:#2dbeb2}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.65 "Heebo","Assistant","Arial Hebrew",Arial,system-ui,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px 48px}header{border-bottom:3px solid var(--accent);padding-bottom:12px;margin-bottom:18px}
h1{margin:0 0 4px;font-size:26px}.meta{color:var(--muted);font-size:14px}.toolbar{display:flex;justify-content:flex-end;gap:8px;margin-bottom:10px}
.toolbar button{font:inherit;font-size:14px;padding:6px 12px;border:1px solid var(--border);border-radius:8px;background:var(--soft);color:var(--text);cursor:pointer}
.q{border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin:12px 0;break-inside:avoid}
.q .head{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}.num{font-weight:700;color:var(--accent)}
.tag{font-size:12px;color:var(--muted);border:1px solid var(--border);border-radius:999px;padding:0 8px}
.stem{font-weight:600;margin:6px 0 8px}ol.opts{list-style:none;margin:0;padding:0;display:grid;gap:6px}
ol.opts li{display:flex;gap:8px}.letter{flex:none;width:24px;height:24px;border:1px solid var(--border);border-radius:50%;display:grid;place-items:center;font-size:13px}
.lines{border-bottom:1px solid var(--border);height:28px}.key{background:var(--soft);border-radius:10px;padding:10px 12px;margin-top:10px;font-size:14px}
.key b{color:var(--accent)}.why{color:var(--muted)}.src{font-size:13px;color:var(--muted)}h2{margin-top:28px}
@media print{.toolbar{display:none}body{font-size:11.5pt}.q{border-color:#bbb}}
"""


def render_exam_html(result: Dict[str, Any], with_answers: bool, autoprint: bool = False) -> str:
    """A printable exam sheet; with_answers adds the answer, the explanation and why each wrong option is wrong."""
    e = html.escape
    language = (result.get("options") or {}).get("language", "he")
    rtl = language != "en"
    letters = HEBREW_KEYS if rtl else "ABCDE"
    t = {"he": {"key": "תשובה", "why": "למה", "src": "בשיעור", "model": "תשובה לדוגמה", "rubric": "מחוון", "open": "שאלה פתוחה",
                "mcq": "אמריקאית", "easy": "קלה", "medium": "בינונית", "hard": "קשה", "questions": "שאלות", "print": "הדפסה / PDF",
                "answers": "עם תשובות", "sheet": "דף מבחן"},
         "en": {"key": "Answer", "why": "Why", "src": "In the lecture", "model": "Model answer", "rubric": "Rubric", "open": "Open",
                "mcq": "Multiple choice", "easy": "easy", "medium": "medium", "hard": "hard", "questions": "questions",
                "print": "Print / PDF", "answers": "with answers", "sheet": "Exam sheet"}}["he" if rtl else "en"]
    titles = result.get("lecture_titles") or {}
    blocks = []
    for q in result.get("questions", []):
        head = (f'<div class="head"><span class="num">{q["number"]}.</span><span class="tag">{e(t[q["type"]])}</span>'
                f'<span class="tag">{e(t[q["difficulty"]])}</span><span class="tag">{e(q.get("topic_name", ""))}</span></div>')
        body = f'<div class="stem">{e(q.get("stem", ""))}</div>'
        if q["type"] == "mcq":
            body += '<ol class="opts">' + "".join(
                f'<li><span class="letter">{letters["ABCDE".index(o["key"])]}</span><span>{e(o["text"])}</span></li>'
                for o in q.get("options", [])) + "</ol>"
        else:
            body += "".join('<div class="lines"></div>' for _ in range(4))
        if with_answers:
            sources = " · ".join(f'{e(titles.get(s.get("lecture"), s.get("lecture", "")))} {e(s.get("time", ""))}'
                                 for s in q.get("sources", []))
            if q["type"] == "mcq":
                answer = letters["ABCDE".index(q["answer"])]
                wrong = "".join(f'<div class="why">{letters["ABCDE".index(o["key"])]}: {e(o.get("feedback", ""))}</div>'
                                for o in q.get("options", []) if not o.get("correct") and o.get("feedback"))
                body += (f'<div class="key"><b>{e(t["key"])}: {answer}</b> · {e(q.get("explanation", ""))}{wrong}'
                         f'<div class="src">{e(t["src"])}: {sources}</div></div>')
            else:
                rubric = "".join(f"<li>{e(r.get('point', ''))} ({e(str(r.get('weight', '')))})</li>" for r in q.get("rubric") or []
                                 if isinstance(r, dict))
                body += (f'<div class="key"><b>{e(t["model"])}:</b> {e(q.get("model_answer", ""))}'
                         f'<div><b>{e(t["rubric"])}:</b><ul>{rubric}</ul></div><div class="src">{e(t["src"])}: {sources}</div></div>')
        blocks.append(f'<section class="q">{head}{body}</section>')
    count = len(result.get("questions", []))
    script = "<script>addEventListener('load',()=>setTimeout(()=>print(),300))</script>" if autoprint else ""
    return f"""<!doctype html><html lang="{e(language)}" dir="{'rtl' if rtl else 'ltr'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(result.get('title', 'Exam'))}</title><style>{_EXAM_CSS}</style></head>
<body><div class="wrap"><div class="toolbar"><button onclick="print()">{e(t['print'])}</button></div>
<header><h1>{e(result.get('title', ''))}</h1><div class="meta">{e(t['answers'] if with_answers else t['sheet'])} · {count} {e(t['questions'])}</div></header>
{''.join(blocks)}</div>{script}</body></html>"""
