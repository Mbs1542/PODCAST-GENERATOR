from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

from pydub import AudioSegment, effects

from ..utils import CostTracker, RunPaths, Settings, get_logger


class PodcastStitcher:
    """Merge audio segments, music beds, and metadata into a final MP3."""

    def __init__(self, settings: Settings, cost_tracker: Optional[CostTracker] = None) -> None:
        self.settings = settings
        self.logger = get_logger(self.__class__.__name__)
        self.cost_tracker = cost_tracker or CostTracker()

    def build(
        self,
        segments: Iterable[Path],
        metadata: Dict,
        run_paths: RunPaths,
    ) -> Path:
        run_paths.log("Starting audio stitching.")
        combined = AudioSegment.silent(duration=0)
        for segment_path in segments:
            segment = AudioSegment.from_file(segment_path)
            combined += segment

        intro = self._load_music(self.settings.intro_music, duration_ms=3000)
        outro = self._load_music(self.settings.outro_music, duration_ms=2000)
        final_mix = intro + combined + outro
        
        # Normalize with headroom to preserve clarity (especially for Hebrew)
        # Use headroom=0.5 to avoid over-compression that can make speech unclear
        final_mix = effects.normalize(final_mix, headroom=0.5)
        
        # Ensure consistent sample rate
        if final_mix.frame_rate != 44100:
            final_mix = final_mix.set_frame_rate(44100)

        run_paths.final_audio_path.parent.mkdir(parents=True, exist_ok=True)
        # Podcast loudness standard (-16 LUFS, -1.5 dBTP): chunks from different generations and the
        # music then play at one even level, like a produced show. Falls back to peak normalization.
        # 44.1 kHz stereo at 192k
        if not self._export_loudness_normalized(final_mix, run_paths.final_audio_path):
            final_mix.export(
                run_paths.final_audio_path,
                format="mp3",
                bitrate="192k",
                parameters=["-ar", "44100", "-ac", "2"],
            )

        duration_minutes = round(final_mix.duration_seconds / 60, 2)
        self._write_metadata(metadata, run_paths, duration_minutes)

        run_paths.log(f"Audio stitching complete. Duration: {duration_minutes} min.")
        return run_paths.final_audio_path

    def _export_loudness_normalized(self, mix: AudioSegment, target: Path) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        with tempfile.TemporaryDirectory(prefix="mbs-mix-") as folder:
            raw = Path(folder) / "mix.wav"
            mix.export(raw, format="wav")
            result = subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
                 "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "2", "-b:a", "192k", str(target)],
                capture_output=True, text=True,
            )
        if result.returncode != 0 or not target.exists():
            self.logger.warning("Loudness normalization failed, using peak normalization: %s", result.stderr[-300:])
            return False
        return True

    def _load_music(self, path: Path, duration_ms: int) -> AudioSegment:
        if not path or not path.exists():
            self.logger.warning("Music bed %s not found, substituting silence.", path)
            return AudioSegment.silent(duration=duration_ms)
        audio = AudioSegment.from_file(path)
        if audio.duration_seconds * 1000 > duration_ms:
            audio = audio[:duration_ms]
        return audio.fade_in(200).fade_out(200)

    def _write_metadata(self, metadata: Dict, run_paths: RunPaths, duration_minutes: float) -> None:
        generated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        payload = {
            "topic": metadata.get("topic"),
            "lecture_date": metadata.get("date"),
            "generated_at": generated_at,
            "duration_minutes": duration_minutes,
            "costs": self.cost_tracker.as_dict(),
            "source_metadata": metadata,
            "artifacts": {
                "dialogue": str(run_paths.dialogue_path),
                "final_audio": str(run_paths.final_audio_path),
                "audio_segments": str(run_paths.audio_dir),
                "processing_log": str(run_paths.log_path),
            },
        }
        run_paths.metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

