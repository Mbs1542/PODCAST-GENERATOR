"""The release gate for M.B.S Studio. A version is released only when it passes.

    python -m scripts.gate                    # all stages; the expert review covers files changed since the last release
    python -m scripts.gate --full             # the expert review reads every source file, line by line
    python -m scripts.gate --skip-review      # no model calls (version, syntax, scan, tests, eval)
    python -m scripts.gate --bump patch       # raise the version (patch|minor|major) and open a CHANGELOG entry, then gate
    python -m scripts.gate --tag              # on a pass, tag the commit v<version>
    python -m scripts.gate --budget 2         # most the expert review may spend, in USD (default 1)
    python -m scripts.gate --skip-links       # without the live check of real links (offline)

What each stage checks is described in src/release/gate.py. Waivers (a finding accepted with a reason) go in
config/gate_waivers.json; legacy test failures that are known and tracked go in config/gate_known_failures.txt.
Exit code 0 = passed, 1 = failed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.release import gate as g  # noqa: E402


def _outputs() -> Path:
    from src.web.settings_store import SettingsStore

    settings, _ = SettingsStore(ROOT).load_settings()
    outputs = Path(settings.output_base_dir) if settings else ROOT / "outputs"
    return outputs if outputs.is_absolute() else ROOT / outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="review every source file, not only the changed ones")
    parser.add_argument("--skip-review", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-links", action="store_true", help="do not check the real links (no network)")
    parser.add_argument("--bump", choices=("patch", "minor", "major"))
    parser.add_argument("--note", default="", help="the CHANGELOG line for --bump")
    parser.add_argument("--tag", action="store_true", help="tag the commit v<version> when the gate passes")
    parser.add_argument("--budget", type=float, default=1.0, help="USD the expert review may spend")
    parser.add_argument("--model", default="", help="reviewer model (default: the app's writer model)")
    parser.add_argument("--since", default="", help="review files changed since this git ref (default: the last v* tag)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    if args.bump:
        print(f"Version raised to {g.bump(ROOT, args.bump, args.note)}")
    started = datetime.now()
    files = g.repo_files(ROOT)
    outputs = _outputs()
    stages = []

    def say(stage: g.StageResult) -> g.StageResult:
        blocking = sum(f.blocks for f in stage.findings)
        print(f"[{stage.name:8}] {'ok ' if not blocking else 'BLOCK'} {len(stage.findings):4} findings, {blocking} blocking  "
              f"({stage.seconds:.0f}s)  {'; '.join(stage.notes)}", flush=True)
        return stage

    version_stage = say(g.timed(lambda: g.stage_version(ROOT)))
    version = next((n.split()[1] for n in version_stage.notes if n.startswith("version ")), "?")
    stages.append(version_stage)
    stages.append(say(g.timed(lambda: g.stage_syntax(ROOT, files))))
    stages.append(say(g.timed(lambda: g.stage_scan(ROOT, files))))
    if args.skip_tests:
        stages.append(g.StageResult("tests", status="skipped", notes=["skipped (--skip-tests)"]))
    else:
        print("[tests   ] running the test suite...", flush=True)
        stages.append(say(g.timed(lambda: g.stage_tests(ROOT))))

    if args.skip_review:
        stages.append(g.StageResult("review", status="skipped", notes=["skipped (--skip-review)"]))
    else:
        from src.engine.llm import build_cloud_llm
        from src.utils.costs import CostTracker
        from src.web.credits import google_spend_usd
        from src.web.settings_store import SettingsStore

        settings, error = SettingsStore(ROOT).load_settings()
        if settings is None:
            stage = g.StageResult("review", notes=[f"no model settings: {error}"])
            stage.findings.append(g.Finding("review", "high", "REV0", "The expert review could not run (settings incomplete)"))
            stages.append(say(stage))
        else:
            costs = CostTracker()
            llm = build_cloud_llm(settings, costs, model=args.model)
            model = args.model or getattr(settings, "gemini_text_model", "") or "?"
            base = args.since or g.last_release_tag(ROOT) or "origin/main~1"
            targets = g.review_targets(ROOT, files, args.full, base)
            print(f"[review  ] {len(targets)} files {'(all)' if args.full else f'changed since {base}'}, budget ${args.budget:.2f}", flush=True)
            try:
                stages.append(say(g.timed(lambda: g.stage_review(ROOT, targets, llm, lambda: google_spend_usd(costs), args.budget,
                                                                  outputs / "gate" / "review_cache", model, args.workers))))
            finally:  # what the review cost goes to the app's credit ledger, like every other job
                from src.web.credits import CreditsLedger

                CreditsLedger(outputs / "private").record("google", google_spend_usd(costs), f"gate:v{version}", "בדיקת גרסה")
    stages.append(say(g.timed(lambda: g.stage_eval(outputs / "exam_eval" / "history.jsonl", version))))
    if args.skip_links:
        stages.append(g.StageResult("links", status="skipped", notes=["skipped (--skip-links)"]))
    else:
        stages.append(say(g.timed(lambda: g.stage_links(outputs / "gate" / "links.txt", outputs / "private" / "cookies"))))

    g.apply_waivers([f for s in stages for f in s.findings], g.load_waivers(ROOT))
    passed = g.finish(stages)
    _, commit = g._run(["git", "rev-parse", "HEAD"], ROOT)
    report = g.write_report(outputs / "gate", version, stages, passed, started, commit.strip())
    print()
    print(f"GATE {'PASSED' if passed else 'FAILED'} for v{version}. Report: {report}")
    if not passed:
        for stage in stages:
            for f in stage.findings:
                if f.blocks:
                    print(f"  BLOCKS  [{f.rule}] {f.where()}: {f.message[:160]}")
        return 1
    if args.tag:
        code, out = g._run(["git", "tag", "-a", f"v{version}", "-m", f"Gate passed {started:%Y-%m-%d %H:%M}"], ROOT)
        if code != 0:
            print(f"Could not tag: {out.strip()}")
            return 1
        print(f"Tagged v{version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
