"""The evaluation harness end to end with scripted models (no network)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.engine import EngineLLMs  # noqa: E402
from src.evaluation import DIMENSION_KEYS, SCENARIOS, EvalRunner, select_scenarios, weighted_overall  # noqa: E402
from src.evaluation.rubric import critic_user_prompt  # noqa: E402
from test_engine import FakeLLM  # noqa: E402

SAMPLE = (Path(__file__).resolve().parents[1] / "data" / "sample_transcript.txt").read_text(encoding="utf-8")


class Author(FakeLLM):
    def chat_json(self, task, system, user, max_tokens=2000, temperature=0.3):
        self.calls.append(task)
        if task == "eval_outline":
            return {"lecturer": "enthusiastic", "parts": ["part one"]}
        if task == "eval_part":
            return {"text": SAMPLE}
        raise AssertionError(task)


class Judge(FakeLLM):
    def __init__(self, faithfulness: int = 8) -> None:
        super().__init__("judge")
        self.faithfulness = faithfulness
        self.prompts = []

    def chat_json(self, task, system, user, max_tokens=2000, temperature=0.3):
        self.calls.append(task)
        self.prompts.append(user)
        if task == "critic":
            scores = {k: {"score": 7, "evidence": "x", "why": "y"} for k in DIMENSION_KEYS}
            scores["faithfulness"]["score"] = self.faithfulness
            scores["engagement"] = 5  # a bare number is accepted too
            return {
                "scores": scores, "verdict": "revise", "strengths": ["s"],
                "issues": [{"severity": "major", "dimension": "engagement", "quote": "שלום", "problem": "flat hook", "fix": "open with a question"}],
                "pipeline_recommendations": [{"stage": "plan", "change": "require a question hook", "expected_gain": "engagement"}],
            }
        if task == "eval_summary":
            return {"diagnosis": "Hooks are weak.", "improvements": [{"rank": 1, "stage": "plan", "change": "question hooks", "fixes": ["engagement"], "effort": "small"}]}
        raise AssertionError(task)


def make_runner(tmp_path, judge, author=None):
    settings = SimpleNamespace(podcast_format="auto", podcast_target_minutes=0, podcast_focus="",
                               verify_grounding=True, script_max_revisions=1, target_language="")
    return EvalRunner(
        settings,
        engine_llms_factory=lambda s, costs: EngineLLMs(cloud=FakeLLM("cloud")),
        judge=judge,
        author=author or Author("author"),
        out_dir=tmp_path / "eval",
        lessons_dir=tmp_path / "lessons",
        log=lambda _m: None,
    )


def test_twenty_distinct_scenarios_cover_every_format():
    assert len(SCENARIOS) == 20 and len({s["id"] for s in SCENARIOS}) == 20
    formats = {s["format"] for s in SCENARIOS}
    assert {"deep_dive", "beginner_explainer", "exam_prep", "story", "debate", "auto"} <= formats
    assert any(s["language"] == "en" for s in SCENARIOS) and any(s["focus"] for s in SCENARIOS)
    assert all(s["tests"] for s in SCENARIOS)
    assert [s["id"] for s in select_scenarios(["01", "07"])] == ["01-bio-intro", "07-econ-inflation"]


def test_overall_is_weighted_and_gated_by_faithfulness():
    good = {k: 8.0 for k in DIMENSION_KEYS}
    assert weighted_overall(good) == 8.0
    invented = dict(good, faithfulness=3.0)
    assert weighted_overall(invented) == 4.0  # lively but unfaithful cannot score well


def test_run_generates_sources_once_judges_and_compares_runs(tmp_path):
    judge, author = Judge(), Author("author")
    scenarios = select_scenarios(["01", "07"])
    first = make_runner(tmp_path, judge, author).run(scenarios)

    assert first["scored"] == 2 and first["previous_run"] is None
    assert first["averages"]["engagement"] == 5.0 and first["averages"]["faithfulness"] == 8.0
    assert author.calls.count("eval_outline") == 2  # one source per scenario
    report = Path(first["report_path"]).read_text(encoding="utf-8")
    assert "## Diagnosis" in report and "question hooks" in report and "07-econ-inflation" in report

    critic_prompt = next(p for p in judge.prompts if "תתמקד" in p or "ההשלכות המעשיות" in p)
    assert "listener_focus" in critic_prompt and "SOURCE" in critic_prompt and "SCRIPT" in critic_prompt

    # Same script, same critic: the saved review is reused instead of paid for again
    judge.calls.clear()
    again = make_runner(tmp_path, judge, author).run(scenarios)
    assert judge.calls == ["eval_summary"] and again["averages"] == first["averages"]

    # Second run: sources are reused, and the report is compared with the first run
    author.calls.clear()
    worse = Judge(faithfulness=3)
    worse.model = "fake-stricter-judge"
    second = make_runner(tmp_path, worse, author).run(scenarios)
    assert author.calls == []
    assert second["previous_run"] == again["run"]
    assert second["delta"]["faithfulness"] == -5.0
    assert second["overall"] == 4.0 and second["overall_delta"] < 0


def test_a_failing_scenario_does_not_lose_the_others(tmp_path):
    class BrokenAuthor(Author):
        def chat_json(self, task, *args, **kwargs):
            if task == "eval_outline" and len(self.calls) == 0:
                self.calls.append(task)
                raise RuntimeError("boom")
            return super().chat_json(task, *args, **kwargs)

    summary = make_runner(tmp_path, Judge(), BrokenAuthor("author")).run(select_scenarios(["01", "02"]))
    assert summary["scored"] == 1
    assert any(r.get("error") == "RuntimeError: boom" for r in summary["results"])


def test_a_dropped_connection_resumes_where_it_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr("src.evaluation.runner.WORDS_PER_PART", 400)  # several source parts

    class FlakyAuthor(Author):
        def __init__(self, name):
            super().__init__(name)
            self.fail_on_part = 2

        def chat_json(self, task, *args, **kwargs):
            if task == "eval_part" and self.calls.count("eval_part") + 1 == self.fail_on_part:
                self.fail_on_part = 0
                self.calls.append("dropped")
                raise ConnectionError("Server disconnected without sending a response.")
            return super().chat_json(task, *args, **kwargs)

    author = FlakyAuthor("author")
    scenarios = select_scenarios(["01"])
    assert make_runner(tmp_path, Judge(), author).run(scenarios)["scored"] == 0
    done_before = author.calls.count("eval_part")

    engine = FakeLLM("cloud")
    runner = make_runner(tmp_path, Judge(), author)
    runner.engine_llms_factory = lambda s, costs: EngineLLMs(cloud=engine)
    assert runner.run(scenarios)["scored"] == 1
    assert author.calls.count("eval_outline") == 1  # the outline and the finished part were kept
    parts = math.ceil(scenarios[0]["lecture_minutes"] * 120 / 400)
    assert author.calls.count("eval_part") == parts and done_before == 1

    # A third run reuses the engine's finished stages as well
    engine.calls.clear()
    runner.run(scenarios)
    assert engine.calls == []


def test_scenarios_run_in_parallel_and_report_progress(tmp_path):
    lines = []
    runner = make_runner(tmp_path, Judge())
    runner.log = lines.append
    summary = runner.run(select_scenarios(["01", "02", "03"]), workers=3)
    assert summary["scored"] == 3
    assert [r["id"] for r in summary["results"]] == [s["id"] for s in select_scenarios(["01", "02", "03"])]  # order kept
    progress = [line for line in lines if line.startswith("[3/3]")]
    assert progress and "ETA done" in progress[0]
    assert any(line.startswith("[1/3]") and "ETA" in line for line in lines)


def test_network_drops_are_retried():
    import httpx

    from src.engine.llm import _gemini_transient

    assert _gemini_transient(httpx.RemoteProtocolError("Server disconnected without sending a response."))
    assert _gemini_transient(OSError(11001, "getaddrinfo failed"))
    assert _gemini_transient(SimpleNamespace(code=503))
    assert not _gemini_transient(ValueError("bad json"))

    class Quota(Exception):
        code = 429

    # A per-minute limit passes in seconds; a used-up daily quota does not
    assert _gemini_transient(Quota("429 RESOURCE_EXHAUSTED: GenerateRequestsPerMinutePerProjectPerModel"))
    assert not _gemini_transient(Quota("429 RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier"))


def test_real_transcripts_replace_generated_ones(tmp_path):
    (tmp_path / "lessons").mkdir()
    (tmp_path / "lessons" / "01-bio-intro.txt").write_text(SAMPLE, encoding="utf-8")
    author = Author("author")
    make_runner(tmp_path, Judge(), author).run(select_scenarios(["01"]))
    assert author.calls == []
