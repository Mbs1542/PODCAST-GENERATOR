"""The release gate: version agreement, the line scanner, review verification, waivers and the verdict."""

from __future__ import annotations

from pathlib import Path

from src.release import gate as g


def _repo(tmp_path: Path, app="1.2.3", project="1.2.3", changelog="1.2.3") -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "version.py").write_text(f'__version__ = "{app}"\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{project}"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n## [{changelog}] - 2026-01-01\n\n- x\n", encoding="utf-8")
    return tmp_path


def test_version_must_agree_everywhere(tmp_path):
    good = tmp_path / "a"
    good.mkdir()
    assert not g.stage_version(_repo(good)).findings
    bad = tmp_path / "b"
    bad.mkdir()
    rules = {f.rule for f in g.stage_version(_repo(bad, project="1.2.2", changelog="1.1.0")).findings}
    assert rules == {"VER2", "VER3"}


def test_bump_raises_every_place_and_opens_a_changelog_entry(tmp_path):
    root = _repo(tmp_path)
    assert g.bump(root, "minor", "New thing") == "1.3.0"
    assert '"1.3.0"' in (root / "src" / "version.py").read_text() and 'version = "1.3.0"' in (root / "pyproject.toml").read_text()
    changelog = (root / "CHANGELOG.md").read_text()
    assert changelog.index("## [1.3.0]") < changelog.index("## [1.2.3]") and "New thing" in changelog
    assert not g.stage_version(root).findings


def test_line_scanner_finds_real_problems_and_leaves_clean_code_alone():
    fake_key = "AIza" + "B" * 35
    code = "\n".join([
        "import subprocess",
        f'KEY = "{fake_key}"',
        "subprocess.run(cmd, shell=True)",
        "try:",
        "    x = 1",
        "except:",
        "    pass",
        "breakpoint()",
        "value = eval(text)",
        "safe = model.evaluate(x)",
    ])
    rules = [(f.rule, f.line) for f in g.scan_lines("src/web/thing.py", code)]
    assert ("SEC1", 2) in rules and ("PY2", 3) in rules and ("PY1", 6) in rules and ("PY6", 6) in rules
    assert ("DBG1", 8) in rules and ("PY3", 9) in rules and not any(line == 10 for _, line in rules)
    assert not [r for r in g.scan_lines("tests/test_x.py", "subprocess.run(c, shell=True)") if r.rule == "PY2"]

    js = 'box.innerHTML = `<b>${q.stem}</b> ${esc(q.title)} ${q.options.length} ${esc(e.message)}`;'
    flagged = [f.message for f in g.scan_lines("src/web/static/x.js", js)]
    assert len(flagged) == 1 and "q.stem" in flagged[0]


def test_review_findings_count_only_when_the_quoted_line_is_really_there():
    lines = ["def f(x):", "    total = x / 0", "    return total"]
    assert g.verify_finding(lines, 2, "total = x / 0") == (True, 2)
    assert g.verify_finding(lines, 3, "total = x / 0") == (True, 2)  # off by one: corrected
    assert g.verify_finding(lines, 2, "os.remove(path)")[0] is False  # invented: never blocks
    blocking = g.Finding("review", "high", "REV:x", "m", "a.py", 2)
    unverified = g.Finding("review", "high", "REV:x", "m", "a.py", 2, verified=False)
    assert blocking.blocks and not unverified.blocks


def test_review_sweeps_then_a_second_reviewer_confirms_or_rejects_and_results_are_cached(tmp_path):
    root = tmp_path
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("def f(x):\n    return x / 0\n\n\ndef g(y):\n    return y + 1\n", encoding="utf-8")

    class FakeReviewer:
        calls: list = []

        def chat_json(self, task, system, user, max_tokens=0, temperature=0.0):
            FakeReviewer.calls.append(task)
            if task == "gate_review":
                assert "    2| " in user and "LINE BY LINE" in system and "WHOLE file" in user
                return {"findings": [
                    {"line": 2, "severity": "high", "category": "correctness", "title": "Division by zero",
                     "evidence": "return x / 0", "failure": "always raises", "fix": "check x"},
                    {"line": 6, "severity": "high", "category": "correctness", "title": "Overflow",
                     "evidence": "return y + 1", "failure": "imagined", "fix": "-"},
                    {"line": 1, "severity": "critical", "category": "security", "title": "Invented",
                     "evidence": "os.system(x)", "failure": "-", "fix": "-"}]}
            assert task == "gate_verify" and "sceptical" in system
            return {"real": "Division" in user, "severity": "high", "reason": "x / 0 always raises" if "Division" in user else "Python ints do not overflow"}

    run = lambda budget=1.0, spent=0.0, cache="cache": g.stage_review(  # noqa: E731
        root, ["src/a.py"], FakeReviewer(), lambda: spent, budget, tmp_path / cache, "m", workers=1, log=lambda m: None)
    stage = run()
    blocking = [f for f in stage.findings if f.blocks]
    assert [(f.line, f.severity) for f in blocking] == [(2, "high")] and "confirmed" in blocking[0].message
    rejected = next(f for f in stage.findings if f.line == 6)
    assert rejected.severity == "info" and "rejected by the second reviewer" in rejected.message
    assert [f.severity for f in stage.findings if not f.verified] == ["info"]  # an invented quote never blocks
    assert FakeReviewer.calls == ["gate_review", "gate_verify", "gate_verify"]  # only blocking findings are re-checked
    run()
    assert len(FakeReviewer.calls) == 3  # unchanged code: every answer comes from the cache
    over = run(budget=1.0, spent=5.0, cache="cache2")
    assert any(f.rule == "REV0" and f.blocks for f in over.findings)  # out of budget: the gate cannot pass unreviewed


def test_waivers_need_a_reason_and_the_verdict_follows_blocking_findings(tmp_path):
    finding = g.Finding("scan", "high", "PY2", "shell", "src/x.py", 3, "subprocess.run('ls', shell=True)")
    g.apply_waivers([finding], [{"rule": "PY2", "file": "src/*.py", "reason": "constant command, no user input"}])
    assert finding.waived and not finding.blocks
    stage = g.StageResult("scan", findings=[finding, g.Finding("scan", "medium", "PY1", "bare")])
    assert g.finish([stage]) and stage.status == "pass"
    stage.findings.append(g.Finding("scan", "critical", "SEC1", "key"))
    assert not g.finish([stage]) and stage.status == "fail"
    report = g.render_report("1.0.0", [stage], False, __import__("datetime").datetime(2026, 1, 1), "abc")
    assert "FAILED" in report and "BLOCKS" in report and "waived: constant command" in report


def test_node_ids_match_pytest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")
    assert g.node_id(tmp_path, "tests.test_x.TestA", "test_b") == "tests/test_x.py::TestA::test_b"
    assert g.node_id(tmp_path, "tests.test_x", "test_c[p]") == "tests/test_x.py::test_c[p]"
