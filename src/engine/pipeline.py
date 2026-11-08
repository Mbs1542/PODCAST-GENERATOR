"""Run stages 1-5 and produce dialogue.json in the format the rest of the app expects.

Every stage saves its result in the work directory (01_clean.json ... 05_report.json).
A re-run reuses a stage when its inputs have not changed, so an interrupted run
resumes where it stopped, and a hand-edited 03_plan.json is respected: only the
script is rewritten from it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..utils.logging import get_logger
from ..utils.text_utils import analyze_language
from .llm import EngineLLMs, build_engine_llms
from .planner import SPEECH_WORDS_PER_MINUTE, EpisodePlanner
from .polish import EpisodePolisher

POLISH_VERSION = "1"
from .profile import LessonProfiler, metadata_hints
from .transcript import CleanTranscript, TranscriptCleaner
from .verifier import ScriptVerifier, SegmentReport
from .writer import SegmentWriter, Turn, overused_phrases

ENGINE_VERSION = "2.0"
PREVIOUS_LINES = 6
STAGES = ("clean", "profile", "plan", "script")


@dataclass
class EngineResult:
    dialogue: Dict[str, Any]
    report: Dict[str, Any]
    plan: Dict[str, Any]
    work_dir: Path


def _hash(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(json.dumps(part, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()[:16]


class ScriptEngine:
    """Stages 1-5: clean -> lesson profile -> episode plan -> segment writing + verification."""

    def __init__(
        self,
        settings: Any,
        cost_tracker: Optional[Any] = None,
        llms: Optional[EngineLLMs] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.settings = settings
        self.llms = llms or build_engine_llms(settings, cost_tracker)
        self.logger = get_logger(self.__class__.__name__)
        self._log_sink = log

    def log(self, message: str) -> None:
        self.logger.info(message)
        if self._log_sink:
            self._log_sink(message)

    def run(
        self,
        transcript_text: str,
        metadata: Dict[str, Any],
        work_dir: Path,
        force: bool = False,
        stop_after: Optional[str] = None,
    ) -> EngineResult:
        """Run the stages; `stop_after="plan"` stops before writing so the plan can be reviewed."""
        if stop_after and stop_after not in STAGES:
            raise ValueError(f"stop_after must be one of {STAGES}")
        work_dir.mkdir(parents=True, exist_ok=True)
        source_language = "en" if analyze_language(transcript_text).primary == "en" else "he"
        language = self._language(source_language)  # the podcast's language: Hebrew unless English was chosen
        fmt = str(getattr(self.settings, "podcast_format", "auto") or "auto")
        minutes = float(getattr(self.settings, "podcast_target_minutes", 0) or 0)
        focus = str(getattr(self.settings, "podcast_focus", "") or "").strip()
        checker = self.llms.checker
        self.log(
            f"Script engine {ENGINE_VERSION}: lecture={source_language}, podcast={language}, writer={self.llms.cloud.model}, "
            f"checker={checker.name}:{checker.model}"
        )

        # Only the inputs belong in the key: if Ollama starts or stops between runs, an edited
        # plan must still be reused rather than silently rebuilt (use force=True to redo cleanup)
        # Cleanup works on the lecture as spoken; everything after it is written in the podcast's language
        key = _hash(ENGINE_VERSION, transcript_text, source_language)
        clean = self._stage(work_dir, "01_clean", key, force, lambda: TranscriptCleaner(
            checker, source_language, self.log
        ).run(transcript_text).to_dict())
        transcript = CleanTranscript.from_dict(clean)
        if stop_after == "clean":
            return EngineResult({}, {}, {}, work_dir)

        hints = metadata_hints(metadata)
        key = _hash(key, hints, language)
        profile = self._stage(work_dir, "02_profile", key, force, lambda: LessonProfiler(
            self.llms.cloud, language, self.log
        ).run(transcript, hints))
        if stop_after == "profile":
            return EngineResult({}, {}, {}, work_dir)

        # A focus is only part of the key when set, so plans made before the option existed stay valid
        key = _hash(key, fmt, minutes, *([focus] if focus else []))
        plan = self._stage(work_dir, "03_plan", key, force, lambda: EpisodePlanner(
            self.llms.cloud, language, fmt, minutes, self.log, focus=focus
        ).run(profile, transcript))
        if stop_after == "plan":
            self.log(f"Plan saved to {work_dir / '03_plan.json'}; stopping before writing.")
            return EngineResult({}, {}, plan, work_dir)

        # The plan file may have been edited by hand, so the script depends on its content
        key = _hash(key, plan)
        script = self._stage(work_dir, "04_script", key, force, lambda: self._write_script(
            language, profile, plan, transcript
        ))
        report = script["report"]
        segments = script["segments"]
        if getattr(self.settings, "script_polish", True):
            polished = self._stage(work_dir, "04b_polish", _hash(key, "polish", POLISH_VERSION), force, lambda: EpisodePolisher(
                self.llms.cloud, language, self.log
            ).run(script["segments"], plan))
            segments = polished["segments"]
            report = {**report, "polish_edits": polished.get("edits", 0), "editor_notes": polished.get("notes", [])}
        # Measured on the final script: reactions and openers the episode still leans on
        report["overused_phrases"] = overused_phrases([t for seg in segments for t in seg["turns"]])
        (work_dir / "05_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        dialogue = self._dialogue_payload(segments, plan, profile, report, language)
        return EngineResult(dialogue, report, plan, work_dir)

    # ------------------------------------------------------------------ stages

    def _stage(self, work_dir: Path, name: str, key: str, force: bool, compute: Callable[[], Dict]) -> Dict:
        path = work_dir / f"{name}.json"
        if path.exists() and not force:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
            # A plan the user edited keeps its key, so it is still reused
            if isinstance(cached, dict) and cached.get("_key") == key:
                self.log(f"{name}: reusing saved result")
                cached.pop("_key", None)
                return cached
        result = compute()
        path.write_text(json.dumps({**result, "_key": key}, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def _write_script(
        self,
        language: str,
        profile: Dict[str, Any],
        plan: Dict[str, Any],
        transcript: CleanTranscript,
    ) -> Dict[str, Any]:
        writer = SegmentWriter(self.llms.cloud, language, profile, plan, transcript)
        verifier = ScriptVerifier(self.llms.checker, bool(getattr(self.settings, "verify_grounding", True)))
        max_revisions = max(0, int(getattr(self.settings, "script_max_revisions", 1)))

        written: List[List[Turn]] = []
        reports: List[SegmentReport] = []
        for index, segment in enumerate(plan["segments"]):
            earlier = [turn for turns in written for turn in turns]
            previous_lines = earlier[-PREVIOUS_LINES:]
            covered = [s["title"] for s in plan["segments"][:index] if s.get("title")]
            source = writer.source_for(index)

            turns = writer.write(index, previous_lines, covered, earlier_turns=earlier)
            report = verifier.check(segment, turns, source, earlier)
            best_turns, best_report = turns, report
            revision = 0
            while not best_report.passed and revision < max_revisions:
                revision += 1
                self.log(f"Segment {segment['id']}: revising ({'; '.join(best_report.issues)[:200]})")
                turns = writer.write(index, previous_lines, covered, best_report.issues, best_turns, earlier_turns=earlier)
                report = verifier.check(segment, turns, source, earlier)
                if len(report.issues) <= len(best_report.issues):
                    best_turns, best_report = turns, report
            best_report.revisions = revision
            written.append(best_turns)
            reports.append(best_report)
            status = "ok" if best_report.passed else f"{len(best_report.issues)} open issue(s)"
            self.log(
                f"Stage 4-5: segment {index + 1}/{len(plan['segments'])} '{segment['title']}': "
                f"{best_report.words}/{segment['target_words']} words, {status}"
            )

        return {
            "segments": [
                {"id": seg["id"], "title": seg["title"], "turns": turns}
                for seg, turns in zip(plan["segments"], written)
            ],
            "report": self._summary(reports, profile, plan, language),
        }

    # ------------------------------------------------------------------ output

    def _summary(
        self,
        reports: List[SegmentReport],
        profile: Dict[str, Any],
        plan: Dict[str, Any],
        language: str,
    ) -> Dict[str, Any]:
        total_words = sum(r.words for r in reports)
        planned_core = [c["name"] for c in profile["concepts"] if c["importance"] == 3]
        missing = {name for r in reports for name in r.missing_concepts}
        covered_core = [name for name in planned_core if name not in missing]
        checked = [r for r in reports if r.grounding_checked]
        flagged = sum(1 for r in checked for u in r.unsupported if u["severity"] == "high")
        wpm = SPEECH_WORDS_PER_MINUTE.get(language, 135)
        return {
            "engine": ENGINE_VERSION,
            "format": plan["format"],
            "target_minutes": plan["target_minutes"],
            "estimated_minutes": round(total_words / wpm, 1),
            "total_words": total_words,
            "core_concept_coverage": round(len(covered_core) / len(planned_core), 3) if planned_core else 1.0,
            "core_concepts_missing": [name for name in planned_core if name in missing],
            "grounding_checked_segments": len(checked),
            "unsupported_high": flagged,
            "segments_revised": sum(1 for r in reports if r.revisions),
            "segments_with_open_issues": [r.segment_id for r in reports if not r.passed],
            "checker": f"{self.llms.checker.name}:{self.llms.checker.model}",
            "segments": [r.to_dict() for r in reports],
        }

    def _dialogue_payload(
        self,
        segments: List[Dict[str, Any]],
        plan: Dict[str, Any],
        profile: Dict[str, Any],
        report: Dict[str, Any],
        language: str,
    ) -> Dict[str, Any]:
        dialogue: List[Turn] = []
        chapters = []
        for segment in segments:
            chapters.append({"title": segment["title"], "start_index": len(dialogue)})
            dialogue.extend(segment["turns"])
        return {
            "dialogue": dialogue,
            "metadata": {
                "total_exchanges": len(dialogue),
                "estimated_duration_minutes": report["estimated_minutes"],
                "key_topics": [c["name"] for c in profile["concepts"] if c["importance"] >= 2][:10],
                "title": plan["title"],
                "format": plan["format"],
                "language": language,
                "chapters": chapters,
                "engine": ENGINE_VERSION,
                "quality": {k: v for k, v in report.items() if k != "segments"},
            },
        }

    def _language(self, source_language: str) -> str:
        """The podcast's language: the chosen one (Hebrew by default), else the older target_language."""
        for attr in ("podcast_language", "target_language"):
            configured = str(getattr(self.settings, attr, "") or "").lower()
            if configured.startswith("en"):
                return "en"
            if configured.startswith("he"):
                return "he"
        return "he"
