"""Script engine v2, end to end, with a scripted fake model (no network)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.engine import EngineLLMs, LLMClient, ScriptEngine
from src.engine.llm import LLMError, parse_json_reply
from src.engine.planner import normalize_plan, target_minutes
from src.engine.profile import normalize_profile
from src.engine.transcript import CleanTranscript, Section, TranscriptCleaner, build_paragraphs, clean_text
from src.engine.verifier import ScriptVerifier, concept_mentioned, normalize_text, _token_set

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_transcript.txt"

PROFILE = {
    "title": "רשתות ב-Azure",
    "summary": "השיעור מציג את אבני הבניין של רשת בענן.",
    "domain": "computer networking",
    "level": "intro",
    "lesson_type": "lecture",
    "audience": "סטודנטים לענן",
    "learning_objectives": ["להבין מה זה VNet"],
    "concepts": [
        {"name": "Virtual Network", "definition": "סביבה מבודדת בענן", "section_ids": ["S1"], "importance": 3},
        {"name": "Subnet", "definition": "תת-רשת", "depends_on": ["Virtual Network"], "section_ids": ["S1"], "importance": 3},
        {"name": "NSG", "definition": "חומת אש לרמת subnet", "section_ids": ["S2"], "importance": 3},
        {"name": "Load Balancer", "definition": "מחלק עומסים", "section_ids": ["S2"], "importance": 2},
        {"name": "ghost", "section_ids": ["S99"], "importance": 7},
    ],
    "examples": [{"description": "subnet לשרתי web", "illustrates": "Subnet", "section_id": "S1"}],
    "key_facts": [{"claim": "יש שני סוגי Load Balancer", "section_id": "S2"}],
}

PLAN = {
    "format": "beginner_explainer",
    "title": "מה זה רשת בענן",
    "hook": "למה צריך רשת פרטית בתוך ענן ציבורי?",
    "segments": [
        {"kind": "intro", "title": "פתיחה"},
        {"kind": "body", "title": "VNet ו-Subnets", "concepts": ["virtual network", "Subnet"], "source_sections": ["S1"], "weight": 2},
        {"kind": "body", "title": "איזון עומסים", "concepts": ["Load Balancer"], "source_sections": ["S2"], "weight": 1},
        {"kind": "closing", "title": "סיכום"},
    ],
    "takeaways": ["VNet מבודד", "NSG מגן", "Load Balancer מחלק"],
}


class FakeLLM(LLMClient):
    """Routes each call by task and records it. `short_first` makes the first draft too short."""

    def __init__(self, name: str = "cloud", short_first: bool = False, unsupported: bool = False) -> None:
        super().__init__(model=f"fake-{name}", name=name)
        self.calls: List[str] = []
        self.short_first = short_first
        self.unsupported = unsupported
        self._drafts: Dict[str, int] = {}

    def chat_json(self, task: str, system: str, user: str, max_tokens: int = 2000, temperature: float = 0.3) -> Dict[str, Any]:
        self.calls.append(task)
        if task == "clean":
            count = len(re.findall(r"^\[\d+\]", user, re.M))
            return {
                "sections": [{"start": 1, "title": "מבוא ל-VNet"}, {"start": max(2, count // 2 + 1), "title": "אבטחה ועומסים"}],
                "fixes": [
                    {"wrong": "VNet", "right": "VNet"},  # no-op, ignored
                    {"wrong": "לא קיים בתמלול", "right": "x"},  # not in text, ignored
                    {"wrong": "Subnets", "right": "Subnets (תתי-רשתות שמחולקות לפי מטרה ושימוש)"},  # a rewrite, ignored
                    {"wrong": "instances", "right": "instances"},
                ],
            }
        if task == "profile":
            return json.loads(json.dumps(PROFILE))
        if task == "plan":
            return json.loads(json.dumps(PLAN))
        if task == "write":
            return {"turns": self._turns(user)}
        if task == "verify":
            if self.unsupported:
                return {"unsupported": [{"turn": 1, "claim": "Azure הומצא ב-1990", "severity": "high"}]}
            return {"unsupported": []}
        if task == "polish":
            return {"edits": [], "notes": ["fine"]}
        raise AssertionError(f"unexpected task {task}")

    def _turns(self, user: str) -> List[Dict[str, str]]:
        title = re.search(r'\): "(.*)"', user).group(1)
        target = int(re.search(r"about (\d+) words", user).group(1))
        must = re.search(r"Must cover:\n(.*?)\nUse these examples", user, re.S).group(1)
        concepts = [line[2:].split(":")[0] for line in must.splitlines() if line.startswith("- ") and "(no specific" not in line]
        draft = self._drafts.get(title, 0)
        self._drafts[title] = draft + 1
        if self.short_first and draft == 0 and concepts:
            return [{"speaker": "Roee", "text": f"{title} {' '.join(concepts)}"}, {"speaker": "Noa", "text": "נכון"}]
        words_per_turn = 30
        turns = []
        for i in range(max(2, round(target / words_per_turn))):
            mention = concepts[i % len(concepts)] if concepts else "השיעור"
            filler = " ".join(["מילה"] * (words_per_turn - 4))
            # Numbers are written as words, as the writer is told to (digits fail the speakability check)
            turns.append({"speaker": "Roee" if i % 2 == 0 else "Noa", "text": f"{title} שורה{'א' * (i + 1)} {mention} {filler}"})
        return turns


def _settings(**overrides) -> SimpleNamespace:
    values = dict(podcast_format="auto", podcast_target_minutes=0, verify_grounding=True,
                  script_max_revisions=1, target_language="")
    values.update(overrides)
    return SimpleNamespace(**values)


def _transcript() -> CleanTranscript:
    return CleanTranscript(
        sections=[Section("S1", "VNet", "Virtual Network ו-Subnet " * 200), Section("S2", "NSG", "NSG ו-Load Balancer " * 200)],
        language="he",
        word_count=20 * 60 * 2,  # ~20 minutes at 120 wpm
    )


# --------------------------------------------------------------------------- llm


def test_parse_json_reply_tolerates_fences_and_chatter():
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('Sure! here it is: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}
    with pytest.raises(LLMError):
        parse_json_reply("no json here")
    with pytest.raises(LLMError):
        parse_json_reply("[1, 2]")


# --------------------------------------------------------------------------- stage 1


def test_srt_parsing_merges_rolling_captions_and_keeps_times():
    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n<i>שלום לכולם</i>\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nשלום לכולם\n\n"
        "3\n00:00:03,000 --> 00:00:04,500\nשלום לכולם היום נדבר\n\n"
        "4\n00:00:09,000 --> 00:00:10,000\nעל VNet\n"
    )
    paragraphs = build_paragraphs(srt)
    assert [p.text for p in paragraphs] == ["שלום לכולם היום נדבר", "על VNet"]  # 4.5s pause starts a new paragraph
    assert paragraphs[0].start == 1.0 and paragraphs[0].end == 4.5


def test_vtt_header_and_unpunctuated_text():
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nhello there\n"
    assert [p.text for p in build_paragraphs(vtt)] == ["hello there"]
    long_block = " ".join(["מילה"] * 400)  # raw ASR output: no punctuation, no line breaks
    assert len(build_paragraphs(long_block)) > 1


def test_clean_text_removes_fillers_and_stutters_only():
    assert clean_text("אז אממ את את ה-VNet, אה, מגדירים um פעם אחת") == "אז את ה-VNet, מגדירים פעם אחת"
    assert clean_text("לא, לא נכון") == "לא, לא נכון"  # emphasis with punctuation is kept


def test_cleaner_without_model_falls_back_to_plain_sections():
    result = TranscriptCleaner(None, "he").run(SAMPLE.read_text(encoding="utf-8"))
    assert result.sections and result.sections[0].id == "S1"
    assert result.sections[0].title == "חלק 1"
    assert result.sectioned_by == "fallback"
    assert "Application Gateway" in " ".join(s.text for s in result.sections)


def test_cleaner_with_model_applies_only_safe_fixes():
    fake = FakeLLM("local")
    result = TranscriptCleaner(fake, "he").run(SAMPLE.read_text(encoding="utf-8"))
    assert [s.title for s in result.sections] == ["מבוא ל-VNet", "אבטחה ועומסים"]
    assert result.fixes == []  # every proposed fix was a no-op, absent, or a rewrite
    assert "Subnets" in result.sections[0].text
    assert result.sectioned_by == "local"


# --------------------------------------------------------------------------- stages 2-3


def test_profile_normalization_drops_invalid_values():
    profile = normalize_profile(PROFILE, ["S1", "S2"])
    ghost = next(c for c in profile["concepts"] if c["name"] == "ghost")
    assert ghost["section_ids"] == [] and ghost["importance"] == 3
    assert profile["level"] == "intro"
    with pytest.raises(LLMError):
        normalize_profile({"concepts": []}, ["S1"])


@pytest.mark.parametrize(
    "lecture, override, expected",
    [(1.3, 0, 2.0), (20, 0, 6.0), (60, 0, 18.0), (300, 0, 30.0), (60, 12, 12.0)],
)
def test_target_minutes_scales_with_the_lecture(lecture, override, expected):
    assert target_minutes(lecture, override) == expected


def test_plan_normalization_guarantees_coverage_and_budget():
    profile = normalize_profile(PROFILE, ["S1", "S2"])
    plan = normalize_plan(json.loads(json.dumps(PLAN)), profile, _transcript(), 6.0, "he")
    kinds = [s["kind"] for s in plan["segments"]]
    assert kinds == ["intro", "body", "body", "closing"]
    taught = {c for s in plan["segments"] for c in s["concepts"]}
    assert {"Virtual Network", "Subnet", "NSG"} <= taught  # NSG was missing from the model's plan
    nsg_segment = next(s for s in plan["segments"] if "NSG" in s["concepts"])
    assert "S2" in nsg_segment["source_sections"]
    assert abs(plan["target_words"] - 6 * 135) < 40
    assert [s["id"] for s in plan["segments"]] == ["P1", "P2", "P3", "P4"]


def test_plan_without_intro_or_closing_gets_them():
    profile = normalize_profile(PROFILE, ["S1", "S2"])
    raw = {"segments": [{"kind": "body", "title": "x", "concepts": ["NSG"]}]}
    plan = normalize_plan(raw, profile, _transcript(), 4.0, "he", forced_format="exam_prep")
    assert [s["kind"] for s in plan["segments"]] == ["intro", "body", "closing"]
    assert plan["format"] == "exam_prep"
    assert plan["segments"][1]["source_sections"]  # filled from the concepts' sections


# --------------------------------------------------------------------------- stage 5


def test_concept_matching_handles_hebrew_prefixes_and_case():
    spoken = normalize_text("ובענן יש לנו virtual networks וגם subnet")
    tokens = _token_set(spoken)
    assert concept_mentioned("ענן", spoken, tokens)
    assert concept_mentioned("Virtual Network", spoken, tokens)
    assert not concept_mentioned("Load Balancer", spoken, tokens)


def test_verifier_reports_structural_problems_as_instructions():
    verifier = ScriptVerifier(checker=None)
    segment = {"id": "P2", "title": "t", "target_words": 100, "concepts": ["NSG"]}
    turns = [
        {"speaker": "Roee", "text": "**כותרת** " + " ".join(["מילה"] * 120)},
        {"speaker": "Roee", "text": "עוד"},
        {"speaker": "Roee", "text": "ועוד"},
        {"speaker": "Roee", "text": "ושוב"},
        {"speaker": "Dana", "text": "שלום"},
    ]
    issues = " | ".join(verifier.check(segment, turns, "", []).issues)
    assert "monologue" in issues
    assert "markdown" in issues
    assert "4 times in a row" in issues
    assert "Dana" in issues
    assert "NSG" in issues


# --------------------------------------------------------------------------- end to end


def test_engine_end_to_end_with_resume(tmp_path):
    cloud, local = FakeLLM("cloud"), FakeLLM("local")
    engine = ScriptEngine(_settings(), llms=EngineLLMs(cloud=cloud, local=local))
    text = SAMPLE.read_text(encoding="utf-8")

    result = engine.run(text, {"topic": "azure"}, tmp_path)
    dialogue = result.dialogue
    assert {t["speaker"] for t in dialogue["dialogue"]} == {"Roee", "Noa"}
    meta = dialogue["metadata"]
    assert len(meta["chapters"]) == 4 and meta["format"] == "beginner_explainer"
    assert meta["quality"]["core_concept_coverage"] == 1.0
    assert meta["quality"]["segments_with_open_issues"] == []
    assert meta["estimated_duration_minutes"] > 1.5  # the sample is ~1.3 min of lecture -> 2-min episode
    for name in ("01_clean", "02_profile", "03_plan", "04_script", "05_report"):
        assert (tmp_path / f"{name}.json").exists()
    assert cloud.calls.count("write") == 4 and "verify" in local.calls and "verify" not in cloud.calls

    # Second run: everything is reused
    cloud.calls.clear(), local.calls.clear()
    engine.run(text, {"topic": "azure"}, tmp_path)
    assert cloud.calls == [] and local.calls == []

    # Edit the plan by hand: only the script is rewritten, following the edit
    plan_path = tmp_path / "03_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["segments"][1]["title"] = "כותרת שנערכה ידנית"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    result = engine.run(text, {"topic": "azure"}, tmp_path)
    # The edited plan is rewritten and the new script polished again; nothing before the plan reruns
    assert set(cloud.calls) == {"write", "polish"} and set(local.calls) == {"verify"}
    assert result.dialogue["metadata"]["chapters"][1]["title"] == "כותרת שנערכה ידנית"


def test_failed_segment_is_revised_with_the_verifier_feedback(tmp_path):
    cloud = FakeLLM("cloud", short_first=True)
    engine = ScriptEngine(_settings(), llms=EngineLLMs(cloud=cloud, local=None))
    result = engine.run(SAMPLE.read_text(encoding="utf-8"), {}, tmp_path)
    segments = result.report["segments"]
    assert all(s["passed"] for s in segments)
    assert result.report["segments_revised"] == 2  # the two body segments
    assert cloud.calls.count("write") == 6
    assert "verify" in cloud.calls  # no local model: the cloud model checks grounding


def test_unsupported_claims_are_reported(tmp_path):
    cloud = FakeLLM("cloud", unsupported=True)
    engine = ScriptEngine(_settings(script_max_revisions=0), llms=EngineLLMs(cloud=cloud))
    report = engine.run(SAMPLE.read_text(encoding="utf-8"), {}, tmp_path).report
    assert report["unsupported_high"] == 4
    assert len(report["segments_with_open_issues"]) == 4


class PromptRecordingLLM(FakeLLM):
    def __init__(self, name: str = "cloud") -> None:
        super().__init__(name)
        self.prompts: List[tuple] = []

    def chat_json(self, task, system, user, max_tokens=2000, temperature=0.3):
        self.prompts.append((task, system, user))
        return super().chat_json(task, system, user, max_tokens, temperature)


ENGLISH_LECTURE = (
    "Hello everyone, today we talk about virtual networks in the cloud.\n\n"
    "A Virtual Network gives you an isolated environment. Inside it you define a Subnet for each purpose.\n\n"
    "Network Security Groups, or NSG, act like a firewall. A Load Balancer spreads traffic across instances."
)


@pytest.mark.parametrize("chosen, expected", [(None, "Hebrew"), ("en", "English")])
def test_podcast_language_is_hebrew_unless_english_is_chosen(tmp_path, chosen, expected):
    cloud, local = PromptRecordingLLM("cloud"), PromptRecordingLLM("local")
    settings = _settings(**({"podcast_language": chosen} if chosen else {}))
    ScriptEngine(settings, llms=EngineLLMs(cloud=cloud, local=local)).run(ENGLISH_LECTURE, {}, tmp_path)

    clean_prompt = next(user for task, _s, user in local.prompts if task == "clean")
    assert "Language: English" in clean_prompt  # cleanup reads the lecture in its own language
    write_system = next(system for task, system, _u in cloud.prompts if task == "write")
    assert f"podcast in {expected}" in write_system
    profile_prompt = next(user for task, _s, user in cloud.prompts if task == "profile")
    assert f"value in {expected}" in profile_prompt


def test_plan_only_stops_before_writing(tmp_path):
    cloud = FakeLLM("cloud")
    engine = ScriptEngine(_settings(), llms=EngineLLMs(cloud=cloud))
    result = engine.run(SAMPLE.read_text(encoding="utf-8"), {}, tmp_path, stop_after="plan")
    assert result.plan["segments"] and "write" not in cloud.calls
    assert not (tmp_path / "04_script.json").exists()
