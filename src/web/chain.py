"""The download -> transcription -> podcast chain: shared helpers.

  resolve_save_folder   a folder the user picked for downloads (local use only)
  pick_folder           the system's own "choose folder" dialog, on the computer running the app
  combine_transcripts   several lessons as one transcript, every cue kept, times running on
  recommend_length      how long the episode should be, from the engine's own rule
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..engine.planner import BRIEF_MINUTES, target_minutes
from ..engine.transcript import LECTURE_WORDS_PER_MINUTE, _CUE_RE, _to_seconds
from ..transcribe.whisper import format_timestamp

PART_GAP_SECONDS = 2.0
_TAG_RE = re.compile(r"<[^>]+>")
# Folders a download must never be written into (the user's own folders and external drives are fine)
_SYSTEM_DIRS = ("/System", "/bin", "/sbin", "/usr", "/etc", "/Library", "/private", "/dev", "/proc", "/boot")
_USER_DIRS_INSIDE_SYSTEM = ("/private/tmp", "/private/var/folders")  # macOS temporary folders belong to the user


class ChainError(ValueError):
    """A message written for the user."""


# --------------------------------------------------------------------------- folders

def resolve_save_folder(raw: str) -> Path:
    """Validate (and create) a folder the user chose for downloads."""
    text = str(raw or "").strip()
    if not text:
        raise ChainError("לא נבחרה תיקייה")
    folder = Path(os.path.expanduser(text))
    if not folder.is_absolute():
        raise ChainError("צריך נתיב מלא לתיקייה (למשל /Users/me/Downloads/קורס)")
    folder = folder.resolve()
    system = str(folder).startswith(_SYSTEM_DIRS) and not str(folder).startswith(_USER_DIRS_INSIDE_SYSTEM)
    if folder == Path(folder.anchor) or (os.name != "nt" and system) \
            or (os.name == "nt" and str(folder).lower().startswith(os.environ.get("WINDIR", r"C:\Windows").lower())):
        raise ChainError("אי אפשר לשמור בתיקיית מערכת. בחר תיקייה אחרת.")
    if folder.exists() and not folder.is_dir():
        raise ChainError("הנתיב שנבחר הוא קובץ, לא תיקייה")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".mbs-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ChainError(f"אין הרשאה לכתוב בתיקייה הזו ({exc.strerror or exc}).") from exc
    return folder


def pick_folder(start: Optional[str] = None, timeout: float = 600) -> Optional[str]:
    """Open the system folder dialog; returns the chosen path, None if cancelled.

    Raises ChainError when this computer has no dialog to show (a container or a server).
    """
    start_dir = start if start and Path(start).is_dir() else str(Path.home())
    if sys.platform == "darwin":
        script = ("activate\n" f'set chosen to choose folder with prompt "בחר תיקייה לשמירת ההורדות" '
                  f'default location (POSIX file "{_applescript_quote(start_dir)}")\n'
                  'POSIX path of chosen')
        command = ["osascript", "-e", script]
    elif os.name == "nt":
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$d.Description = 'בחר תיקייה לשמירת ההורדות';"
              f"$d.SelectedPath = '{start_dir.replace(chr(39), chr(39) * 2)}';"
              "$d.ShowNewFolderButton = $true;"
              "if ($d.ShowDialog() -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; $d.SelectedPath }")
        command = ["powershell", "-NoProfile", "-STA", "-Command", ps]
    elif _has("zenity"):
        command = ["zenity", "--file-selection", "--directory", f"--filename={start_dir}/"]
    else:
        raise ChainError("אין במחשב הזה חלון לבחירת תיקייה. הקלד את הנתיב בשדה.")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChainError("לא הצלחתי לפתוח את חלון בחירת התיקייה. הקלד את הנתיב בשדה.") from exc
    chosen = result.stdout.strip()
    return chosen.rstrip("/\\") or None if result.returncode == 0 and chosen else None


def reveal(path: Path) -> None:
    """Show a file or folder in Finder / Explorer."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    elif os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{path}"] if path.is_file() else ["explorer", str(path)])
    elif _has("xdg-open"):
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
    else:
        raise ChainError("אין במחשב הזה סייר קבצים לפתוח")


def _applescript_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _has(program: str) -> bool:
    from shutil import which

    return which(program) is not None


# --------------------------------------------------------------------------- transcripts

def read_cues(raw: str) -> List[Tuple[float, float, str]]:
    """Every SRT/VTT cue as (start, end, text), nothing merged or dropped."""
    cues: List[Tuple[float, float, str]] = []
    current: Optional[list] = None
    for line in raw.splitlines():
        match = _CUE_RE.match(line)
        if match:
            current = [_to_seconds(match.group(1)), _to_seconds(match.group(2)), ""]
            cues.append(current)
            continue
        stripped = _TAG_RE.sub("", line).strip()
        if not stripped:
            current = None
        elif current is not None:
            current[2] = f"{current[2]} {stripped}".strip()
    return [(s, e, t) for s, e, t in cues if t]


def transcript_stats(raw: str) -> Dict[str, float]:
    cues = read_cues(raw)
    words = sum(len(t.split()) for _, _, t in cues) if cues else len(raw.split())
    seconds = max((e for _, e, _ in cues), default=0.0)
    minutes = seconds / 60 if seconds else words / LECTURE_WORDS_PER_MINUTE
    return {"minutes": round(minutes, 1), "words": words}


def combine_transcripts(parts: Sequence[Tuple[str, str]], language: str = "he") -> str:
    """Several lessons as one SRT, in the given order.

    Every cue of every part is kept with its text untouched; later parts are shifted to start after
    the previous one, and a short marker cue names each part so the analysis sees where lessons begin.
    """
    if len(parts) == 1:
        return parts[0][1]
    blocks: List[str] = []
    offset = 0.0
    total = len(parts)
    for number, (title, raw) in enumerate(parts, start=1):
        cues = read_cues(raw)
        if not cues:  # a plain-text transcript: give it a time span from its length
            words = len(raw.split())
            cues = [(0.0, max(1.0, words / LECTURE_WORDS_PER_MINUTE * 60), " ".join(raw.split()))]
        marker = (f"חלק {number} מתוך {total}: {title}." if language != "en" else f"Part {number} of {total}: {title}.")
        blocks.append(_cue(len(blocks) + 1, offset, offset + 1.0, marker))
        start = offset + 1.0
        for cue_start, cue_end, text in cues:
            blocks.append(_cue(len(blocks) + 1, start + cue_start, start + cue_end, text))
        offset = start + cues[-1][1] + PART_GAP_SECONDS
    return "\n".join(blocks)


def _cue(number: int, start: float, end: float, text: str) -> str:
    return f"{number}\n{format_timestamp(start)} --> {format_timestamp(max(end, start + 0.01))}\n{text}\n"


# --------------------------------------------------------------------------- length

def _minutes_he(minutes: float) -> str:
    return "פחות מדקה" if minutes < 1 else f"{minutes:.0f} דקות"


def recommend_length(lecture_minutes: float, files: int = 1) -> Dict[str, object]:
    """The episode length to suggest, using the engine's own rule (about 30% of the lecture, 6-30 min)."""
    recommended = target_minutes(lecture_minutes)
    choices = sorted({max(2.0, round(recommended * 0.5)), round(recommended), min(60.0, round(recommended * 1.5))})
    if lecture_minutes <= 0:
        reason = "לא ידוע אורך ההקלטה, ולכן ההמלצה לפי ברירת המחדל."
    elif lecture_minutes * 0.3 > 30:
        reason = (f"ההקלטה ארוכה ({_minutes_he(lecture_minutes)}), ולכן ההמלצה נעצרת ב-30 דקות. "
                  "לכיסוי מלא אפשר לבחור ארוך יותר, או לפצל לכמה פרקים.")
    elif lecture_minutes * 0.3 < recommended:
        reason = (f"ההקלטה קצרה ({_minutes_he(lecture_minutes)}), ולכן פרק של כ-{recommended:g} דקות: "
                  "לא מותחים אותו מעבר לזה, כי מתיחה היא המקום שבו נכנס תוכן מומצא.")
    else:
        reason = f"כ-30% מאורך {'ההקלטות' if files > 1 else 'ההקלטה'} ({_minutes_he(lecture_minutes)}), כמו שהמנוע מחשב במצב אוטומטי."
    return {
        "lecture_minutes": round(lecture_minutes, 1),
        "files": files,
        "recommended": recommended,
        "choices": choices,
        "brief": BRIEF_MINUTES,
        "reason": reason,
    }
