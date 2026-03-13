from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import json

from pydub import AudioSegment

from .logging import get_logger
from .quality_checker import QualityReport
from . import RunPaths, Settings

import numpy as np

try:  # pragma: no cover - optional dependency
    import cv2  # type: ignore
except Exception:  # pragma: no cover - headless fallback
    cv2 = None


@dataclass
class GuardianResult:
    """Result of the guardian healing pass."""

    assets: List[Path]
    manifest: List[Path]
    manifest_path: Optional[Path] = None
    adaptive_timeline: bool = False
    ken_burns: bool = False
    audio_normalized: bool = False


class ProductionGuardian:
    """
    Final safeguard layer before video composition.

    Responsibilities:
    - Validate and heal visual assets (remove corrupt/black frames, regenerate once if possible).
    - Decide whether to run in adaptive timeline mode (stretch existing frames, no placeholders).
    - Normalize final audio loudness to avoid clipping/quiet renders.
    """

    def __init__(self, run_paths: RunPaths, settings: Settings, quality_report: QualityReport) -> None:
        self.run_paths = run_paths
        self.settings = settings
        self.quality_report = quality_report
        self.logger = get_logger(self.__class__.__name__)

    def optimize_and_fix(
        self,
        metadata: Optional[Dict] = None,
        visual_metadata: Optional[Dict] = None,
        extra_assets: Optional[List[Path]] = None,
    ) -> GuardianResult:
        visuals_dir = self.run_paths.visuals_dir
        visuals_dir.mkdir(parents=True, exist_ok=True)

        expected_images = self._expected_image_count()

        extra_assets = extra_assets or []
        image_candidates = list(visuals_dir.glob("*.png")) + list(visuals_dir.glob("*.jpg")) + list(visuals_dir.glob("*.jpeg"))
        image_candidates.extend([p for p in extra_assets if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
        valid_images = self._validate_images(image_candidates)

        video_candidates = list(visuals_dir.glob("*.mp4")) + list(visuals_dir.glob("*.mov")) + list(visuals_dir.glob("*.webm"))
        video_candidates.extend([p for p in extra_assets if p.suffix.lower() in {".mp4", ".mov", ".webm"}])
        video_assets: List[Path] = self._validate_videos(sorted(set(video_candidates)))

        # One retry: attempt regeneration when we have fewer images than requested.
        if len(valid_images) < expected_images:
            missing = expected_images - len(valid_images)
            regenerated = self._attempt_regeneration(missing, metadata or {}, visual_metadata or {})
            if regenerated:
                # Re-validate including the regenerated assets
                valid_images = self._validate_images(valid_images + regenerated)

        # If no visuals survived, fail fast instead of injecting placeholders
        if not valid_images and not video_assets:
            msg = "No valid visual assets after validation (images/videos missing or corrupt)."
            self.logger.error(msg)
            raise RuntimeError(msg)

        adaptive = False
        if len(valid_images) < max(1, expected_images):
            adaptive = True
            self.quality_report.recommendations.append(
                f"Adaptive timeline enabled: using {len(valid_images)} of {expected_images} requested visuals."
            )

        audio_normalized = self._normalize_audio()

        manifest_assets = sorted(set(valid_images + video_assets))

        # Persist execution manifest for downstream consumers (deterministic asset list)
        manifest_path = self.run_paths.run_dir / "execution_manifest.json"
        try:
            manifest_path.write_text(
                json.dumps([str(p) for p in manifest_assets], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.logger.info("Execution manifest written: %s", manifest_path.name)
        except Exception as exc:
            self.logger.warning("Failed to write execution manifest: %s", exc)
            manifest_path = None

        return GuardianResult(
            assets=manifest_assets,
            manifest=manifest_assets,
            manifest_path=manifest_path,
            adaptive_timeline=adaptive,
            ken_burns=False,
            audio_normalized=audio_normalized,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _expected_image_count(self) -> int:
        try:
            count = int(getattr(self.settings, "image_count", 0) or 0)
        except Exception:
            count = 0
        return max(1, count or 5)

    def _validate_images(self, images: List[Path]) -> List[Path]:
        """Filter out unreadable/black images and delete corrupt ones."""
        valid: List[Path] = []
        for image_path in images:
            try:
                if not image_path.exists():
                    continue
                size_kb = image_path.stat().st_size / 1024
                if size_kb < 8:
                    self.logger.warning("Skipping tiny image (%.1fKB): %s", size_kb, image_path.name)
                    continue
                if cv2 is None:
                    valid.append(image_path)
                    continue

                # Use numpy.fromfile + cv2.imdecode to handle Unicode paths on Windows
                try:
                    stream = np.fromfile(str(image_path), dtype=np.uint8)
                    img = cv2.imdecode(stream, cv2.IMREAD_COLOR)
                except Exception as exc:
                    self.logger.warning("Skipping image read error (%s): %s", image_path.name, exc)
                    continue

                if img is None:
                    self.logger.warning("Skipping unreadable image (imdecode failed): %s", image_path.name)
                    continue

                # Detect near-black/corrupt frames
                mean_intensity = float(cv2.mean(img)[0])
                if mean_intensity < 2.5:
                    self.logger.warning("Skipping black frame: %s (mean=%.2f)", image_path.name, mean_intensity)
                    continue

                # Detect flat/empty frames (uniform gray) that can sneak through
                std_intensity = float(np.std(img))
                if std_intensity < 1.5:
                    self.logger.warning(
                        "Skipping flat frame: %s (std=%.2f, mean=%.2f)", image_path.name, std_intensity, mean_intensity
                    )
                    continue

                valid.append(image_path)
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("Error validating %s: %s", image_path.name, exc)
        return sorted(valid)

    def _validate_videos(self, videos: List[Path]) -> List[Path]:
        """
        Filter out unreadable/fully-black videos. Uses three probes (start/mid/end)
        to avoid rejecting fades that begin with a black frame.
        """
        valid: List[Path] = []
        for video_path in videos:
            try:
                if not video_path.exists():
                    continue
                if cv2 is None:
                    valid.append(video_path)
                    continue

                cap = cv2.VideoCapture(str(video_path))
                if not cap or not cap.isOpened():
                    self.logger.warning("Skipping unreadable video: %s", video_path.name)
                    continue

                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                sample_indices = [0]
                if frame_count > 0:
                    mid_idx = max(frame_count // 2, 0)
                    last_idx = max(frame_count - 1, 0)
                    sample_indices = sorted({0, mid_idx, last_idx})

                means: List[float] = []
                for idx in sample_indices:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, float(idx))
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        continue
                    means.append(float(cv2.mean(frame)[0]))

                cap.release()

                if not means:
                    self.logger.warning("Skipping video with no readable frames: %s", video_path.name)
                    continue

                if all(m < 2.5 for m in means):
                    self.logger.warning(
                        "Skipping likely black video: %s (sample means=%s)", video_path.name, [f"{m:.2f}" for m in means]
                    )
                    continue

                valid.append(video_path)
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("Error validating video %s: %s", video_path.name, exc)
        return sorted(valid)

    def _generate_placeholder(self, metadata: Dict, visuals_dir: Path) -> Optional[Path]:
        """Generate a simple gradient placeholder to avoid blank outputs."""
        try:
            visuals_dir.mkdir(parents=True, exist_ok=True)
            width, height = 1920, 1080
            from PIL import Image, ImageDraw, ImageFont

            img = Image.new("RGB", (width, height))
            draw = ImageDraw.Draw(img)
            for y in range(height):
                ratio = y / max(height - 1, 1)
                r = int(20 + 45 * ratio)
                g = int(28 + 60 * ratio)
                b = int(58 + 90 * ratio)
                draw.line([(0, y), (width, y)], fill=(r, g, b))

            # Add light noise so files are not classified as tiny/flat
            try:
                noise = Image.effect_noise((width, height), 10)
                noise_rgb = noise.convert("RGB")
                img = Image.blend(img, noise_rgb, 0.08)
                draw = ImageDraw.Draw(img)
            except Exception:
                pass

            try:
                font_title = ImageFont.truetype("arial.ttf", 64)
                font_body = ImageFont.truetype("arial.ttf", 36)
            except OSError:
                font_title = ImageFont.load_default()
                font_body = ImageFont.load_default()

            headline = metadata.get("topic") or "תוכן חזותי ייווצר לאחר שחזור רשת"
            caption = metadata.get("summary") or "ממתינים לשירות ה-AI או לחיבור יציב יותר."
            draw.text((120, 320), str(headline), font=font_title, fill=(245, 245, 245))
            draw.text((120, 420), str(caption)[:140], font=font_body, fill=(220, 220, 220))

            out_path = visuals_dir / "guardian_placeholder.png"
            img.save(out_path, quality=95)
            self.logger.info("Guardian generated placeholder: %s", out_path.name)
            return out_path
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning("Failed to generate guardian placeholder: %s", exc)
            return None

    def _attempt_regeneration(
        self,
        missing: int,
        metadata: Dict,
        visual_metadata: Dict,
    ) -> List[Path]:
        """Best-effort regeneration using Google Imagen when available."""
        outputs: List[Path] = []
        if missing <= 0:
            return outputs

        topic = metadata.get("topic") or metadata.get("summary") or "learning content"
        topic_slug = self._slugify(topic)

        try:
            from ..visuals.google_ai_visuals import GoogleAIVisualGenerator  # type: ignore
        except Exception:
            self.logger.info("GoogleAIVisualGenerator unavailable; skipping regeneration.")
            return outputs

        try:
            generator = GoogleAIVisualGenerator(self.settings)
        except Exception as exc:
            self.logger.warning("Could not initialize GoogleAIVisualGenerator: %s", exc)
            return outputs

        if not getattr(generator, "is_available", False):
            self.logger.info("Google AI visuals not available; skipping regeneration.")
            return outputs

        for idx in range(missing):
            out_path = self.run_paths.visuals_dir / f"guardian_regen_{idx+1:02d}_{topic_slug}.png"
            try:
                prompt_source = visual_metadata.get("images", [{}])[idx % max(len(visual_metadata.get("images", [])), 1)]
                concept = prompt_source.get("prompt") or topic
            except Exception:
                concept = topic

            result = generator.generate_concept_illustration(concept=concept, context=topic, output_path=out_path)
            if result and result.exists():
                self.logger.info("Regenerated visual: %s", result.name)
                outputs.append(result)

        return outputs

    def _normalize_audio(self) -> bool:
        """Normalize final audio loudness to avoid clipping/quiet renders."""
        audio_path = self.run_paths.final_audio_path
        if not audio_path.exists():
            return False

        try:
            audio = AudioSegment.from_file(audio_path)
        except Exception as exc:  # pragma: no cover - runtime only
            self.logger.warning("Audio normalization skipped (load failed): %s", exc)
            return False

        try:
            current_lufs = audio.dBFS
            peak_db = audio.max_dBFS
        except Exception:
            current_lufs = audio.dBFS
            peak_db = -1.0

        target_lufs = -16.0
        needs_raise = current_lufs < -20.0
        needs_trim = peak_db > -1.0

        if not needs_raise and not needs_trim:
            return False

        gain_db = 0.0
        if needs_raise:
            gain_db = target_lufs - current_lufs
        if needs_trim:
            headroom = -1.0 - peak_db
            gain_db = min(gain_db, headroom) if gain_db else headroom

        if gain_db == 0.0:
            return False

        normalized = audio.apply_gain(gain_db)
        format_hint = audio_path.suffix.replace(".", "") or "mp3"
        normalized.export(audio_path, format=format_hint)
        self.logger.info(
            "Audio normalized by %.2fdB (LUFS %.2f -> %.2f, peak %.2f dBFS)",
            gain_db,
            current_lufs,
            normalized.dBFS,
            normalized.max_dBFS,
        )
        self.quality_report.recommendations.append(
            f"Audio normalized by {gain_db:.2f}dB to target loudness."
        )
        return True

    @staticmethod
    def _slugify(value: str) -> str:
        clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
        return clean.strip("_")[:80] or "visual"

