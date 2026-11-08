"""Run the evaluation: lesson source -> script engine (stages 1-5) -> critic -> report.

Each run writes outputs/eval/runs/<timestamp>/ with every scenario's script and review,
a summary.json and a report.md that compares against the previous run, so a prompt
change can be judged by its effect on all twenty lessons at once.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..engine import EngineLLMs, LLMClient, ScriptEngine
from ..utils import CostTracker
from .rubric import (
    CRITIC_SYSTEM,
    DIMENSION_KEYS,
    DIMENSIONS,
    SUMMARY_SYSTEM,
    critic_user_prompt,
    summary_user_prompt,
    weighted_overall,
)
from .scenarios import CORE_IDS, LESSON_SYSTEM, SCENARIOS, outline_prompt, part_prompt

LECTURE_WORDS_PER_MINUTE = 120
WORDS_PER_PART = 1300
CRITIC_SOURCE_MAX_CHARS = 60_000
SHORT = {"faithfulness": "faith", "coverage": "cover", "pedagogy": "teach", "dialogue_dynamics": "dynam",
         "engagement": "engag", "structure": "struct", "language_quality": "lang", "fit": "fit",
         "audio_readiness": "audio", "notebooklm_parity": "NLM"}

Log = Callable[[str], None]


def _hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


def _file_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _minutes(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m" if seconds >= 3600 else f"{seconds // 60}m{seconds % 60:02d}s"


def _short_id(scenario: Dict[str, Any]) -> str:
    """'08-med-cardio' -> '08'."""
    return scenario["id"].split("-", 1)[0]


def rubric_module_path() -> Path:
    return Path(__file__).with_name("rubric.py")


def pipeline_fingerprint(settings: Any) -> str:
    """Changes whenever the engine's code/prompts or the writing model change."""
    engine_dir = Path(__file__).resolve().parents[1] / "engine"
    sources = "".join(_file_text(p) for p in sorted(engine_dir.glob("*.py")))
    models = "|".join(str(getattr(settings, name, "")) for name in (
        "llm_provider", "gemini_text_model", "openai_deployment", "local_llm_model", "local_llm_mode", "script_max_revisions",
    ))
    return _hash(sources, models)


def select_scenarios(ids: Optional[List[str]] = None, limit: int = 0, everything: bool = False) -> List[Dict[str, Any]]:
    """By id prefixes; with no ids, the ten core scenarios (or all twenty with everything=True)."""
    if ids:
        chosen = [s for s in SCENARIOS if any(s["id"].startswith(i) for i in ids)]
    else:
        chosen = [s for s in SCENARIOS if everything or s["id"] in CORE_IDS]
    return chosen[:limit] if limit else chosen


class EvalRunner:
    def __init__(
        self,
        settings: Any,
        engine_llms_factory: Callable[[Any, CostTracker], EngineLLMs],
        judge: LLMClient,
        author: LLMClient,
        out_dir: Path,
        lessons_dir: Path,
        judge_samples: int = 1,
        log: Optional[Log] = None,
    ) -> None:
        self.settings = settings
        self.engine_llms_factory = engine_llms_factory
        self.judge = judge
        self.author = author
        self.out_dir = Path(out_dir)
        self.lessons_dir = Path(lessons_dir)
        self.judge_samples = max(1, judge_samples)
        self.log = log or print

    # ------------------------------------------------------------------ sources

    def lesson(self, scenario: Dict[str, Any]) -> str:
        """A real transcript if one was provided, else a generated one (generated once, then reused).

        The outline and every part are saved as they arrive, so a dropped connection
        resumes from the part it was on instead of starting the lesson again.
        """
        sid = scenario["id"]
        path = self.lessons_dir / f"{sid}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8-sig")
        drafts = self.lessons_dir / "_drafts" / _short_id(scenario)
        drafts.mkdir(parents=True, exist_ok=True)
        total_words = scenario["lecture_minutes"] * LECTURE_WORDS_PER_MINUTE
        parts = max(1, math.ceil(total_words / WORDS_PER_PART))
        words = math.ceil(total_words / parts)
        self.log(f"  [{sid}] generating the source lesson ({parts} part(s), ~{total_words} words)")

        outline_path = drafts / "outline.json"
        outline = _read_json(outline_path)
        if outline is None:
            outline = self.author.chat_json("eval_outline", LESSON_SYSTEM, outline_prompt(scenario, parts), max_tokens=1500, temperature=0.7)
            plan = [str(p) for p in outline.get("parts", []) if str(p).strip()] or [scenario["title"]]
            outline["parts"] = (plan * parts)[:parts] if len(plan) < parts else plan[:parts]
            _write_json(outline_path, outline)

        texts: List[str] = []
        for index in range(parts):
            part_path = drafts / f"part_{index + 1:02d}.txt"
            if part_path.exists():
                texts.append(part_path.read_text(encoding="utf-8"))
                continue
            tail = texts[-1][-300:] if texts else ""
            reply = self.author.chat_json(
                "eval_part", LESSON_SYSTEM, part_prompt(scenario, outline, index, words, tail),
                max_tokens=min(8000, words * 4 + 500), temperature=0.8,
            )
            text = str(reply.get("text", "")).strip()
            part_path.write_text(text, encoding="utf-8")
            texts.append(text)
            self.log(f"  [{sid}] source part {index + 1}/{parts} ready")
        transcript = "\n\n".join(t for t in texts if t)
        if not transcript:
            raise RuntimeError("the source lesson came back empty")
        path.write_text(transcript, encoding="utf-8")
        return transcript

    # ------------------------------------------------------------------ one scenario

    def _work_dir(self, scenario: Dict[str, Any]) -> Path:
        """Where the engine keeps its stage files for this scenario.

        Stable across runs, so a rerun after a dropped connection resumes from the last
        finished stage; keyed by the prompts and models, so a prompt change starts fresh.
        """
        # Short folder names: Windows refuses paths over 260 characters, and installs can sit deep
        return self.out_dir / "work" / pipeline_fingerprint(self.settings)[:8] / _short_id(scenario)

    def run_scenario(self, scenario: Dict[str, Any], run_dir: Path) -> Dict[str, Any]:
        sid = scenario["id"]
        source = self.lesson(scenario)
        settings = copy.copy(self.settings)
        settings.podcast_format = scenario.get("format") or "auto"
        settings.podcast_target_minutes = float(scenario.get("target_minutes") or 0)
        settings.podcast_focus = scenario.get("focus") or ""
        settings.podcast_language = scenario.get("output_language", "he")

        costs = CostTracker()
        folder = run_dir / _short_id(scenario)
        folder.mkdir(parents=True, exist_ok=True)
        work = self._work_dir(scenario)
        engine = ScriptEngine(settings, costs, llms=self.engine_llms_factory(settings, costs),
                              log=lambda message: self.log(f"    [{sid}] {message}"))
        result = engine.run(source, {"topic": scenario["title"]}, work)
        profile = json.loads((work / "02_profile.json").read_text(encoding="utf-8"))
        (folder / "dialogue.json").write_text(json.dumps(result.dialogue, ensure_ascii=False, indent=2), encoding="utf-8")

        # The critic's verdict is kept next to the script it judged; the same script,
        # critic model and rubric give the same review, so it is not paid for twice
        critic_key = _hash(json.dumps(result.dialogue, ensure_ascii=False, sort_keys=True), _file_text(rubric_module_path()),
                           getattr(self.judge, "model", ""), str(self.judge_samples))
        cached = _read_json(work / "critic.json")
        if cached and cached.get("_key") == critic_key:
            self.log(f"  [{sid}] critic: reusing the saved review")
            review = cached["review"]
        else:
            review = self._judge(scenario, source, profile, result.plan, result.dialogue, result.report)
            _write_json(work / "critic.json", {"_key": critic_key, "review": review})
        record = {
            "scenario": scenario,
            **review,
            "auto": {k: result.report.get(k) for k in (
                "format", "target_minutes", "estimated_minutes", "core_concept_coverage", "unsupported_high", "segments_revised",
                "polish_edits", "overused_phrases",
            )},
            "pipeline_cost_usd": costs.openai_cost,
        }
        (folder / "critic.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def _judge(self, scenario, source, profile, plan, dialogue, report) -> Dict[str, Any]:
        if len(source) > CRITIC_SOURCE_MAX_CHARS:
            source = source[:CRITIC_SOURCE_MAX_CHARS] + "\n[... the rest of the lecture is omitted for length ...]"
        prompt = critic_user_prompt(scenario, source, profile, plan, dialogue, report)
        samples = []
        for sample in range(self.judge_samples):
            samples.append(self.judge.chat_json(
                "critic", CRITIC_SYSTEM, prompt, max_tokens=3500, temperature=0.0 if self.judge_samples == 1 else 0.3,
            ))
        scores: Dict[str, float] = {}
        evidence: Dict[str, Any] = {}
        for key in DIMENSION_KEYS:
            values = []
            for reply in samples:
                item = (reply.get("scores") or {}).get(key)
                raw = item.get("score") if isinstance(item, dict) else item
                try:
                    values.append(max(1.0, min(10.0, float(raw))))
                except (TypeError, ValueError):
                    continue
            if values:
                scores[key] = round(sum(values) / len(values), 2)
                first = (samples[0].get("scores") or {}).get(key)
                evidence[key] = first if isinstance(first, dict) else {}
        first = samples[0]
        return {
            "scores": scores,
            "evidence": evidence,
            "overall": weighted_overall(scores),
            "verdict": str(first.get("verdict", "")),
            "strengths": first.get("strengths") or [],
            "issues": first.get("issues") or [],
            "pipeline_recommendations": first.get("pipeline_recommendations") or [],
        }

    # ------------------------------------------------------------------ whole run

    def prepare_sources(self, scenarios: List[Dict[str, Any]], workers: int = 1) -> List[str]:
        """Generate the missing source lessons only (they do not depend on the pipeline). Returns failures."""
        failures: List[str] = []

        def one(scenario: Dict[str, Any]) -> None:
            try:
                words = len(self.lesson(scenario).split())
                self.log(f"[source] {scenario['id']}: {words} words")
            except Exception as exc:
                failures.append(f"{scenario['id']}: {type(exc).__name__}: {exc}")
                self.log(f"[source] {scenario['id']} FAILED: {exc}")

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, scenarios))
        return failures

    def run(self, scenarios: List[Dict[str, Any]], workers: int = 1) -> Dict[str, Any]:
        runs_dir = self.out_dir / "runs"
        previous = self._previous_summary(runs_dir)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_dir = runs_dir / stamp
        suffix = 1
        while run_dir.exists():  # two runs in the same second must not share a folder
            suffix += 1
            run_dir = runs_dir / f"{stamp}_{suffix}"
        run_dir.mkdir(parents=True)

        total = len(scenarios)
        started = time.monotonic()
        done = 0
        lock = threading.Lock()

        def one(scenario: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal done
            self.log(f"[start] {scenario['id']}: {scenario['title']}")
            t0 = time.monotonic()
            try:
                record = self.run_scenario(scenario, run_dir)
                outcome = f"overall {record['overall']:.1f}/10 ({record['verdict'] or 'no verdict'})"
            except Exception as exc:  # one broken scenario must not lose the others
                record = {"scenario": scenario, "error": f"{type(exc).__name__}: {exc}"}
                outcome = f"FAILED: {record['error']}"
            with lock:
                done += 1
                elapsed = time.monotonic() - started
                # Scenarios run `workers` at a time, so the average wall-clock per finished scenario predicts the rest
                eta = elapsed / done * (total - done)
                self.log(f"[{done}/{total}] {scenario['id']} {outcome}  took {_minutes(time.monotonic() - t0)};"
                         f" elapsed {_minutes(elapsed)}, ETA {_minutes(eta) if done < total else 'done'}")
            return record

        workers = max(1, min(workers, total))
        if workers == 1:
            results = [one(s) for s in scenarios]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one, scenarios))

        scored = [r for r in results if "scores" in r]
        averages = {
            key: round(sum(r["scores"][key] for r in scored if key in r["scores"]) / max(1, sum(1 for r in scored if key in r["scores"])), 2)
            for key in DIMENSION_KEYS
        }
        overall = round(sum(r["overall"] for r in scored) / len(scored), 2) if scored else 0.0

        synthesis: Dict[str, Any] = {}
        if scored:
            try:
                synthesis = self.judge.chat_json("eval_summary", SUMMARY_SYSTEM, summary_user_prompt(scored, averages),
                                                 max_tokens=3500, temperature=0.2)
            except Exception as exc:
                self.log(f"Cross-scenario summary failed: {exc}")

        summary = {
            "run": run_dir.name,
            "scenarios": len(scenarios),
            "scored": len(scored),
            "overall": overall,
            "averages": averages,
            "previous_run": previous.get("run") if previous else None,
            "delta": {k: round(averages[k] - previous["averages"].get(k, averages[k]), 2) for k in averages} if previous else {},
            "overall_delta": round(overall - previous.get("overall", overall), 2) if previous else None,
            "diagnosis": synthesis.get("diagnosis", ""),
            "improvements": synthesis.get("improvements", []),
            "results": [{k: r.get(k) for k in ("overall", "verdict", "scores", "error")} | {"id": r["scenario"]["id"]} for r in results],
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "report.md").write_text(render_report(summary, results), encoding="utf-8")
        summary["report_path"] = str(run_dir / "report.md")
        return summary

    @staticmethod
    def _previous_summary(runs_dir: Path) -> Optional[Dict[str, Any]]:
        if not runs_dir.exists():
            return None
        for folder in sorted(runs_dir.iterdir(), reverse=True):
            path = folder / "summary.json"
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
        return None


def _delta(value: Optional[float]) -> str:
    if value is None or value == 0:
        return ""
    return f" ({'+' if value > 0 else ''}{value:.1f})"


def render_report(summary: Dict[str, Any], results: List[Dict[str, Any]]) -> str:
    lines = [
        f"# Evaluation run {summary['run']}",
        "",
        f"**Overall: {summary['overall']:.2f}/10**{_delta(summary.get('overall_delta'))} across {summary['scored']}/{summary['scenarios']} scenarios"
        + (f" (compared with run {summary['previous_run']})" if summary.get("previous_run") else ""),
        "",
    ]
    if summary.get("diagnosis"):
        lines += ["## Diagnosis", "", summary["diagnosis"], ""]

    lines += ["## Average per dimension (weakest first)", "", "| Dimension | Weight | Average | Change |", "|---|---|---|---|"]
    weights = {d["key"]: d["weight"] for d in DIMENSIONS}
    names = {d["key"]: d["name"] for d in DIMENSIONS}
    for key, value in sorted(summary["averages"].items(), key=lambda kv: kv[1]):
        lines.append(f"| {names[key]} | {weights[key]} | {value:.2f} |{_delta(summary.get('delta', {}).get(key))} |")

    header = "| Scenario | Overall | " + " | ".join(SHORT[k] for k in DIMENSION_KEYS) + " | Verdict |"
    lines += ["", "## Scores per scenario", "", header, "|" + "---|" * (len(DIMENSION_KEYS) + 3)]
    for record in results:
        sid = record["scenario"]["id"]
        if "error" in record:
            lines.append(f"| {sid} | FAILED | " + " | ".join("" for _ in DIMENSION_KEYS) + f" | {record['error'][:60]} |")
            continue
        cells = " | ".join(f"{record['scores'].get(k, 0):.0f}" for k in DIMENSION_KEYS)
        lines.append(f"| {sid} | {record['overall']:.1f} | {cells} | {record.get('verdict', '')} |")

    if summary.get("improvements"):
        lines += ["", "## Top improvements (cross-scenario)", ""]
        for item in summary["improvements"]:
            fixes = ", ".join(item.get("fixes", []))
            lines.append(f"{item.get('rank', '-')}. **[{item.get('stage', '?')}]** {item.get('change', '')}"
                         f" (improves: {fixes}; effort: {item.get('effort', '?')})")

    lines += ["", "## Issues per scenario", ""]
    for record in results:
        if "error" in record:
            continue
        scenario = record["scenario"]
        lines += [f"### {scenario['id']}: {scenario['title']} ({record['overall']:.1f}/10)", f"_Tests: {scenario.get('tests', '')}_", ""]
        for issue in record.get("issues", [])[:6]:
            quote = f" “{issue.get('quote')}”" if issue.get("quote") else ""
            lines.append(f"- **{issue.get('severity', '?')} / {issue.get('dimension', '?')}**:{quote} {issue.get('problem', '')}"
                         f" Fix: {issue.get('fix', '')}")
        lines.append("")
    return "\n".join(lines)
