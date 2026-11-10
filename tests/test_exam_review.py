"""Spaced repetition (FSRS-5), practice without repeats, difficulty calibration and the evaluation set."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.engine.exam import ExamBuilder, ExamOptions, calibrate_mix  # noqa: E402
from src.engine.exam_eval import metrics, regressions, run_eval  # noqa: E402
from src.engine.exam_prompts import write_prompt  # noqa: E402
from src.engine.exam_rules import repeats_of  # noqa: E402
from src.engine.fsrs import AGAIN, EASY, GOOD, HARD, Card, Scheduler  # noqa: E402
from src.web.review import ReviewDeck, rating_from_score  # noqa: E402
from test_exam import FakeExamLLM, _lecture, _project, client  # noqa: E402,F401

NOW = datetime(2026, 11, 10, 9, 0, 0)


# ------------------------------------------------------------------ FSRS-5

def test_fsrs_first_review_follows_the_published_defaults():
    s = Scheduler()
    days = {r: (datetime.fromisoformat(s.review(Card(), r, NOW).due) - NOW) for r in (AGAIN, HARD, GOOD, EASY)}
    assert days[AGAIN] == timedelta(minutes=10)
    assert days[HARD].days == 1 and days[GOOD].days == 3 and days[EASY].days == 16  # w1, w2, w3 at 90% retention
    good = s.review(Card(), GOOD, NOW)
    assert good.state == "review" and 1 <= good.difficulty <= 10 and good.reps == 1


def test_fsrs_success_grows_the_interval_and_a_lapse_shrinks_it():
    s = Scheduler()
    card = s.review(Card(), GOOD, NOW)
    due = datetime.fromisoformat(card.due)
    assert abs(s.retrievability(card, due) - 0.9) < 0.01  # due exactly when recall falls to 90%
    second = s.review(card, GOOD, due)
    assert second.stability > card.stability * 2 and (datetime.fromisoformat(second.due) - due).days >= 8
    lapse = s.review(second, AGAIN, datetime.fromisoformat(second.due))
    assert lapse.lapses == 1 and lapse.state == "relearning" and lapse.stability < second.stability
    assert lapse.difficulty > second.difficulty  # forgetting makes the card harder
    with pytest.raises(ValueError):
        s.review(card, 5, NOW)


def test_rating_from_an_attempt_score():
    assert [rating_from_score(x) for x in (None, 0.0, 0.6, 0.8, 1.0)] == [None, AGAIN, HARD, GOOD, EASY]


def test_deck_schedules_practised_questions_and_limits_new_ones(tmp_path):
    deck = ReviewDeck(tmp_path)
    result = {"questions": [{"id": f"q{i}"} for i in range(1, 4)]}
    assert deck.add_exam("e1", result, {"q1": 0.0, "q2": 1.0}, now=NOW) == 3
    assert deck.add_exam("e1", result, now=NOW) == 0  # already there
    due_now = [c["id"] for c in deck.due(NOW)]
    assert due_now == ["e1:q3"]  # only the unanswered (new) one; the wrong one is due in 10 minutes
    later = [c["id"] for c in deck.due(NOW + timedelta(minutes=11))]
    assert later[0] == "e1:q1" and "e1:q2" not in later
    reviewed = deck.review("e1:q1", GOOD, NOW + timedelta(minutes=11))
    assert reviewed["state"] == "review" and reviewed["due"] > (NOW + timedelta(hours=1)).isoformat()
    stats = deck.stats(NOW + timedelta(minutes=12))
    assert stats["total"] == 3 and stats["new"] == 1 and stats["learned"] == 2 and stats["reviewed_today"] == 3

    many = {"questions": [{"id": f"n{i}"} for i in range(30)]}
    deck.add_exam("e2", many, now=NOW)
    assert len([c for c in deck.due(NOW) if c["state"] == "new"]) == 20  # new cards: at most 20 a day
    assert deck.remove_exam("e2") == 30 and deck.stats(NOW)["total"] == 3


# ------------------------------------------------------------------ calibration

def test_calibration_aims_at_70_to_85_percent_success():
    first = calibrate_mix([])
    assert first["in_band"] and abs(sum(first["mix"].values()) - 1) < 1e-9
    strong = calibrate_mix([{"easy": {"share": 1.0, "count": 5}, "medium": {"share": 1.0, "count": 5},
                             "hard": {"share": 0.9, "count": 5}}] * 3)
    weak = calibrate_mix([{"easy": {"share": 0.6, "count": 5}, "medium": {"share": 0.3, "count": 5},
                           "hard": {"share": 0.1, "count": 5}}] * 3)
    assert strong["mix"]["hard"] > first["mix"]["hard"]  # doing well: harder questions
    assert weak["mix"]["easy"] > first["mix"]["easy"]    # struggling: easier questions
    assert strong["in_band"] and 0.70 <= strong["expected"] <= 0.85


def test_adaptive_options_build_the_calibrated_mix(tmp_path):
    options = ExamOptions.from_dict({"difficulty": "adaptive", "mix": {"easy": 2, "medium": 5, "hard": 3}, "count": 10})
    assert options.mix == {"easy": 0.2, "medium": 0.5, "hard": 0.3}
    assert ExamOptions.from_dict({"difficulty": "adaptive"}).difficulty == "mixed"  # no mix given: the standard split
    exam = ExamBuilder(FakeExamLLM(), tmp_path / "e").run([_lecture("L1", ["רגרסיה", "מתאם", "שונות"], ["x"])], options)
    assert exam["stats"]["written"] == 10 and exam["stats"]["by_difficulty"]["hard"] >= 2


# ------------------------------------------------------------------ no repeats

def test_a_new_set_does_not_repeat_earlier_questions(tmp_path):
    lectures = [_lecture("L1", ["רגרסיה", "מתאם"], ["מתאם הוא סיבתיות"])]
    first = ExamBuilder(FakeExamLLM(), tmp_path / "a").run(lectures, ExamOptions.from_dict({"count": 4, "mcq_share": 1}))
    asked = [{"stem": q["stem"], "answer": next(o["text"] for o in q["options"] if o["correct"]), "topic": q["topic_name"]}
             for q in first["questions"]]
    # The same (deterministic) writer would write the very same questions again: every one is left out
    again = ExamBuilder(FakeExamLLM(), tmp_path / "b").run(lectures, ExamOptions.from_dict({"count": 4, "mcq_share": 1, "avoid": asked}))
    assert again["stats"]["repeats_left_out"] == len(first["questions"]) and not again["questions"]
    prompt = write_prompt({"name": "רגרסיה"}, [{"type": "mcq", "difficulty": "easy"}], "", "he", 4, "final", "",
                          avoid=[asked[0]["stem"]])
    assert "ALREADY ASKED" in prompt and asked[0]["stem"] in prompt
    assert repeats_of([{"stem": "מהו מתאם בין שני משתנים?"}], [{"stem": "מהי רגרסיה ליניארית פשוטה?"}]) == []


# ------------------------------------------------------------------ routes

def _wait(client, exam_id):
    deadline = time.time() + 20
    while time.time() < deadline:
        exam = client.get(f"/api/exams/{exam_id}").json()
        if not exam["running"]:
            return exam
        time.sleep(0.05)
    raise AssertionError("exam did not finish")


def test_practice_feeds_the_review_deck_and_the_next_set_is_calibrated(client):  # noqa: F811
    pid = _project(client.mbs.store, _lecture("L1", ["רגרסיה", "מתאם", "שונות"], ["מתאם הוא סיבתיות"]))
    created = client.post("/api/exams", json={"projects": [pid], "options": {"count": 4, "mcq_share": 1}}).json()
    exam = _wait(client, created["id"])
    questions = exam["result"]["questions"]
    answers = {q["id"]: (q["answer"] if i % 2 == 0 else "Z") for i, q in enumerate(questions)}
    attempt = client.post(f"/api/exams/{exam['id']}/attempts", json={"answers": answers}).json()
    assert attempt["review_added"] == len(questions) and attempt["review"]["total"] == len(questions)
    assert client.get("/api/review").json()["cards"] == []  # wrong ones return in 10 minutes, right ones in days

    card_id = f"{exam['id']}:{questions[0]['id']}"
    assert client.post(f"/api/review/{card_id}", json={"rating": 9}).status_code == 400
    assert client.post("/api/review/nope:q1", json={"rating": 3}).status_code == 404
    reviewed = client.post(f"/api/review/{card_id}", json={"rating": 3}).json()
    assert reviewed["card"]["reps"] == 2 and set(reviewed["preview"]) == {"1", "2", "3", "4"}

    practice = client.post(f"/api/exams/{exam['id']}/practice").json()
    assert practice["options"]["difficulty"] == "adaptive" and practice["options"]["calibration"]["answers"] == len(questions)
    assert len(practice["options"]["avoid"]) == len(questions)  # the earlier questions are passed on, to be avoided
    done = _wait(client, practice["id"])
    assert done["status"] == "done" and "repeats_left_out" in done["stats"] and done["stats"]["final"] > 0

    added = client.post(f"/api/exams/{done['id']}/review").json()
    assert added["added"] == done["stats"]["final"]
    queue = client.get("/api/review").json()
    assert queue["cards"] and queue["cards"][0]["question"]["stem"] and set(queue["cards"][0]["preview"]) == {"1", "2", "3", "4"}
    assert client.get("/api/exams").json()["review"]["due_now"] == len(queue["cards"])
    assert client.delete(f"/api/exams/{done['id']}").json() == {"ok": True}
    assert client.get("/api/review").json()["stats"]["total"] == len(questions)  # its cards left with it


# ------------------------------------------------------------------ evaluation set

def test_evaluation_set_measures_lessons_and_flags_regressions(tmp_path):
    lessons = [("p1", [_lecture("L1", ["רגרסיה", "מתאם", "שונות"], ["x"])]), ("p2", [_lecture("L1", ["התאמת יתר", "מדגם"], ["y"])])]
    run = run_eval(lessons, lambda: (FakeExamLLM(), lambda: 0.05), tmp_path, {"count": 6}, log=lambda m: None, app_version="t")
    assert len(run["lessons"]) == 2 and 0 < run["average"]["keep_rate"] <= 1 and run["average"]["grounded"] == 1
    assert run["average"]["cost_per_question"] > 0 and set(run["lessons"][0]) >= {"keep_rate", "first_pass", "length_cue"}
    worse = dict(run, date="2099-01-01T00:00:00", average={**run["average"], "keep_rate": run["average"]["keep_rate"] - 0.3})
    assert any(r.startswith("keep_rate") for r in regressions(worse, [run]))
    assert regressions(run, [run]) == [] and regressions(dict(run, fingerprint="other"), [run]) == []
    assert metrics({"stats": {"requested": 0}, "questions": []})["keep_rate"] == 0
