"""The release gate: a version passes only when every stage passes.

Stages, in order (each one adds findings; critical and high findings block unless waived with a reason):

  1. version   src/version.py, pyproject.toml and the top of CHANGELOG.md agree; the version is semantic and newer
               than the last released tag (v<version>).
  2. syntax    every Python file compiles; every JavaScript file parses (JavaScriptCore on macOS, or Node).
  3. scan      a line-by-line static scan of every source file: secrets, merge markers, leftover debuggers,
               dangerous calls (a shell, eval, pickle, unchecked TLS), swallowed errors, unescaped HTML in the
               front end, machine-specific paths, junk files in the repository. A line can opt out of one
               rule with a trailing comment naming it and the reason: # gate: ignore[PY2] constant command
  4. tests     the whole test suite; a failure blocks unless it is listed as known (config/gate_known_failures.txt).
  5. review    an expert review by a language model, line by line, of every file changed since the last release
               (or every file with --full): numbered lines go in, findings come back with the exact line quoted.
               A finding whose quote is not found at that line is marked unverified and never blocks.
  6. eval      the exam-quality evaluation set (scripts/exam_eval.py): the latest run must not have regressed.
  7. links     real links still download, checked the way the app downloads them (saved cookies, the retry from the
               embedding site for Vimeo) without downloading: one URL per line in <outputs>/gate/links.txt (kept on
               this machine, since course links are private). A link that stopped working blocks.

The report (Markdown and JSON) is written to <outputs>/gate/. With --tag, a passing gate tags the commit v<version>.
"""

from __future__ import annotations

import concurrent.futures as futures
import fnmatch
import hashlib
import json
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

SEVERITIES = ("critical", "high", "medium", "low", "info")
BLOCKING = ("critical", "high")
SOURCE_EXT = {".py", ".js", ".html", ".css", ".json", ".md", ".sh", ".bat", ".ps1", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".ini"}
CODE_DIRS = ("src/", "scripts/", "tests/", "installer/", "run_gui.py")
REVIEW_EXT = {".py", ".js", ".html", ".css"}
REVIEW_PROMPT_VERSION = "2"
WINDOW = 320            # lines reviewed per request (the model sees the whole file, or a wide margin, around them)
FULL_CONTEXT_LINES = 900  # files up to this size are sent whole with every part
MAX_FILE_BYTES = 400_000
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


@dataclass
class Finding:
    stage: str
    severity: str
    rule: str
    message: str
    file: str = ""
    line: int = 0
    evidence: str = ""
    fix: str = ""
    verified: bool = True
    waived: str = ""

    @property
    def blocks(self) -> bool:
        return self.severity in BLOCKING and self.verified and not self.waived

    def where(self) -> str:
        return f"{self.file}:{self.line}" if self.file and self.line else self.file


@dataclass
class StageResult:
    name: str
    status: str = "pass"     # pass | fail | skipped
    seconds: float = 0.0
    notes: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers

def _run(cmd: Sequence[str], cwd: Path, timeout: int = 60) -> Tuple[int, str]:
    try:
        out = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def repo_files(root: Path) -> List[str]:
    """Tracked files plus new files that are not ignored: what the next commit would contain."""
    code, out = _run(["git", "-c", "core.quotepath=off", "ls-files", "--cached", "--others", "--exclude-standard"], root)
    if code != 0:
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    return sorted({line for line in out.splitlines() if line and (root / line).is_file()})


def _text(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _semver(text: str) -> Optional[Tuple[int, int, int]]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(x) for x in match.groups()) if match else None  # type: ignore[return-value]


def last_release_tag(root: Path) -> Optional[str]:
    code, out = _run(["git", "tag", "--list", "v*", "--sort=-v:refname"], root)
    tags = [t for t in out.splitlines() if _semver(t[1:] if t.startswith("v") else "")]
    return tags[0] if code == 0 and tags else None


# --------------------------------------------------------------------------- waivers

def load_waivers(root: Path) -> List[Dict[str, str]]:
    try:
        data = json.loads((root / "config" / "gate_waivers.json").read_text(encoding="utf-8"))
        return [w for w in data.get("waivers", []) if w.get("reason")]
    except (OSError, ValueError):
        return []


def apply_waivers(findings: Iterable[Finding], waivers: Sequence[Dict[str, str]]) -> None:
    for finding in findings:
        for waiver in waivers:
            if waiver.get("rule") and waiver["rule"] != finding.rule:
                continue
            if waiver.get("file") and not fnmatch.fnmatch(finding.file, waiver["file"]):
                continue
            if waiver.get("contains") and waiver["contains"] not in (finding.evidence + " " + finding.message):
                continue
            finding.waived = waiver["reason"]
            break


# --------------------------------------------------------------------------- 1. version

def stage_version(root: Path) -> StageResult:
    stage = StageResult("version")
    source = _text(root / "src" / "version.py") or ""
    app = re.search(r'__version__\s*=\s*"([^"]+)"', source)
    project = re.search(r'^version\s*=\s*"([^"]+)"', _text(root / "pyproject.toml") or "", re.M)
    changelog = re.search(r"^##\s*\[?v?(\d+\.\d+\.\d+)\]?", _text(root / "CHANGELOG.md") or "", re.M)
    version = app.group(1) if app else ""
    stage.notes.append(f"version {version or '?'}")
    if not app or not _semver(version):
        stage.findings.append(Finding("version", "critical", "VER1", "src/version.py has no semantic version (x.y.z)", "src/version.py"))
        return stage
    if not project or project.group(1) != version:
        stage.findings.append(Finding("version", "high", "VER2", f"pyproject.toml says {project.group(1) if project else 'nothing'}, the app says {version}",
                                      "pyproject.toml", fix="Set the same version in both, or run the gate with --bump."))
    if not changelog or changelog.group(1) != version:
        stage.findings.append(Finding("version", "high", "VER3", f"CHANGELOG.md has no entry for {version} at the top", "CHANGELOG.md",
                                      fix=f"Add a '## [{version}] - <date>' section describing the release."))
    tag = last_release_tag(root)
    if tag:
        stage.notes.append(f"last release {tag}")
        if _semver(version) < _semver(tag[1:]):  # type: ignore[operator]
            stage.findings.append(Finding("version", "high", "VER4", f"{version} is older than the last release {tag}"))
        elif _semver(version) == _semver(tag[1:]):
            code, tagged = _run(["git", "rev-list", "-n", "1", tag], root)
            _, head = _run(["git", "rev-parse", "HEAD"], root)
            if tagged.strip() != head.strip():
                stage.findings.append(Finding("version", "high", "VER5", f"{tag} is already released from another commit; bump the version",
                                              fix="python -m scripts.gate --bump patch"))
    return stage


def bump(root: Path, part: str, note: str = "") -> str:
    """Raise the version in src/version.py and pyproject.toml and open a CHANGELOG entry. Returns the new version."""
    path = root / "src" / "version.py"
    current = re.search(r'__version__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8")).group(1)  # type: ignore[union-attr]
    major, minor, patch = _semver(current) or (0, 0, 0)
    new = {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[part]
    path.write_text(re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new}"', path.read_text(encoding="utf-8")), encoding="utf-8")
    pyproject = root / "pyproject.toml"
    pyproject.write_text(re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{new}"', pyproject.read_text(encoding="utf-8"), count=1, flags=re.M),
                         encoding="utf-8")
    changelog = root / "CHANGELOG.md"
    text = _text(changelog) or "# Changelog\n"
    entry = f"## [{new}] - {datetime.now():%Y-%m-%d}\n\n- {note or 'Describe this release.'}\n\n"
    head, sep, rest = text.partition("\n## ")
    changelog.write_text(f"{head.rstrip()}\n\n{entry}" + (f"## {rest}" if sep else ""), encoding="utf-8")
    return new


# --------------------------------------------------------------------------- 2. syntax

def stage_syntax(root: Path, files: Sequence[str]) -> StageResult:
    stage = StageResult("syntax")
    python = [f for f in files if f.endswith(".py")]
    for name in python:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                py_compile.compile(str(root / name), cfile=str(Path(tmp) / "x.pyc"), doraise=True)
        except py_compile.PyCompileError as exc:
            line = getattr(exc.exc_value, "lineno", 0) or 0
            stage.findings.append(Finding("syntax", "critical", "SYN1", f"Python does not compile: {exc.exc_value}", name, line))
    scripts = [f for f in files if f.endswith(".js")]
    checker = None
    if Path(JSC).exists():
        checker = "jsc"
    elif shutil.which("node"):
        checker = "node"
    for name in scripts:
        if checker == "jsc":
            code, out = _run([JSC, "-e", f"checkSyntax({json.dumps(str(root / name))})"], root)
        elif checker == "node":
            code, out = _run(["node", "--check", str(root / name)], root)
        else:
            break
        if code != 0:
            line = re.search(r":(\d+)", out)
            stage.findings.append(Finding("syntax", "critical", "SYN2", f"JavaScript does not parse: {out.strip().splitlines()[0][:200]}",
                                          name, int(line.group(1)) if line else 0))
    stage.notes.append(f"{len(python)} Python files, {len(scripts)} JavaScript files" + ("" if checker else " (JavaScript not checked: no jsc or node)"))
    return stage


# --------------------------------------------------------------------------- 3. line-by-line scan

@dataclass
class Rule:
    id: str
    severity: str
    pattern: "re.Pattern[str]"
    message: str
    fix: str = ""
    ext: Tuple[str, ...] = ()           # empty = every text file
    paths: Tuple[str, ...] = CODE_DIRS  # path prefixes the rule applies to; () = everywhere
    skip_tests: bool = False


SECRET_RULES = [
    Rule("SEC1", "critical", re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "Google API key in the source", "Move it to .env (ignored) and rotate the key.", paths=()),
    Rule("SEC2", "critical", re.compile(r"\b(ghp|gho|ghu|ghs)_[A-Za-z0-9]{36}\b|github_pat_[A-Za-z0-9_]{60,}"), "GitHub token in the source", "Revoke it on GitHub.", paths=()),
    Rule("SEC3", "critical", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"), "Private key in the source", paths=()),
    Rule("SEC4", "critical", re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}\b|\bsk_[a-f0-9]{48}\b"), "API secret key (OpenAI / ElevenLabs style) in the source", paths=()),
    Rule("SEC5", "critical", re.compile(r"GOCSPX-[A-Za-z0-9_\-]{20,}"), "Google OAuth client secret in the source", "Keep the client file out of git.", paths=()),
    Rule("SEC6", "critical", re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"), "Slack token in the source", paths=()),
    Rule("GEN4", "critical", re.compile(r"^(<<<<<<< |>>>>>>> |=======$)"), "Merge conflict marker", paths=()),
]
CODE_RULES = [
    Rule("DBG1", "high", re.compile(r"^\s*(breakpoint\(\)|import pdb\b|pdb\.set_trace\(\))"), "Debugger left in the code", ext=(".py",)),
    Rule("DBG2", "high", re.compile(r"^\s*debugger\s*;?\s*$"), "Debugger statement left in the code", ext=(".js", ".html")),
    Rule("PY1", "medium", re.compile(r"^\s*except\s*:"), "Bare except also catches KeyboardInterrupt and SystemExit",
         "Catch Exception (or the specific errors) instead.", ext=(".py",)),
    Rule("PY2", "high", re.compile(r"shell\s*=\s*True"), "subprocess through a shell: injection risk when any part is not a constant",
         "Pass a list of arguments without the shell.", ext=(".py",), skip_tests=True),
    Rule("PY3", "high", re.compile(r"(?<![\w.])(eval|exec)\s*\("), "eval/exec runs arbitrary code", ext=(".py",), skip_tests=True),
    Rule("PY4", "high", re.compile(r"\bpickle\.loads?\(|\byaml\.load\((?![^)]*SafeLoader)"), "Unsafe deserialisation",
         "Use JSON or yaml.safe_load.", ext=(".py",), skip_tests=True),
    Rule("PY5", "high", re.compile(r"verify\s*=\s*False"), "TLS certificate checking turned off", ext=(".py",), skip_tests=True),
    Rule("GEN1", "low", re.compile(r"\b(TODO|FIXME|XXX|HACK)\b"), "Unfinished work marked in the code", skip_tests=True),
    Rule("GEN2", "medium", re.compile(r"(/Users/[a-z][\w.-]+/|[A-Za-z]:\\\\Users\\\\)"), "A path from one machine is hard-coded",
         "Build paths from the project root or the user's home.", ext=(".py", ".js"), skip_tests=True),
    Rule("JS2", "low", re.compile(r"\bconsole\.log\("), "console.log left in the front end", ext=(".js",), paths=("src/web/static/",)),
]
# Values that come from a model, a file or the user, shown in HTML: they must pass through esc()
_TEMPLATE_EXPR = re.compile(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
_DATA_FIELD = re.compile(r"\.(title|text|stem|name|message|error|explanation|feedback|model_answer|point|description|filename|summary|definition)\b(?!\s*\()")
_SAFE_CALL = re.compile(r"^\s*(esc|enc|encodeURIComponent|Number|Math\.\w+|pct|fmt\w*|whenLabel|JSON\.stringify|String\(\w+\.length)\b")


_IGNORE = re.compile(r"gate:\s*ignore\[([A-Z0-9, ]+)\]")


def _ignored(line: str, rule: str) -> bool:
    """`# gate: ignore[PY2] reason` on the line itself opts it out of that rule (never of a secret)."""
    match = _IGNORE.search(line)
    return bool(match) and rule in [r.strip() for r in match.group(1).split(",")]


def scan_lines(name: str, text: str) -> List[Finding]:
    findings: List[Finding] = []
    ext = Path(name).suffix
    in_tests = name.startswith("tests/")
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        for rule in SECRET_RULES + CODE_RULES:
            if rule.ext and ext not in rule.ext:
                continue
            if rule.paths and not name.startswith(rule.paths):
                continue
            if rule.skip_tests and in_tests:
                continue
            if rule.pattern.search(line) and not (rule.severity != "critical" and _ignored(line, rule.id)):
                findings.append(Finding("scan", rule.severity, rule.id, rule.message, name, number, line.strip()[:200], rule.fix))
        if ext == ".py" and re.match(r"^\s*except(\s+Exception)?(\s+as\s+\w+)?\s*:\s*(pass)?\s*$", line):
            following = line.split(":", 1)[1].strip() or (lines[number].strip() if number < len(lines) else "")
            if following == "pass" and not in_tests:
                findings.append(Finding("scan", "medium", "PY6", "An error is swallowed without a log line", name, number,
                                        line.strip(), "Log it (logger.debug at least) or narrow the exception."))
        builds_html = "<" in line or "innerHTML" in line or "insertAdjacentHTML" in line
        if ext == ".js" and name.startswith("src/web/static/") and "${" in line and builds_html and not _ignored(line, "JS1"):
            for expr in _TEMPLATE_EXPR.findall(line):
                if _DATA_FIELD.search(expr) and not _SAFE_CALL.match(expr) and "esc(" not in expr:
                    findings.append(Finding("scan", "medium", "JS1", f"Value shown in HTML without esc(): ${{{expr.strip()[:80]}}}",
                                            name, number, line.strip()[:200], "Wrap it in esc() unless it is known to be safe."))
    return findings


def stage_scan(root: Path, files: Sequence[str]) -> StageResult:
    stage = StageResult("scan")
    scanned = lines = 0
    for name in files:
        lower = name.lower()
        if lower.endswith((".env", ".pem", ".p12", ".key")) or Path(name).name in (".env", "client_secret.json"):
            stage.findings.append(Finding("scan", "critical", "SEC7", "A secrets file is in the repository", name))
        if "c__users_" in lower or "\\" in name or lower.endswith(".log"):
            stage.findings.append(Finding("scan", "low", "GEN5", "A stray file (editor attachment or log) is in the repository", name,
                                          fix="git rm --cached it and add it to .gitignore."))
        if not (root / name).is_file():
            continue
        size = (root / name).stat().st_size
        if size > 5_000_000:
            stage.findings.append(Finding("scan", "low", "GEN6", f"Large file in the repository ({size // 1_000_000} MB)", name))
        if Path(name).suffix.lower() not in SOURCE_EXT:
            continue
        text = _text(root / name)
        if text is None:
            continue
        if Path(name).suffix == ".json" and '"client_secret"' in text and not name.startswith("tests/"):
            stage.findings.append(Finding("scan", "critical", "SEC5", "An OAuth client file with its secret is in the repository", name))
        scanned += 1
        lines += text.count("\n") + 1
        stage.findings.extend(scan_lines(name, text))
    stage.notes.append(f"{scanned} files, {lines:,} lines scanned")
    return stage


# --------------------------------------------------------------------------- 4. tests

def node_id(root: Path, classname: str, name: str) -> str:
    """pytest's id (tests/test_x.py::Class::test) from a JUnit classname (tests.test_x.Class) and test name."""
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        path = "/".join(parts[:cut]) + ".py"
        if (root / path).exists():
            return "::".join([path, *parts[cut:], name])
    return f"{classname}::{name}"


def stage_tests(root: Path, python: str = sys.executable, timeout: int = 1800) -> StageResult:
    stage = StageResult("tests")
    known_path = root / "config" / "gate_known_failures.txt"
    known = {l.split("#")[0].strip() for l in (_text(known_path) or "").splitlines() if l.split("#")[0].strip()}
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "junit.xml"
        code, out = _run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}"], root, timeout)
        if not report.exists():
            stage.findings.append(Finding("tests", "critical", "TST0", f"The test suite did not run: {out.strip()[-300:]}"))
            return stage
        tree = ET.parse(report)
    failed, total, skipped = [], 0, 0
    for case in tree.iter("testcase"):
        total += 1
        if case.find("skipped") is not None:
            skipped += 1
        problem = case.find("failure") if case.find("failure") is not None else case.find("error")
        if problem is not None:
            failed.append((node_id(root, case.get("classname", ""), case.get("name", "")), (problem.get("message") or "")[:200]))
    new, still_known = [], 0
    for test_id, message in failed:
        if test_id in known:
            still_known += 1
            continue
        new.append(Finding("tests", "critical", "TST1", f"Test fails: {message}", test_id.split("::")[0], 0, test_id))
    fixed = len(known) - still_known
    stage.findings.extend(new)
    stage.notes.append(f"{total} tests: {total - len(failed) - skipped} passed, {len(failed)} failed "
                       f"({still_known} known legacy failures), {skipped} skipped")
    if fixed > 0:
        stage.notes.append(f"{fixed} known failure(s) now pass: remove them from config/gate_known_failures.txt")
    return stage


# --------------------------------------------------------------------------- 5. expert review

REVIEW_SYSTEM = """You are a world-class principal software engineer and application-security expert reviewing a release.
You read the code LINE BY LINE, the way a top reviewer at a leading company would, and report only real defects:
bugs, security holes, data loss, race conditions, resource leaks, broken error handling, wrong edge cases,
accessibility or right-to-left (Hebrew) UI defects, and performance traps. No style nits, no praise, no
speculation without a concrete failure scenario. Reply with JSON only."""

APP_CONTEXT = """About the application: M.B.S Studio, a local web app (FastAPI + vanilla JavaScript, Hebrew RTL UI) that turns
lecture recordings into podcasts, transcripts, study summaries and exams. It runs on the user's own computer
(macOS or Windows) for one user; every /api route requires a per-launch token; keys (Gemini, ElevenLabs, Google)
live in a git-ignored .env. Long jobs run in background threads; JSON files under outputs/ are the database.
Model output and file contents are untrusted and must be escaped with esc() before being shown as HTML."""


def review_prompt(name: str, lines: Sequence[str], first: int, last: int) -> str:
    """Lines first..last of the file are under review; the rest of what is shown is context only."""
    if len(lines) <= FULL_CONTEXT_LINES:
        shown_from, shown_to, note = 1, len(lines), "The WHOLE file is shown."
    else:
        shown_from, shown_to = max(1, first - 150), min(len(lines), last + 150)
        note = (f"Only lines {shown_from}-{shown_to} of {len(lines)} are shown (plus the file's first 40 lines): "
                "names, returns and imports outside them exist unless you can see otherwise.")
    head = "\n".join(f"{n:5d}| {lines[n - 1]}" for n in range(1, min(41, shown_from)))
    body = "\n".join(f"{n:5d}| {lines[n - 1]}" for n in range(shown_from, shown_to + 1))
    return f"""{APP_CONTEXT}

File: {name}. {note}
REVIEW ONLY LINES {first}-{last}, line by line; everything else is context. Names imported from other modules of this
project exist and work as their names say: never report a missing import, an undefined name, a missing return or a
model name you do not recognise unless the code shown proves it.
Severity: critical = exploitable security hole or certain data loss; high = a bug users will hit, or a security
weakness; medium = a real but narrower bug or robustness gap; low = minor defect.

For each finding give the line number and quote that line EXACTLY as "evidence" (without the line-number prefix).
Return JSON: {{"findings": [{{"line": 12, "severity": "high", "category": "correctness|security|data-loss|concurrency|
error-handling|performance|ux-rtl|accessibility", "title": "short", "evidence": "the exact code on that line",
"failure": "the concrete scenario that goes wrong", "fix": "the specific change"}}]}}
Return {{"findings": []}} when these lines are sound.

{('FILE START (context):' + chr(10) + head + chr(10) + '...' + chr(10)) if head else ''}CODE:
{body}"""


VERIFY_SYSTEM = """You are the second reviewer on a release: a sceptical, world-class engineer. Another reviewer reported a
defect. Decide, from the code alone, whether it is REAL: the failure must actually happen with this code as written.
Reject findings that rely on code not shown, on names that are defined elsewhere, on library behaviour that is not
true, or that are style preferences. Reply with JSON only."""


def verify_prompt(name: str, lines: Sequence[str], finding: "Finding") -> str:
    if len(lines) <= FULL_CONTEXT_LINES:
        span = range(1, len(lines) + 1)
    else:
        span = range(max(1, finding.line - 200), min(len(lines), finding.line + 200) + 1)
    code = "\n".join(f"{n:5d}| {lines[n - 1]}" for n in span)
    return f"""{APP_CONTEXT}

REPORTED in {name}, line {finding.line} ({finding.severity}): {finding.message}
Code on that line: {finding.evidence}
Suggested fix: {finding.fix}

Is this a real defect in the code below? Return JSON: {{"real": true|false, "severity": "critical|high|medium|low",
"reason": "one or two sentences"}}

CODE ({name}):
{code}"""


def _norm_code(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^\s*\d+\|\s?", "", text or "")).strip()


def verify_finding(lines: Sequence[str], line: int, evidence: str) -> Tuple[bool, int]:
    """Is the quoted code really at (or within 3 lines of) the reported line? Returns (verified, corrected line)."""
    quote = _norm_code(evidence)
    if len(quote) < 3:
        return False, line
    for offset in (0, -1, 1, -2, 2, -3, 3):
        index = line - 1 + offset
        if 0 <= index < len(lines):
            actual = _norm_code(lines[index])
            if actual and (quote in actual or (len(actual) > 10 and actual in quote)):
                return True, index + 1
    return False, line


def changed_files(root: Path, base: Optional[str]) -> List[str]:
    names: set = set()
    if base:
        _, out = _run(["git", "-c", "core.quotepath=off", "diff", "--name-only", f"{base}...HEAD"], root)
        names.update(out.splitlines())
    _, out = _run(["git", "-c", "core.quotepath=off", "diff", "--name-only", "HEAD"], root)
    names.update(out.splitlines())
    _, out = _run(["git", "-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard"], root)
    names.update(out.splitlines())
    return sorted(n for n in names if (root / n).is_file())


def review_targets(root: Path, files: Sequence[str], full: bool, base: Optional[str]) -> List[str]:
    pool = files if full else changed_files(root, base)
    return [f for f in pool if Path(f).suffix in REVIEW_EXT and f.startswith(("src/", "scripts/"))
            and not f.startswith("src/web/static/vendor")]


def stage_review(root: Path, targets: Sequence[str], llm: Any, spent: Callable[[], float], budget_usd: float,
                 cache_dir: Path, model: str, workers: int = 4, log: Callable[[str], None] = print) -> StageResult:
    """Two passes: a broad line-by-line sweep (quick thinking), then every finding that would block is checked
    again by a second, sceptical reviewer with the whole file (deep thinking). Only confirmed findings block."""
    stage = StageResult("review")
    cache_dir.mkdir(parents=True, exist_ok=True)
    files: Dict[str, List[str]] = {}
    jobs = []
    for name in targets:
        text = _text(root / name)
        if text is None:
            stage.notes.append(f"skipped {name} (too large or not text)")
            continue
        files[name] = text.splitlines()
        for start in range(0, len(files[name]), WINDOW):
            jobs.append((name, start + 1, min(len(files[name]), start + WINDOW)))
    stage.notes.append(f"{len(files)} files, {sum(len(v) for v in files.values()):,} lines, {len(jobs)} parts, model {model}")

    def cached_call(task: str, system: str, prompt: str, max_tokens: int) -> Tuple[Dict[str, Any], bool]:
        key = hashlib.sha256(f"{REVIEW_PROMPT_VERSION}|{model}|{task}|{prompt}".encode("utf-8")).hexdigest()[:24]
        path = cache_dir / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8")), True
            except ValueError:
                pass
        if spent() >= budget_usd:
            raise BudgetExceeded()
        reply = llm.chat_json(task, system, prompt, max_tokens=max_tokens, temperature=0.1)
        path.write_text(json.dumps(reply, ensure_ascii=False), encoding="utf-8")
        return reply, False

    def sweep(job) -> Tuple[List[Dict[str, Any]], bool]:
        name, first, last = job
        reply, hit = cached_call("gate_review", REVIEW_SYSTEM, review_prompt(name, files[name], first, last), 6000)
        return [f for f in reply.get("findings", []) if isinstance(f, dict)], hit

    done = from_cache = 0
    incomplete: List[str] = []
    with futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        running = {pool.submit(sweep, job): job for job in jobs}
        for future in futures.as_completed(running):
            name, first, last = running[future]
            try:
                found, hit = future.result()
            except BudgetExceeded:
                incomplete.append(f"{name}:{first}")
                continue
            except Exception as exc:  # one failed part must not hide the others
                incomplete.append(f"{name}:{first} ({exc.__class__.__name__})")
                continue
            done += 1
            from_cache += hit
            if done % 10 == 0:
                log(f"review: {done}/{len(jobs)} parts (${spent():.3f})")
            for raw in found:
                severity = str(raw.get("severity", "low")).lower()
                severity = severity if severity in SEVERITIES else "low"
                try:
                    line = int(raw.get("line") or 0)
                except (TypeError, ValueError):
                    line = 0
                verified, line = verify_finding(files[name], line, str(raw.get("evidence") or ""))
                if verified and not first - 3 <= line <= last + 3:
                    continue  # about context lines: another part reviews them
                stage.findings.append(Finding(
                    "review", severity if verified else "info", f"REV:{str(raw.get('category') or 'other')[:20]}",
                    f"{str(raw.get('title') or '')[:140]}. {str(raw.get('failure') or '')[:400]}".strip(". "),
                    name, line, str(raw.get("evidence") or "")[:200], str(raw.get("fix") or "")[:400], verified))

    # Second pass: a sceptical reviewer re-checks each finding that would block
    confirmed = rejected = 0
    for finding in [f for f in stage.findings if f.severity in BLOCKING and f.verified]:
        try:
            reply, hit = cached_call("gate_verify", VERIFY_SYSTEM, verify_prompt(finding.file, files[finding.file], finding), 3000)
        except BudgetExceeded:
            incomplete.append(f"verify {finding.where()}")
            continue
        except Exception as exc:
            incomplete.append(f"verify {finding.where()} ({exc.__class__.__name__})")
            continue
        if reply.get("real") is True:
            confirmed += 1
            severity = str(reply.get("severity") or finding.severity).lower()
            finding.severity = severity if severity in SEVERITIES else finding.severity
            finding.message += f" [confirmed: {str(reply.get('reason') or '')[:200]}]"
        else:
            rejected += 1
            finding.severity = "info"
            finding.message += f" [rejected by the second reviewer: {str(reply.get('reason') or '')[:200]}]"
    stage.notes.append(f"{done} parts reviewed ({from_cache} from the cache); second reviewer confirmed {confirmed}, "
                       f"rejected {rejected}; ${spent():.3f} spent")
    if incomplete:
        stage.findings.append(Finding("review", "high", "REV0", f"{len(incomplete)} part(s) were not reviewed (budget or errors): "
                                      + ", ".join(incomplete[:8]), fix="Raise --budget or run the gate again (finished parts are cached)."))
    return stage


class BudgetExceeded(RuntimeError):
    pass


# --------------------------------------------------------------------------- 6. evaluation set

def stage_eval(history_path: Path, version: str) -> StageResult:
    stage = StageResult("eval")
    from ..engine.exam_eval import read_history

    runs = read_history(history_path)
    if not runs:
        stage.notes.append("no evaluation run yet (python -m scripts.exam_eval --init, then --yes)")
        stage.findings.append(Finding("eval", "low", "EVAL0", "The exam evaluation set has never been run"))
        return stage
    last = runs[-1]
    avg = last.get("average", {})
    stage.notes.append(f"latest run {last['date'][:16]} (app {last.get('app_version', '?')}, {len(last.get('lessons', []))} lessons): "
                       f"keep {avg.get('keep_rate', 0):.0%}, first pass {avg.get('first_pass', 0):.0%}, "
                       f"length cue {avg.get('length_cue', 0):.0%}, ${avg.get('cost_per_question', 0):.4f}/question")
    for item in last.get("regressions") or []:
        stage.findings.append(Finding("eval", "high", "EVAL1", f"Exam quality regressed: {item}"))
    if last.get("app_version") != version:
        stage.findings.append(Finding("eval", "low", "EVAL2", f"The latest evaluation ran on {last.get('app_version') or '?'}, not {version}",
                                      fix="python -m scripts.exam_eval --yes"))
    return stage


# --------------------------------------------------------------------------- 7. live links

def read_links(path: Path) -> List[str]:
    return [line.strip() for line in (_text(path) or "").splitlines()
            if line.strip().startswith("http") and not line.strip().startswith("#")]


def stage_links(links_path: Path, cookie_dir: Path, probe: Optional[Callable[..., Dict[str, Any]]] = None) -> StageResult:
    stage = StageResult("links")
    links = read_links(links_path)
    if not links:
        stage.notes.append(f"no links to check (add one URL per line to {links_path})")
        return stage
    if probe is None:
        from ..media.downloader import probe
    cookies = sorted(cookie_dir.glob("*.txt")) if cookie_dir.exists() else []
    ok = 0
    for url in links:
        short = url.split("?")[0][:90]
        try:
            result = probe(url, cookies)
        except Exception as exc:  # the probe itself failed (no network, missing package): report, never crash the gate
            result = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        if result.get("ok"):
            ok += 1
            via = f"cookies {result['cookies']}" if result.get("cookies") else "no cookies"
            via += f", embedded on {result['referer']}" if result.get("referer") else ""
            stage.notes.append(f"OK {short}: {result.get('title', '')[:60]} ({result.get('minutes', 0)} min; {via})")
        else:
            stage.findings.append(Finding("links", "high", "LNK1", f"The link no longer downloads: {result.get('error', '')[:300]}",
                                          short, fix="Refresh the site's cookies in Tools, or check the link in the browser."))
    stage.notes.insert(0, f"{ok} of {len(links)} links download")
    return stage


# --------------------------------------------------------------------------- report

def finish(stages: List[StageResult]) -> bool:
    ok = True
    for stage in stages:
        if stage.status == "skipped":
            continue
        stage.status = "fail" if any(f.blocks for f in stage.findings) else "pass"
        ok = ok and stage.status == "pass"
    return ok


def render_report(version: str, stages: List[StageResult], passed: bool, started: datetime, commit: str) -> str:
    lines = [f"# Release gate: v{version}", "",
             f"**Result: {'PASSED' if passed else 'FAILED'}**  ·  {started:%Y-%m-%d %H:%M}  ·  commit `{commit[:10]}`", "",
             "| Stage | Result | Blocking | All findings | Time | Notes |", "|---|---|---|---|---|---|"]
    for s in stages:
        lines.append(f"| {s.name} | {s.status.upper()} | {sum(f.blocks for f in s.findings)} | {len(s.findings)} | {s.seconds:.0f}s | "
                     f"{'; '.join(s.notes)} |")
    order = {s: i for i, s in enumerate(SEVERITIES)}
    for s in stages:
        if not s.findings:
            continue
        lines += ["", f"## {s.name}", ""]
        for f in sorted(s.findings, key=lambda f: (not f.blocks, order.get(f.severity, 9), f.file, f.line)):
            tag = "BLOCKS" if f.blocks else ("waived: " + f.waived if f.waived else ("unverified" if not f.verified else f.severity))
            lines.append(f"- **{f.severity}** [{f.rule}] `{f.where()}` ({tag}): {f.message}")
            if f.evidence:
                lines.append(f"  - code: `{f.evidence.replace('`', chr(39))}`")
            if f.fix:
                lines.append(f"  - fix: {f.fix}")
    return "\n".join(lines) + "\n"


def write_report(folder: Path, version: str, stages: List[StageResult], passed: bool, started: datetime, commit: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"v{version}-{started:%Y%m%d-%H%M%S}"
    (folder / f"{stem}.json").write_text(json.dumps({
        "version": version, "passed": passed, "commit": commit, "date": started.isoformat(timespec="seconds"),
        "stages": [{**{k: v for k, v in asdict(s).items() if k != "findings"},
                    "findings": [asdict(f) | {"blocks": f.blocks} for f in s.findings]} for s in stages],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    path = folder / f"{stem}.md"
    path.write_text(render_report(version, stages, passed, started, commit), encoding="utf-8")
    return path


def timed(run: Callable[[], StageResult]) -> StageResult:
    started = time.time()
    result = run()
    result.seconds = time.time() - started
    return result
