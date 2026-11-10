"""Projects: one folder per lesson under <outputs>/projects, described by project.json.

The folder is laid out by RunPaths (the same layout the CLI pipeline uses), so the
video pipeline can run on a project and reuse its script and audio.
"""

from __future__ import annotations

import json
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.storage import RunPaths

MAX_LOG_LINES = 400

# Where a project is in its life; the UI shows the next action for each
STATUSES = (
    "new",            # created, nothing run yet
    "transcribed",    # transcript ready, plan not built (e.g. Azure not configured yet)
    "plan_ready",     # stages 1-3 done; plan waits for review
    "script_ready",   # stages 4-5 done
    "audio_ready",    # podcast MP3 ready
    "video_ready",    # video ready
    "error",
)


@dataclass
class Project:
    id: str
    title: str
    date: str
    created: str
    source_kind: str               # media | transcript | url
    source_name: str
    options: Dict[str, Any] = field(default_factory=dict)  # format, minutes, voice
    status: str = "new"
    running: Optional[str] = None  # job kind while a job runs
    stage: str = ""                # human-readable current step
    progress: Optional[float] = None
    error: str = ""
    notice: str = ""
    log: List[str] = field(default_factory=list)


class ProjectStore:
    def __init__(self, outputs_dir: Path) -> None:
        self.root = Path(outputs_dir) / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ paths

    def run_paths(self, project: Project) -> RunPaths:
        return RunPaths(self.root, project.date, project.title)

    def folder(self, project: Project) -> Path:
        return self.root / project.id

    def source_dir(self, project: Project) -> Path:
        return self.folder(project) / "source"

    def engine_dir(self, project: Project) -> Path:
        return self.folder(project) / "engine"

    def transcript_path(self, project: Project) -> Optional[Path]:
        """The text the engine reads: a transcription result, or the uploaded transcript."""
        folder = self.folder(project)
        for name in ("transcript.srt", "transcript.vtt", "transcript.txt", "transcript.md"):
            if (folder / name).exists():
                return folder / name
        return None

    def lesson_metadata_path(self, project: Project) -> Path:
        return self.folder(project) / "lesson.json"

    # ------------------------------------------------------------------ CRUD

    def create(self, title: str, source_kind: str, source_name: str, options: Dict[str, Any]) -> Project:
        with self._lock:
            date = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            paths = RunPaths(self.root, date, title)
            project = Project(
                id=paths.run_dir.name,
                title=title,
                date=date,
                created=datetime.now().isoformat(timespec="seconds"),
                source_kind=source_kind,
                source_name=source_name,
                options=options,
            )
            self.source_dir(project).mkdir(parents=True, exist_ok=True)
            self.lesson_metadata_path(project).write_text(
                json.dumps({"topic": title, "date": date}, ensure_ascii=False), encoding="utf-8"
            )
            self.save(project)
            return project

    def get(self, project_id: str) -> Optional[Project]:
        path = self.root / project_id / "project.json"
        if "/" in project_id or "\\" in project_id or ".." in project_id or not path.exists():
            return None
        with self._lock:
            try:
                return Project(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                return None

    def list(self) -> List[Project]:
        projects = [self.get(p.name) for p in self.root.iterdir() if (p / "project.json").exists()]
        return sorted((p for p in projects if p), key=lambda p: p.created, reverse=True)

    def save(self, project: Project) -> None:
        with self._lock:
            project.log = project.log[-MAX_LOG_LINES:]
            path = self.folder(project) / "project.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(project), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

    def update(self, project_id: str, **changes: Any) -> Project:
        with self._lock:
            project = self.get(project_id)
            if project is None:
                raise KeyError(project_id)
            for key, value in changes.items():
                setattr(project, key, value)
            self.save(project)
            return project

    def append_log(self, project_id: str, line: str) -> None:
        with self._lock:
            project = self.get(project_id)
            if project is None:
                return
            stamp = datetime.now().strftime("%H:%M:%S")
            project.log.append(f"{stamp}  {line}")
            self.save(project)

    def delete(self, project_id: str) -> bool:
        with self._lock:
            project = self.get(project_id)
            if project is None or project.running:
                return False
            shutil.rmtree(self.folder(project), ignore_errors=True)
            return True

    # ------------------------------------------------------------------ outputs

    def files(self, project: Project) -> Dict[str, Path]:
        """Downloadable results that exist, by short name."""
        paths = self.run_paths(project)
        candidates = {
            "audio": paths.final_audio_path,
            "video": paths.final_video_path,
            "transcript_srt": self.folder(project) / "transcript.srt",
            "transcript_txt": self.folder(project) / "transcript.txt",
            "script_json": paths.dialogue_path,
            "summary_html": self.folder(project) / "summary.html",
        }
        return {name: path for name, path in candidates.items() if path.exists()}

    def read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
