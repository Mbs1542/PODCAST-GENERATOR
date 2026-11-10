"""HTTP routes for exam mode: create, follow, retry, delete, estimate and export exams."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from ..engine.exam import ExamOptions, calibrate_mix
from .exams import (Exam, ExamError, asked_before, attempt_history, combined_profile, list_attempts, render_exam_html,
                    save_attempt, score_attempt)

MAX_LECTURES = 12


def _view(exam: Exam, full: bool = False) -> Dict[str, Any]:
    data = {k: getattr(exam, k) for k in ("id", "title", "created", "projects", "options", "status", "stage",
                                          "progress", "stage_started", "error", "stats")}
    data["running"] = exam.status in ("queued", "running")
    if full:
        data["log"] = exam.log[-150:]
    return data


def register_exam_routes(app: FastAPI, state: Any) -> None:
    store, runner, deck = state.exam_store, state.exam_runner, state.review_deck

    def _get(exam_id: str) -> Exam:
        exam = store.get(exam_id)
        if exam is None:
            raise HTTPException(404, "המבחן לא נמצא")
        return exam

    def _ready(project) -> bool:
        engine = state.store.engine_dir(project)
        return (engine / "01_clean.json").exists() and (engine / "02_profile.json").exists()

    @app.get("/api/exams")
    def exams_list() -> Dict[str, Any]:
        """Exams, and the projects that can be used (analysed lessons), with their topics for focus choices."""
        projects = []
        for project in state.store.list():
            ready = _ready(project)
            profile = state.store.read_json(state.store.engine_dir(project) / "02_profile.json") if ready else {}
            projects.append({"id": project.id, "title": project.title, "ready": ready,
                             "concepts": [c.get("name", "") for c in (profile or {}).get("concepts", [])]})
        return {"exams": [_view(e) for e in store.list()], "projects": projects, "review": deck.stats()}

    @app.get("/api/exams/estimate")
    def exams_estimate(projects: str = "", count: int = 10) -> Dict[str, Any]:
        from ..utils.pricing import estimate_exam

        ids = [p for p in projects.split(",") if p][:MAX_LECTURES]
        lectures = runner.lectures(ids) if ids else []
        topics = sum(len(l.profile.get("concepts", [])) for l in lectures) or 1
        chars = sum(len(s.get("text", "")) for l in lectures for s in l.sections)
        env = state.settings_store.read_env()
        result = estimate_exam(max(1, min(60, count)), min(topics, max(1, count)), chars, len(lectures),
                               writer_model=env.get("GEMINI_TEXT_MODEL") or "gemini-3.8-flash",
                               writer=env.get("LLM_PROVIDER") or "gemini")
        ledger = state.credits.view()
        result["ils"] = {"low": round(result["low"] * ledger["usd_rate"], 2), "high": round(result["high"] * ledger["usd_rate"], 2),
                         "left": ledger["left"]}
        return result

    @app.post("/api/exams")
    async def exams_create(request: Request) -> Dict[str, Any]:
        body = await request.json()
        project_ids: List[str] = [str(p) for p in dict.fromkeys(body.get("projects") or [])][:MAX_LECTURES]
        if not project_ids:
            raise HTTPException(400, "בחר לפחות שיעור אחד")
        try:
            lectures = runner.lectures(project_ids)  # fails early, with the reason, when a lesson is not analysed
        except ExamError as exc:
            raise HTTPException(409, str(exc))
        raw = dict(body.get("options") or {})
        calibration = None
        if raw.get("difficulty") == "adaptive":  # levels aimed at 70-85% success, from earlier attempts on these lessons
            calibration = calibrate_mix(attempt_history(store, project_ids))
            raw["mix"] = calibration["mix"]
        raw["avoid"] = asked_before(store, project_ids) if raw.get("fresh", True) else []
        options = ExamOptions.from_dict(raw)
        default_title = lectures[0].title if len(lectures) == 1 else f"{lectures[0].title} ועוד {len(lectures) - 1}"
        title = " ".join(str(body.get("title") or f"מבחן: {default_title}").split())[:120]

        saved = asdict(options)
        if calibration:
            saved["calibration"] = calibration
        exam = store.create(title, project_ids, saved)
        return _view(runner.submit(exam.id), full=True)

    @app.get("/api/exams/{exam_id}")
    def exams_get(exam_id: str) -> Dict[str, Any]:
        exam = _get(exam_id)
        data = _view(exam, full=True)
        if exam.status == "done":
            data["result"] = store.result(exam_id)
        return data

    @app.post("/api/exams/{exam_id}/run")
    def exams_run(exam_id: str) -> Dict[str, Any]:
        exam = _get(exam_id)
        if exam.status in ("queued", "running"):
            raise HTTPException(409, "המבחן כבר בעבודה")
        return _view(runner.submit(exam.id), full=True)

    @app.delete("/api/exams/{exam_id}")
    def exams_delete(exam_id: str) -> Dict[str, Any]:
        if not store.delete(_get(exam_id).id):
            raise HTTPException(409, "אי אפשר למחוק מבחן בזמן שהוא נוצר")
        deck.remove_exam(exam_id)
        return {"ok": True}

    @app.post("/api/exams/{exam_id}/attempts")
    async def exams_attempt(exam_id: str, request: Request) -> Dict[str, Any]:
        """Score a finished attempt (multiple-choice exactly, open questions by the student's own 0-10 score)."""
        exam = _get(exam_id)
        result = store.result(exam_id)
        if result is None:
            raise HTTPException(409, "המבחן עוד לא מוכן")
        body = await request.json()
        attempt = score_attempt(result, body.get("answers") or {}, body.get("self_scores") or {})
        attempt["number"] = save_attempt(store, exam.id, attempt)
        attempt["review_added"] = deck.add_exam(exam.id, result, {r["id"]: r["score"] for r in attempt["questions"]})
        attempt["review"] = deck.stats()
        return attempt

    @app.get("/api/exams/{exam_id}/attempts")
    def exams_attempts(exam_id: str) -> Dict[str, Any]:
        return {"attempts": list_attempts(store, _get(exam_id).id)}

    @app.post("/api/exams/{exam_id}/practice")
    async def exams_practice(exam_id: str, request: Request) -> Dict[str, Any]:
        """A new set aimed at the weak topics of the last attempt, one level easier when the score was low."""
        exam = _get(exam_id)
        attempts = list_attempts(store, exam.id)
        if not attempts:
            raise HTTPException(409, "קודם צריך לפתור את המבחן")
        last = attempts[-1]
        weak = last.get("weak_topics") or []
        raw = {k: v for k, v in exam.options.items() if k not in ("calibration", "avoid", "mix")}
        raw.update(focus=weak or raw.get("focus", []), seed=int(raw.get("seed") or 0) + len(attempts) + 1,
                   count=max(5, min(15, 3 * len(weak) or raw.get("count", 10))))
        # Difficulty aimed at 70-85% expected success, from every attempt so far on these lessons
        calibration = calibrate_mix(attempt_history(store, exam.projects))
        raw.update(difficulty="adaptive", mix=calibration["mix"], avoid=asked_before(store, exam.projects))
        options = asdict(ExamOptions.from_dict(raw))
        options["calibration"] = calibration
        title = f"תרגול חוזר: {exam.title.replace('מבחן: ', '').replace('תרגול חוזר: ', '')}"[:120]
        practice = store.create(title, exam.projects, options)
        return _view(runner.submit(practice.id), full=True)

    # -------------------------------------------------------------- spaced repetition (FSRS-5)

    def _card_view(card: Dict[str, Any], results: Dict[str, Any]) -> Dict[str, Any]:
        exam_id = card["exam"]
        if exam_id not in results:
            results[exam_id] = store.result(exam_id)
        result = results[exam_id] or {}
        question = next((q for q in result.get("questions", []) if q["id"] == card["question"]), None)
        if question is None:
            return {}
        exam = store.get(exam_id)
        return {"id": card["id"], "state": card["state"], "reps": card["reps"], "lapses": card["lapses"],
                "due": card["due"], "exam": exam_id, "exam_title": exam.title if exam else "",
                "projects": exam.projects if exam else [], "language": (exam.options if exam else {}).get("language", "he"),
                "lecture_titles": result.get("lecture_titles", {}), "question": question,
                "preview": {str(k): v for k, v in deck.preview(card["id"]).items()}}

    @app.get("/api/review")
    def review_queue(limit: int = 50) -> Dict[str, Any]:
        """The cards due now, each with its question and when it would return after each rating."""
        results: Dict[str, Any] = {}
        cards, missing = [], []
        for card in deck.due(limit=max(1, min(200, limit))):
            view = _card_view(card, results)
            (cards.append(view) if view else missing.append(card["id"]))
        if missing:  # the exam or question is gone: the card goes too
            deck.remove_cards(missing)
        return {"cards": cards, "stats": deck.stats()}

    @app.get("/api/review/settings")
    def review_settings() -> Dict[str, Any]:
        from .review import DEFAULTS, LIMITS

        return {"settings": deck.settings(), "defaults": DEFAULTS, "limits": LIMITS}

    @app.put("/api/review/settings")
    async def review_settings_save(request: Request) -> Dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "הגדרות לא חוקיות")
        return {"settings": deck.save_settings(body)}

    @app.post("/api/review/{card_id}")
    async def review_answer(card_id: str, request: Request) -> Dict[str, Any]:
        body = await request.json()
        try:
            rating = int(body.get("rating"))
            card = deck.review(card_id, rating)
        except KeyError:
            raise HTTPException(404, "הכרטיס לא נמצא")
        except (TypeError, ValueError):
            raise HTTPException(400, "דירוג לא חוקי (1-4)")
        return {"card": card, "stats": deck.stats(), "preview": {str(k): v for k, v in deck.preview(card_id).items()}}

    @app.post("/api/exams/{exam_id}/review")
    def exams_add_to_review(exam_id: str) -> Dict[str, Any]:
        """Add all of an exam's questions to the review deck (as new cards, introduced up to 20 a day)."""
        exam = _get(exam_id)
        result = store.result(exam.id)
        if result is None:
            raise HTTPException(409, "המבחן עוד לא מוכן")
        return {"added": deck.add_exam(exam.id, result), "stats": deck.stats()}

    @app.get("/api/exams/{exam_id}/summary")
    def exams_summary(exam_id: str, format: str = "html", download: int = 0, print: int = 0) -> Response:
        """The study summary of the exam's lectures, merged into one page (from the lesson analyses)."""
        from ..video.slideshow import ACCENTS
        from .summary import FORMATS as SUMMARY_FORMATS, build_summary, render_html, render_markdown, write_docx

        exam = _get(exam_id)
        if format not in SUMMARY_FORMATS:
            raise HTTPException(400, "פורמט לא נתמך")
        try:
            lectures = runner.lectures(exam.projects)
        except ExamError as exc:
            raise HTTPException(409, str(exc))
        language = (exam.options or {}).get("language", "he")
        model = build_summary(combined_profile(lectures), title=exam.title, language=language,
                              lecture_minutes=None, sources=[l.title for l in lectures])
        accent = ACCENTS.get(state.brand.prefs().get("accent", "teal"), ACCENTS["teal"])
        media_type, suffix = SUMMARY_FORMATS[format]
        name = quote(f"{exam.title} - סיכום לימודי{suffix}")
        headers = {"Content-Disposition": f"{'attachment' if download else 'inline'}; filename*=UTF-8''{name}"}
        if format == "html":
            page = render_html(model, accent)
            if print:
                page = page.replace("</body>", "<script>addEventListener('load',()=>{document.querySelectorAll('details')"
                                               ".forEach(d=>d.open=true);setTimeout(()=>window.print(),300)})</script></body>")
            return Response(page, media_type=media_type, headers=headers)
        if format == "md":
            return Response(render_markdown(model), media_type=media_type, headers=headers)
        target = store.folder(exam.id) / "summary.docx"
        write_docx(model, target, accent)
        from fastapi.responses import FileResponse

        return FileResponse(target, media_type=media_type, headers=headers)

    @app.get("/api/exams/{exam_id}/export")
    def exams_export(exam_id: str, format: str = "html", answers: int = 0, download: int = 0, print: int = 0) -> Response:
        exam = _get(exam_id)
        result = store.result(exam_id)
        if result is None:
            raise HTTPException(409, "המבחן עוד לא מוכן")
        suffix = " - תשובות" if answers else ""
        if format == "json":
            import json

            name = quote(f"{exam.title}{suffix}.json")
            return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json",
                            headers={"Content-Disposition": f"{'attachment' if download else 'inline'}; filename*=UTF-8''{name}"})
        if format != "html":
            try:
                from .exam_export import export_exam
            except ImportError:
                raise HTTPException(400, "פורמט לא נתמך")
            try:
                content, media_type, extension = export_exam(result, format)
            except ValueError:
                raise HTTPException(400, "פורמט לא נתמך")
            name = quote(f"{exam.title}{extension}")
            return Response(content, media_type=media_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})
        name = quote(f"{exam.title}{suffix}.html")
        return HTMLResponse(render_exam_html(result, bool(answers), bool(print)),
                            headers={"Content-Disposition": f"{'attachment' if download else 'inline'}; filename*=UTF-8''{name}"})
