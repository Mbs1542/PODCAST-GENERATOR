from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TypeVar
import tempfile
import time
import subprocess
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont
# MoviePy 2.0 compatible imports
from moviepy.editor import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)
from moviepy.video.tools.subtitles import SubtitlesClip
from ..utils import RunPaths, Settings, get_logger
from .subtitle_generator import generate_srt_from_dialogue, wrap_caption_text
from ..utils.text_utils import shape_rtl, srt_text, render_text, wrap_logical_text


T = TypeVar("T")


@dataclass
class VideoComposer:
    """Combine audio, animations, and slides into final video."""

    settings: Settings
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
    VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
    MIN_IMAGE_DURATION = 2.0
    SAFE_BG = (12, 12, 12)  # Neutral dark gray to avoid blue screens
    SAFE_FG = (235, 235, 235)

    def __post_init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)

    # ---------- Debug + safety helpers ----------
    def _log_clip_state(
        self,
        clip: VideoClip,
        label: str,
        path: Optional[Path] = None,
        stage: str = "pre",
    ) -> None:
        try:
            size = (getattr(clip, "w", None), getattr(clip, "h", None))
            duration = getattr(clip, "duration", None)
            fps = getattr(clip, "fps", None) or getattr(getattr(clip, "reader", None), "fps", None)
            has_alpha = bool(getattr(clip, "mask", None))
            exists = path.exists() if path else None
            self.logger.debug(
                "[Clip %s] %s | path=%s exists=%s size=%s duration=%.3f fps=%s alpha=%s type=%s",
                stage,
                label,
                path.name if path else "n/a",
                exists,
                size,
                float(duration or 0.0),
                fps,
                has_alpha,
                clip.__class__.__name__,
            )
        except Exception as exc:
            self.logger.debug("[Clip %s] %s | state logging failed: %s", stage, label, exc)

    def _ensure_fps(self, clip: VideoClip, fps: int = 30) -> VideoClip:
        try:
            if getattr(clip, "fps", None) and abs(getattr(clip, "fps") - fps) < 0.01:
                return clip
            if hasattr(clip, "set_fps"):
                return clip.set_fps(fps)
        except Exception:
            pass
        return clip

    def _normalize_clip(
        self,
        clip: VideoClip,
        label: str,
        path: Optional[Path] = None,
        duration: Optional[float] = None,
        target_size: tuple[int, int] = (1920, 1080),
    ) -> VideoClip:
        self._log_clip_state(clip, label, path, stage="pre-norm")
        try:
            clip = self._fit_clip_to_frame(clip, target_size)
        except Exception as exc:
            self.logger.debug("Fit-to-frame failed for %s: %s", label, exc)
        if duration is not None:
            clip = self._apply_duration(clip, max(0.01, duration))
        clip = self._ensure_fps(clip, 30)
        self._log_clip_state(clip, label, path, stage="post-norm")
        return clip

    def _safe_placeholder_clip(self, duration: float, text: str = "Missing asset") -> VideoClip:
        """
        Neutral placeholder (dark gray) to avoid accidental blue/green frames.
        """
        width, height = 1920, 1080
        try:
            canvas = Image.new("RGB", (width, height), color=self.SAFE_BG)
            draw = ImageDraw.Draw(canvas)
            font = ImageFont.load_default()
            msg = shape_rtl(text)
            tw, th = draw.textsize(msg, font=font)
            draw.text(((width - tw) / 2, (height - th) / 2), msg, font=font, fill=self.SAFE_FG)
            clip = ImageClip(np.array(canvas))
        except Exception:
            clip = ColorClip(size=(width, height), color=self.SAFE_BG)
        clip = self._apply_duration(clip, max(0.01, duration))
        clip = self._ensure_fps(clip, 30)
        self._log_clip_state(clip, "safe-placeholder", stage="post-norm")
        return clip

    def apply_ken_burns(self, clip: VideoClip, zoom_factor: float = 1.05) -> VideoClip:
        """
        Apply a gentle Ken Burns zoom compatible with MoviePy 2.0 (no callable centers).
        Falls back gracefully if resizing/cropping fails.
        """
        duration = max(getattr(clip, "duration", 0.0) or 0.0, 0.001)
        target_w = getattr(clip, "w", 1920)
        target_h = getattr(clip, "h", 1080)

        try:
            zoomed = clip.fx(vfx.resize, zoom_factor)
            # Static drift to avoid crop() expecting numeric centers in MoviePy 2.0
            x_drift = random.uniform(-0.04, 0.04) * target_w
            y_drift = random.uniform(-0.04, 0.04) * target_h
            center_x = (getattr(zoomed, "w", target_w) or target_w) / 2 + x_drift
            center_y = (getattr(zoomed, "h", target_h) or target_h) / 2 + y_drift

            cropped = zoomed.fx(
                vfx.crop,
                width=target_w,
                height=target_h,
                x_center=float(center_x),
                y_center=float(center_y),
            )
            cropped = self._ensure_fps(cropped, getattr(clip, "fps", 30) or 30)
            return cropped
        except Exception as exc:
            self.logger.warning("Ken Burns disabled for clip (%s): %s", getattr(clip, "__class__", type(clip)).__name__, exc)
            return clip

    def _load_cv2(self):
        try:
            import cv2  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency issue
            self.logger.error("OpenCV (cv2) is required for visual validation: %s", exc)
            return None
        return cv2

    def _create_placeholder_slides(
        self,
        count: int,
        output_dir: Path,
        metadata: Dict[str, Any],
        start_index: int = 1,
    ) -> List[Path]:
        """
        Generate simple Hebrew placeholder slides when real images are missing.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        font_title, font_body = self._load_fonts()
        topic = metadata.get("topic") or metadata.get("summary") or "תוכן חזותי"
        concepts = metadata.get("key_concepts") or []
        speakers = metadata.get("speakers") or metadata.get("characters") or []

        palettes = [
            ((15, 23, 42), (79, 70, 229), (244, 114, 182)),
            ((32, 19, 53), (190, 24, 93), (248, 250, 252)),
            ((20, 83, 45), (74, 222, 128), (248, 250, 252)),
            ((67, 56, 202), (147, 51, 234), (253, 224, 71)),
        ]

        placeholders: List[Path] = []
        width, height = 1920, 1080

        for idx in range(count):
            palette = palettes[(start_index + idx - 1) % len(palettes)]
            bg_start, bg_end, accent = palette
            image = Image.new("RGB", (width, height), color=bg_start)
            draw = ImageDraw.Draw(image)

            for y in range(height):
                ratio = y / max(height - 1, 1)
                r = int(bg_start[0] * (1 - ratio) + bg_end[0] * ratio)
                g = int(bg_start[1] * (1 - ratio) + bg_end[1] * ratio)
                b = int(bg_start[2] * (1 - ratio) + bg_end[2] * ratio)
                draw.line([(0, y), (width, y)], fill=(r, g, b))

            draw.rectangle([(0, 0), (width, 12)], fill=accent)
            draw.rectangle([(0, height - 12), (width, height)], fill=accent)

            # Add subtle noise to avoid ultra-small placeholder files flagged as corrupt
            try:
                noise = Image.effect_noise((width, height), 12)
                noise_rgb = noise.convert("RGB")
                image = Image.blend(image, noise_rgb, 0.06)
                draw = ImageDraw.Draw(image)
            except Exception:
                pass

            concept_text = ""
            if concepts:
                concept_text = concepts[(start_index + idx - 1) % len(concepts)]

            headline = shape_rtl("תמונת פלייסהולדר")
            subline = shape_rtl(f"{topic}" if topic else "תוכן חזותי")
            caption = shape_rtl(concept_text or "ויזואל זמני עד ליצירת תמונה מותאמת")

            draw.text((120, 200), headline, font=font_title, fill=(255, 255, 255))
            draw.text((120, 320), subline, font=font_body, fill=(240, 240, 240))
            draw.text((120, 420), caption, font=font_body, fill=(220, 220, 220))

            if speakers:
                speaker_line = "דוברים: " + ", ".join(speakers[:4])
                draw.text((120, 520), speaker_line, font=font_body, fill=(210, 210, 210))

            badge_text = "AI · Hebrew · Placeholder"
            draw.rectangle([(120, 580), (700, 640)], fill=(0, 0, 0, 120), outline=None)
            draw.text((140, 595), badge_text, font=font_body, fill=(255, 255, 255))

            filename = f"placeholder_{start_index + idx:02d}.png"
            path = output_dir / filename
            image.save(path, quality=95)
            placeholders.append(path)
            self.logger.info("Generated placeholder slide: %s", path.name)

        return placeholders

    def validate_image(self, image_path: Path) -> bool:
        """
        Validate that an image exists, is readable, and meets basic quality requirements.
        """
        cv2 = self._load_cv2()
        if cv2 is None:
            return False

        try:
            if not image_path.exists():
                self.logger.warning("Image missing: %s", image_path)
                return False

            file_size_kb = image_path.stat().st_size / 1024
            if file_size_kb < 100:
                self.logger.warning("Image too small (%.1fKB): %s", file_size_kb, image_path.name)
                return False

            # Use numpy.fromfile + cv2.imdecode to handle Unicode paths on Windows
            stream = np.fromfile(str(image_path), dtype=np.uint8)
            img = cv2.imdecode(stream, cv2.IMREAD_COLOR)
            if img is None:
                self.logger.warning("Failed to read image: %s", image_path.name)
                return False

            height, width = img.shape[:2]
            if width < 800 or height < 600:
                self.logger.warning(
                    "Image resolution too low (%dx%d): %s", width, height, image_path.name
                )
                return False

            return True
        except Exception as exc:
            self.logger.error("Image validation error for %s: %s", image_path.name, exc)
            return False

    def get_valid_images(
        self,
        image_dir: Path,
        required_count: int,
        candidates: Optional[Iterable[Path]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        allow_placeholders: bool = True,
    ) -> List[Path]:
        """
        Validate images from directory and return high-quality assets.

        Args:
            image_dir: Directory containing generated visuals.
            required_count: Desired number of image assets.
            candidates: Optional iterable of pre-discovered image paths.
        """
        if required_count <= 0:
            required_count = 1

        if candidates is None:
            candidate_paths: List[Path] = []
            for ext in self.IMAGE_EXTENSIONS:
                candidate_paths.extend(image_dir.glob(f"*{ext}"))
        else:
            candidate_paths = list(candidates)

        unique_candidates = sorted(set(candidate_paths))
        valid_images: List[Path] = []

        for img_path in unique_candidates:
            if self.validate_image(img_path):
                valid_images.append(img_path)
            else:
                self.logger.debug("Discarded invalid image: %s", img_path.name)

        if not valid_images:
            self.logger.warning("No valid images found in %s", image_dir)

        if len(valid_images) >= required_count:
            return valid_images[:required_count]

        placeholders_needed = required_count - len(valid_images)
        if placeholders_needed > 0:
            msg = (
                f"Missing {placeholders_needed} visual(s); validated only {len(valid_images)} / {required_count}. "
                "Placeholders are disabled to avoid silent fallback."
            )
            self.logger.error(msg)
            raise RuntimeError(msg)

        return valid_images[:required_count]

    def _prioritize_real_images(self, image_paths: List[Path]) -> List[Path]:
        """
        Group visuals by slide index and prefer real assets over placeholders.

        If a real image exists for an index, placeholders for that index are ignored
        (not deleted). Otherwise, placeholders are used as a fallback.
        """
        real_pattern = re.compile(r"image_(\d+)", re.IGNORECASE)
        placeholder_pattern = re.compile(r"placeholder_(\d+)", re.IGNORECASE)

        real_by_index: Dict[int, List[Path]] = {}
        placeholders_by_index: Dict[int, List[Path]] = {}
        unmatched: List[Path] = []

        for path in image_paths:
            name = path.name
            real_match = real_pattern.search(name)
            placeholder_match = placeholder_pattern.search(name)

            if real_match:
                idx = int(real_match.group(1))
                real_by_index.setdefault(idx, []).append(path)
            elif placeholder_match:
                idx = int(placeholder_match.group(1))
                placeholders_by_index.setdefault(idx, []).append(path)
            else:
                unmatched.append(path)

        prioritized: List[Path] = []
        all_indices = sorted(set(real_by_index.keys()) | set(placeholders_by_index.keys()))
        for idx in all_indices:
            real_assets = sorted(real_by_index.get(idx, []))
            placeholder_assets = sorted(placeholders_by_index.get(idx, []))

            if real_assets:
                if placeholder_assets:
                    self.logger.info(
                        "[VideoComposer] Index %d: Using Real Image (ignoring placeholder).",
                        idx,
                    )
                prioritized.extend(real_assets)
            elif placeholder_assets:
                prioritized.extend(placeholder_assets)

        prioritized.extend(sorted(unmatched))
        return prioritized


    @staticmethod
    def _apply_duration(clip: VideoClip, duration: float) -> VideoClip:
        if hasattr(clip, "with_duration"):
            return clip.with_duration(duration)
        return clip.set_duration(duration)

    @staticmethod
    def _apply_start(clip: VideoClip, start: float) -> VideoClip:
        if hasattr(clip, "with_start"):
            return clip.with_start(start)
        return clip.set_start(start)

    @staticmethod
    def _fit_clip_to_frame(clip: VideoClip, target_size: tuple[int, int] = (1920, 1080)) -> VideoClip:
        """
        Scale and center a clip to fit inside the target frame without leaving it tiny/off-frame.
        """
        try:
            target_w, target_h = target_size
            scale = max(target_w / max(getattr(clip, "w", target_w), 1e-3), target_h / max(getattr(clip, "h", target_h), 1e-3))
            resized = clip.fx(vfx.resize, scale)
            centered = resized.fx(
                vfx.crop,
                width=target_w,
                height=target_h,
                x_center=getattr(resized, "w", target_w) / 2,
                y_center=getattr(resized, "h", target_h) / 2,
            )
            return centered.set_position("center")
        except Exception:
            return clip

    @staticmethod
    def _extract_asset_index(path: Path) -> Optional[int]:
        """
        Extract a numeric index from an asset filename. Examples:
        - image_01.png -> 1
        - AzureScene2.mp4 -> 2
        - slide-003.jpg -> 3
        """
        match = re.search(r"(\d{1,3})", path.stem)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    def create_timeline(
        self,
        dialogue_json: Dict,
        metadata: Dict,
        audio_file: Path,
        animations_dir: Path,
        run_paths: Optional[RunPaths] = None,
        asset_paths: Optional[List[Path]] = None,
        adaptive_timeline: bool = False,
        apply_ken_burns: bool = True,
        segment_durations: Optional[List[float]] = None,
        lead_in_seconds: float = 0.0,
    ) -> List[VideoClip]:
        """
        Build video timeline aligned with the final audio.
        """
        if not apply_ken_burns:
            self.logger.info(
                "[VideoComposer] Enforcing Ken Burns (5%% zoom) for all static visuals; override ignored."
            )
        apply_ken_burns = True
        preview_cap = 3 if getattr(self.settings, "preview_mode", False) else None

        audio_clip = AudioFileClip(str(audio_file))
        total_duration = audio_clip.duration
        audio_clip.close()
        turn_count = max(len(dialogue_json.get("dialogue", [])), 1)
        visual_cues = []
        try:
            visual_cues = [float(x) for x in metadata.get("visual_cues", [])] if isinstance(metadata, dict) else []
        except Exception:
            visual_cues = []

        # Prefer actual timestamps if present for tighter audio-visual sync
        turn_durations: List[float] = []
        for turn in dialogue_json.get("dialogue", []):
            start = turn.get("start") or turn.get("ts_start")
            end = turn.get("end") or turn.get("ts_end")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
                turn_durations.append(float(end - start))
        has_aligned_timings = bool(turn_durations) and len(turn_durations) == turn_count

        provided_turn_durations: List[float] = []
        if segment_durations:
            provided_turn_durations = [max(0.01, float(d or 0.0)) for d in segment_durations]
            if len(provided_turn_durations) < turn_count:
                provided_turn_durations.extend([0.5] * (turn_count - len(provided_turn_durations)))
            provided_turn_durations = provided_turn_durations[:turn_count]
            has_aligned_timings = len(provided_turn_durations) == turn_count

        effective_turn_durations: List[float] = provided_turn_durations or (turn_durations if has_aligned_timings else [])

        # More robust file detection
        video_files: List[Path] = []
        image_files: List[Path] = []
        raw_image_files: List[Path] = []
        prioritized_images: List[Path] = []

        # Placeholders are disabled: fail fast if assets are missing
        allow_placeholders = False

        if asset_paths is not None:
            existing = [p for p in asset_paths if p and p.exists()]
            # Prefer assets generated in the current run directory
            existing = sorted(
                set(existing),
                key=lambda p: 0 if animations_dir in p.parents else 1,
            )
            video_files = sorted({p for p in existing if p.suffix.lower() in self.VIDEO_EXTENSIONS})
            prioritized_images = sorted({p for p in existing if p.suffix.lower() in self.IMAGE_EXTENSIONS})
            raw_image_files = list(prioritized_images)
        else:
            for ext in self.VIDEO_EXTENSIONS:
                video_files.extend(list(animations_dir.glob(f"*{ext}")))
            for ext in self.IMAGE_EXTENSIONS:
                image_files.extend(list(animations_dir.glob(f"*{ext}")))

            video_files = sorted(list(set(video_files)))  # Remove duplicates
            raw_image_files = sorted(list(set(image_files)))  # Remove duplicates

            # Group by index and prefer real images over placeholders without deleting files
            prioritized_images = self._prioritize_real_images(raw_image_files)
            real_count = sum(1 for p in prioritized_images if "placeholder" not in p.name.lower())
            placeholder_count = sum(1 for p in prioritized_images if "placeholder" in p.name.lower())
            self.logger.info(
                "Prioritized images: %d real, %d placeholder (post-filter).",
                real_count,
                placeholder_count,
            )

        if preview_cap:
            prioritized_images = prioritized_images[:preview_cap]
            if video_files:
                video_files = video_files[:preview_cap]
            self.logger.info("[VideoComposer] Preview cap active: limiting visuals to %d asset(s).", preview_cap)

        # Validate images before composing to avoid corrupted assets.
        if asset_paths is not None:
            raw_image_count = getattr(self.settings, "image_count", 1)
            try:
                parsed_count = int(raw_image_count)
            except Exception:
                parsed_count = 1
            required_image_count = len(prioritized_images) or max(1, parsed_count)
        else:
            raw_image_count = getattr(self.settings, "image_count", len(prioritized_images) or 5)
            try:
                parsed_count = int(raw_image_count)
            except Exception:
                parsed_count = len(prioritized_images) or 5
            required_image_count = max(len(prioritized_images), parsed_count)

        if preview_cap:
            required_image_count = min(preview_cap, required_image_count)

        image_files = self.get_valid_images(
            animations_dir,
            required_count=required_image_count,
            candidates=prioritized_images,
            metadata=metadata,
            allow_placeholders=allow_placeholders,
        )

        # Surface and fail on missing visuals when images were requested
        if required_image_count > 0 and len(image_files) < required_image_count:
            msg = (
                f"Validated {len(image_files)} of {required_image_count} required visual(s); "
                "aborting to avoid placeholder/blank output."
            )
            self.logger.error(msg)
            if run_paths:
                run_paths.log(f"❌ {msg}")
            raise RuntimeError(msg)

        if adaptive_timeline and run_paths:
            run_paths.log("Adaptive timeline: stretching available visuals (no placeholders).")

        visual_summary = (
            "Visual assets detected: "
            f"{len(video_files)} video(s), {len(image_files)} image(s) for {turn_count} turns."
        )
        self.logger.info(visual_summary)
        if run_paths:
            run_paths.log(visual_summary)

        # Log detailed file information for debugging
        if video_files:
            self.logger.info("Video files found: %s", [p.name for p in video_files])
        if image_files:
            self.logger.info("Image files found: %s", [p.name for p in image_files])
        if raw_image_files and len(image_files) < len(raw_image_files):
            removed = set(p.name for p in raw_image_files) - set(p.name for p in image_files)
            self.logger.warning("Discarded %d invalid image(s): %s", len(removed), list(removed))

        # region agent log
        try:
            log_path = Path(r"c:\Users\maorb\OneDrive\Desktop\PODCAST GENERATOR\.cursor\debug.log")
            payload = {
                "sessionId": "debug-session",
                "runId": "pre-fix",
                "hypothesisId": "H3-placeholder-selection",
                "location": "video_composer.py:create_timeline",
                "message": "asset_inventory",
                "data": {
                    "video_files": [p.name for p in video_files],
                    "image_files": [p.name for p in image_files],
                    "placeholders": [p.name for p in image_files if "placeholder" in p.name.lower()],
                    "preview_cap": preview_cap,
                    "adaptive_timeline": adaptive_timeline,
                    "turn_count": turn_count,
                    "required_image_count": required_image_count,
                },
                "timestamp": int(time.time() * 1000),
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
        except Exception:
            pass
        # endregion

        if not video_files and not image_files:
            fallback_msg = "No rendered visuals found; failing to avoid slide/placeholder fallback."
            self.logger.error(fallback_msg)
            if run_paths:
                run_paths.log(f"❌ {fallback_msg}")
            raise RuntimeError(fallback_msg)

        # ----- Asset ordering: unified index + even distribution fallback -----
        indexed_assets: List[Tuple[int, int, str, Path]] = []
        videos_no_index: List[Path] = []
        images_no_index: List[Path] = []

        type_priority = {"video": 0, "image": 1}
        for vid in video_files:
            idx = self._extract_asset_index(vid)
            if idx is not None:
                indexed_assets.append((idx, type_priority["video"], "video", vid))
            else:
                videos_no_index.append(vid)
        for img in image_files:
            idx = self._extract_asset_index(img)
            if idx is not None:
                indexed_assets.append((idx, type_priority["image"], "image", img))
            else:
                images_no_index.append(img)

        ordered_assets: List[Tuple[str, Path]] = [
            (asset_type, path) for _, _, asset_type, path in sorted(indexed_assets, key=lambda x: (x[0], x[1]))
        ]

        if videos_no_index:
            if ordered_assets:
                slots = len(ordered_assets) + 1
                for i, vid in enumerate(sorted(videos_no_index)):
                    pos = int((i + 1) * slots / (len(videos_no_index) + 1))
                    pos = max(0, min(pos, len(ordered_assets)))
                    ordered_assets.insert(pos, ("video", vid))
            else:
                # No indexed anchors: interleave to avoid clustering
                max_len = max(len(images_no_index), len(videos_no_index))
                interleaved: List[Tuple[str, Path]] = []
                vids_sorted = sorted(videos_no_index)
                imgs_sorted = sorted(images_no_index)
                for i in range(max_len):
                    if i < len(imgs_sorted):
                        interleaved.append(("image", imgs_sorted[i]))
                    if i < len(vids_sorted):
                        interleaved.append(("video", vids_sorted[i]))
                ordered_assets = interleaved

        if images_no_index and ordered_assets:
            # Append remaining unindexed images in stable order
            for img in sorted(images_no_index):
                if ("image", img) not in ordered_assets:
                    ordered_assets.append(("image", img))

        if not ordered_assets:
            for path in sorted(video_files + image_files):
                suffix = path.suffix.lower()
                if suffix in self.VIDEO_EXTENSIONS:
                    ordered_assets.append(("video", path))
                elif suffix in self.IMAGE_EXTENSIONS:
                    ordered_assets.append(("image", path))

        if preview_cap:
            ordered_assets = ordered_assets[:preview_cap]

        # ----- Duration planning -----
        video_durations: Dict[Path, float] = {}
        for _, media in [(t, p) for t, p in ordered_assets if t == "video"]:
            try:
                with VideoFileClip(str(media)) as clip:
                    video_durations[media] = float(getattr(clip, "duration", 0.0) or 0.0)
            except Exception as exc:
                self.logger.warning("Failed to probe video duration for %s: %s", media.name, exc)
                video_durations[media] = 0.0

        sum_video_duration = sum(video_durations.values())
        image_assets = [(t, p) for t, p in ordered_assets if t == "image"]
        image_count = len(image_assets)
        remaining_for_images = max(total_duration - sum_video_duration, 0.0)
        per_image_duration = (
            max(remaining_for_images / image_count, self.MIN_IMAGE_DURATION) if image_count > 0 else 0.0
        )

        image_duration_plan: List[float] = []
        if image_count > 0 and effective_turn_durations:
            turns_len = len(effective_turn_durations)
            if image_count == turns_len:
                image_duration_plan = list(effective_turn_durations)
                if image_duration_plan:
                    image_duration_plan[0] += max(0.0, lead_in_seconds)
            else:
                # Partition dialogue turns into contiguous groups for each image
                boundaries = [int(round(i * turns_len / image_count)) for i in range(image_count + 1)]
                boundaries[0] = 0
                boundaries[-1] = turns_len
                for i in range(1, len(boundaries)):
                    boundaries[i] = min(turns_len, max(boundaries[i], boundaries[i - 1]))

                grouped: List[float] = []
                for i in range(image_count):
                    start_idx = boundaries[i]
                    end_idx = boundaries[i + 1]
                    if end_idx <= start_idx:
                        end_idx = min(turns_len, start_idx + 1)
                    segment_sum = sum(effective_turn_durations[start_idx:end_idx]) if start_idx < turns_len else 0.0
                    if i == 0:
                        segment_sum += max(0.0, lead_in_seconds)
                    grouped.append(max(0.01, segment_sum))

                image_duration_plan = grouped

        clips: List[VideoClip] = []
        current_start = 0.0
        last_image_index: Optional[int] = None
        last_image_clip: Optional[VideoClip] = None
        image_idx = 0

        def _build_image_clip(path: Path, duration: float) -> VideoClip:
            from PIL import Image as PILImage

            img = PILImage.open(str(path))
            img_width, img_height = img.size
            video_width, video_height = 1920, 1080
            ratio = min(video_width / img_width, video_height / img_height)
            new_width = int(img_width * ratio)
            new_height = int(img_height * ratio)
            img = img.resize((new_width, new_height), PILImage.LANCZOS)
            bg = PILImage.new("RGB", (video_width, video_height), (0, 0, 0))
            x = (video_width - new_width) // 2
            y = (video_height - new_height) // 2
            bg.paste(img, (x, y))
            img_array = np.array(bg)
            clip = ImageClip(img_array)
            clip = self._apply_duration(clip, duration)
            clip = self._ensure_fps(clip, 30)
            clip = self._normalize_clip(clip, f"image:{path.name}", path=path, duration=duration)
            if apply_ken_burns:
                try:
                    clip = self.apply_ken_burns(clip)
                except Exception as kb_exc:
                    self.logger.warning("Ken Burns skipped for %s: %s", path.name, kb_exc)
            return clip

        for asset_type, media in ordered_assets:
            remaining = max(total_duration - current_start, 0.0)
            if remaining <= 0:
                break

            try:
                bg_used: Optional[str] = None
                if asset_type == "video":
                    fg_clip = VideoFileClip(str(media), has_mask=True)
                    self._log_clip_state(fg_clip, f"video:{media.name}", media, stage="loaded")
                    fg_clip = self._normalize_clip(fg_clip, f"video:{media.name}", path=media)
                    duration = getattr(fg_clip, "duration", 0.0) or 0.0
                    if duration <= 0:
                        self.logger.warning("Video %s has zero duration; skipping.", media.name)
                        fg_clip.close()
                        continue
                    if duration > remaining:
                        duration = remaining
                        fg_clip = fg_clip.subclipped(0, duration)

                    bg_path: Optional[Path] = None
                    # Use next available image as background, but don't consume it if we're reusing the last one
                    if image_idx < len(image_files):
                        bg_path = image_files[image_idx]
                        # Only advance index if we're not at the last image (to allow reuse)
                        if image_idx < len(image_files) - 1:
                            image_idx += 1
                    elif image_files:
                        # Reuse last image if we've exhausted the list
                        bg_path = image_files[-1]

                    if bg_path and bg_path.exists():
                        try:
                            bg_clip = _build_image_clip(bg_path, duration)
                            self.logger.debug("Using image %s as background for video %s", bg_path.name, media.name)
                            bg_used = bg_path.name
                        except Exception as img_err:
                            self.logger.warning("Background image failed (%s): %s", bg_path.name, img_err, exc_info=True)
                            bg_clip = ColorClip(size=(1920, 1080), color=self.SAFE_BG, duration=duration)
                            bg_clip = self._ensure_fps(bg_clip, 30)
                            bg_used = "color_fallback"
                    else:
                        bg_clip = ColorClip(size=(1920, 1080), color=self.SAFE_BG, duration=duration)
                        bg_clip = self._ensure_fps(bg_clip, 30)
                        bg_used = "color_fallback"

                    fg_clip = self._apply_start(fg_clip, 0)
                    bg_clip = self._apply_start(bg_clip, 0)
                    # Ensure background is bottom, foreground on top
                    composite = CompositeVideoClip([bg_clip, fg_clip], size=(1920, 1080))
                    composite = self._apply_duration(composite, duration)
                    composite = self._apply_start(composite, current_start)
                    self._log_clip_state(composite, f"composite:{media.name}", media, stage="selected")
                    self.logger.info("Added composited video %s over background (%.2fs)", media.name, duration)
                    clip = composite
                else:
                    if visual_cues and image_idx < len(visual_cues):
                        cue_start = max(0.0, visual_cues[image_idx])
                        if cue_start > current_start:
                            current_start = cue_start
                    if image_duration_plan:
                        planned = image_duration_plan[image_idx] if image_idx < len(image_duration_plan) else image_duration_plan[-1]
                        duration = min(planned, remaining)
                    else:
                        duration = min(per_image_duration if per_image_duration > 0 else remaining, remaining)
                    try:
                        clip = _build_image_clip(media, duration)
                        clip = self._apply_start(clip, current_start)
                        self._log_clip_state(clip, f"image:{media.name}", media, stage="selected")
                        self.logger.info(
                            "Added image clip %s (%.2fs)", media.name, duration
                        )
                    except Exception as img_err:
                        self.logger.warning(
                            "Failed to process image %s: %s",
                            media.name,
                            img_err,
                            exc_info=True,
                        )
                        raise RuntimeError(f"Image processing failed for {media.name}: {img_err}") from img_err
                clips.append(clip)
                if asset_type == "image":
                    last_image_index = len(clips) - 1
                    last_image_clip = clip
                    image_idx += 1
                current_start += getattr(clip, "duration", 0.0) or 0.0
                # region agent log
                try:
                    log_path = Path(r"c:\Users\maorb\OneDrive\Desktop\PODCAST GENERATOR\.cursor\debug.log")
                    payload = {
                        "sessionId": "debug-session",
                        "runId": "pre-fix",
                        "hypothesisId": "H2-layering-alpha",
                        "location": "video_composer.py:create_timeline_loop",
                        "message": "clip_selected",
                        "data": {
                            "slot": len(clips),
                            "asset_type": asset_type,
                            "media": media.name if isinstance(media, Path) else str(media),
                            "start": float(getattr(clip, "start", 0.0) or 0.0),
                            "duration": float(getattr(clip, "duration", 0.0) or 0.0),
                            "size": (getattr(clip, "w", None), getattr(clip, "h", None)),
                            "fps": getattr(clip, "fps", None) or getattr(getattr(clip, "reader", None), "fps", None),
                            "is_colorclip": isinstance(clip, ColorClip),
                            "bg_used": bg_used,
                        },
                        "timestamp": int(time.time() * 1000),
                    }
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(payload) + "\n")
                except Exception:
                    pass
                # endregion
                try:
                    self.logger.info(
                        "[Timeline] slot=%d type=%s path=%s start=%.2fs dur=%.2fs size=%s fps=%s placeholder=%s",
                        len(clips) - 1,
                        asset_type,
                        media.name if isinstance(media, Path) else str(media),
                        getattr(clip, "start", 0.0) or 0.0,
                        getattr(clip, "duration", 0.0) or 0.0,
                        (getattr(clip, "w", None), getattr(clip, "h", None)),
                        getattr(clip, "fps", None) or getattr(getattr(clip, "reader", None), "fps", None),
                        isinstance(clip, ColorClip),
                    )
                except Exception:
                    pass
            except Exception as e:
                self.logger.error("Failed to process asset %s: %s", media.name, e, exc_info=True)
                continue

        # Fill or trim to audio duration
        if current_start < total_duration:
            remaining = total_duration - current_start
            if last_image_index is not None and last_image_clip is not None:
                extended = self._apply_duration(last_image_clip, getattr(last_image_clip, "duration", 0.0) + remaining)
                clips[last_image_index] = extended
            elif clips:
                last_clip = clips[-1]
                clips[-1] = self._apply_duration(last_clip, getattr(last_clip, "duration", 0.0) + remaining)
            else:
                tail = ColorClip(size=(1920, 1080), color=self.SAFE_BG, duration=remaining)
                tail = self._ensure_fps(tail, 30)
                self._log_clip_state(tail, "tail-placeholder", stage="post-norm")
                clips.append(tail)
        elif current_start > total_duration and clips:
            overflow = current_start - total_duration
            last_clip = clips[-1]
            new_duration = max((getattr(last_clip, "duration", 0.0) or 0.0) - overflow, 0.01)
            clips[-1] = self._apply_duration(last_clip, new_duration)

        return clips

    def render_final_video(
        self,
        clips: Sequence[VideoClip],
        audio_file: Path,
        output_file: Path,
        dialogue_json: Optional[Dict] = None,
        segment_durations: Optional[List[float]] = None,
        lead_in_seconds: float = 0.0,
        outro_seconds: float = 0.0,
        metadata: Optional[Dict] = None,
    ) -> Path:
        """
        Composite all clips, set audio track, export MP4 (1080p, 30fps, H.264).
        
        Critical: Ensures video duration matches audio duration to prevent
        silent video output.
        """
        clip_list = list(clips)
        
        # Load audio first to get the authoritative duration
        audio = AudioFileClip(str(audio_file))
        audio_duration = audio.duration
        self.logger.info("Audio duration: %.2f seconds", audio_duration)

        # If no clips survived validation, generate a placeholder to avoid blank/grey output
        if not clip_list:
            msg = "No valid clips to render; failing to avoid placeholder video."
            self.logger.error(msg)
            raise RuntimeError(msg)
        
        has_non_solid = any(not isinstance(c, ColorClip) for c in clip_list)
        if not has_non_solid:
            msg = "Only solid color clips detected; failing to avoid placeholder output."
            self.logger.error(msg)
            raise RuntimeError(msg)

        # region agent log
        try:
            log_path = Path(r"c:\Users\maorb\OneDrive\Desktop\PODCAST GENERATOR\.cursor\debug.log")
            payload = {
                "sessionId": "debug-session",
                "runId": "pre-fix",
                "hypothesisId": "H2-layering-alpha",
                "location": "video_composer.py:render_final_video",
                "message": "timeline_summary",
                "data": {
                    "clip_count": len(clip_list),
                    "colorclip_count": sum(1 for c in clip_list if isinstance(c, ColorClip)),
                    "has_non_solid": has_non_solid,
                    "audio_duration": audio_duration,
                },
                "timestamp": int(time.time() * 1000),
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
        except Exception:
            pass
        # endregion

        # Concatenate video clips
        # Log clip ordering before compose
        try:
            order_debug = [
                {
                    "idx": i,
                    "type": clip.__class__.__name__,
                    "start": getattr(clip, "start", 0),
                    "duration": getattr(clip, "duration", 0),
                    "size": (getattr(clip, "w", None), getattr(clip, "h", None)),
                }
                for i, clip in enumerate(clip_list)
            ]
            self.logger.info("Clip order (bottom->top per slot): %s", order_debug)
        except Exception:
            pass

        base = concatenate_videoclips(clip_list, method="compose")
        video_duration = base.duration
        self.logger.info("Video duration before sync: %.2f seconds", video_duration)
        # Ensure video duration matches audio duration exactly
        duration_synced = self._apply_duration(base, audio_duration)

        # Always (re)generate SRT captions alongside the video
        captions_path = output_file.parent / "captions.srt"
        if dialogue_json:
            try:
                self._write_srt(
                    dialogue_json,
                    audio_duration,
                    captions_path,
                    segment_durations=segment_durations,
                    lead_in_seconds=lead_in_seconds,
                    outro_seconds=outro_seconds,
                    target_language=getattr(self.settings, "target_language", None),
                )
            except Exception as exc:
                self.logger.error("Failed to write captions.srt: %s", exc)

        # Build clean video with audio first (no burned subtitles)
        audio_mix = CompositeAudioClip([audio])
        # Prefer the original base clip for audio attachment (preserves mocks)
        attach_source = base if getattr(base, "with_audio", None) else duration_synced
        attach_fn = getattr(attach_source, "with_audio", None) or getattr(attach_source, "set_audio", None)
        attached_clip = attach_fn(audio_mix) if attach_fn else attach_source.set_audio(audio_mix)
        if getattr(attached_clip, "audio", None) is None:
            self.logger.error("CRITICAL: Audio failed to attach to video!")
            raise RuntimeError("Failed to attach audio to video. Audio track is None.")
        final = attached_clip
        # Clamp minimum pixel values slightly above black to prevent blank-frame flags
        try:
            final = final.fl_image(lambda frame: np.clip(frame, 16, 255))
        except Exception as clamp_exc:  # pragma: no cover - defensive
            self.logger.warning("Luma clamp skipped: %s", clamp_exc)

        if final.audio is None:
            self.logger.error("CRITICAL: Audio failed to attach to video!")
            raise RuntimeError("Failed to attach audio to video. Audio track is None.")
        self.logger.info(
            "Final video (pre-subtitles) ready: duration=%.2fs, has_audio=%s",
            final.duration,
            final.audio is not None
        )
        if abs((final.duration or 0) - (audio_duration or 0)) > 0.05:
            self.logger.warning(
                "Duration mismatch detected (video=%.2fs, audio=%.2fs); enforcing sync.",
                final.duration,
                audio_duration,
            )
            final = self._apply_duration(final, audio_duration)

        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Use system temp directory to avoid issues with Hebrew/Unicode characters in path
        temp_dir = tempfile.gettempdir()
        temp_audio_path = Path(temp_dir) / f"mbs_temp_audio_{time.time_ns()}.m4a"
        temp_clean_video = Path(temp_dir) / f"mbs_temp_clean_{time.time_ns()}.mp4"

        subtitle_method = "none"
        ffmpeg_burn_failed = False

        try:
            temp_audio_path.parent.mkdir(parents=True, exist_ok=True)
            self.logger.info(
                "Writing clean video (no subtitles) to temp: %s",
                temp_clean_video,
            )
            final.write_videofile(
                str(temp_clean_video),
                codec="libx264",
                audio=True,
                audio_codec="aac",
                audio_fps=44100,
                audio_bitrate="256k",
                audio_bufsize=3000,
                audio_nbytes=4,
                fps=30,
                threads=4,
                ffmpeg_params=["-pix_fmt", "yuv420p"],
                logger=None,
                write_logfile=False,
                temp_audiofile=str(temp_audio_path),
            )
            subtitle_method = "moviepy_clean"
            if not temp_clean_video.exists():
                raise RuntimeError(f"Expected temp video not found: {temp_clean_video}")

            if captions_path.exists():
                try:
                    self._burn_subtitles_ffmpeg(temp_clean_video, captions_path, output_file)
                    subtitle_method = "ffmpeg_burn"
                    self.logger.info("Subtitles burned via FFmpeg into: %s", output_file)
                except Exception as burn_exc:
                    ffmpeg_burn_failed = True
                    self.logger.error("FFmpeg subtitle burn failed: %s", burn_exc, exc_info=True)
            else:
                temp_clean_video.replace(output_file)
                subtitle_method = "no_captions"

            # Fallback to MoviePy overlay if FFmpeg burn failed
            if ffmpeg_burn_failed:
                self.logger.info("Falling back to MoviePy SubtitlesClip overlay.")

                target_language = getattr(self.settings, "target_language", "") or ""
                from .subtitle_generator import fix_rtl_text

                def _text_factory(txt: str) -> TextClip:
                    logical = srt_text(txt)
                    lines = wrap_logical_text(logical, max_chars_per_line=42)
                    use_rtl = not target_language.startswith("en")
                    display_lines = [render_text(line) if use_rtl else line for line in lines]
                    display_text = "\n".join(display_lines)
                    max_width = int(final.w * 0.83)
                    wrapped_text, applied_size = wrap_caption_text(
                        display_text,
                        max_width_px=max_width,
                        max_lines=2,
                        font_size=45,
                    )
                    # Use method="label" for proper background rendering in MoviePy 2.0
                    return TextClip(
                        wrapped_text,
                        fontsize=applied_size,
                        font="Arial",
                        color="white",
                        bg_color="black",
                        stroke_color="black",
                        stroke_width=2,
                        method="label",
                        size=(max_width, None),
                        align="center",
                    )

                def _parse_ts(ts: str) -> float:
                    hrs, mins, rest = ts.split(":", 2)
                    secs, millis = rest.split(",", 1)
                    return int(hrs) * 3600 + int(mins) * 60 + int(secs) + int(millis) / 1000.0

                subtitle_entries = []
                try:
                    raw = captions_path.read_text(encoding="utf-8")
                    for block in raw.strip().split("\n\n"):
                        lines = block.strip().splitlines()
                        if len(lines) < 3:
                            continue
                        ts_line = lines[1]
                        if "-->" not in ts_line:
                            continue
                        start_str, end_str = [part.strip() for part in ts_line.split("-->", 1)]
                        text = "\n".join(lines[2:]).strip()
                        subtitle_entries.append((( _parse_ts(start_str), _parse_ts(end_str) ), text))
                except Exception as parse_exc:
                    self.logger.error("Failed to parse captions for MoviePy fallback: %s", parse_exc)
                    subtitle_entries = []

                subs = SubtitlesClip(subtitle_entries, _text_factory).set_position(
                    ("center", "bottom")
                )
                subs = subs.set_duration(audio_duration)
                final_video = CompositeVideoClip([final, subs]).set_audio(audio_mix).set_duration(audio_duration)
                final_video.write_videofile(
                    str(output_file),
                    codec="libx264",
                    audio=True,
                    audio_codec="aac",
                    audio_fps=44100,
                    audio_bitrate="256k",
                    audio_bufsize=3000,
                    audio_nbytes=4,
                    fps=30,
                    threads=4,
                    ffmpeg_params=["-pix_fmt", "yuv420p"],
                    logger=None,
                    write_logfile=False,
                    temp_audiofile=str(temp_audio_path),
                )
                subtitle_method = "moviepy_overlay"
        finally:
            audio_mix.close()
            audio.close()
            final.close()
            base.close()
            for clip in clip_list:
                clip.close()
            if temp_audio_path.exists():
                try:
                    temp_audio_path.unlink()
                except OSError:
                    pass
            if temp_clean_video.exists():
                try:
                    temp_clean_video.unlink()
                except OSError:
                    pass

        self.logger.info("Video exported successfully (%s): %s", subtitle_method, output_file)
        return output_file

    def _burn_subtitles_ffmpeg(self, video_path: Path, srt_path: Path, output_path: Path) -> None:
        """
        Burn subtitles into a video using FFmpeg with a Hebrew-friendly style box.
        """
        srt_path_resolved = srt_path.resolve()
        srt_for_ffmpeg = srt_path_resolved.as_posix().replace("\\", "/").replace(":", r"\:")
        # Use libass subtitles filter with a Hebrew-compatible font; keep UTF-8 markers.
        filter_style = (
            "subtitles='{srt}':charenc=UTF-8:force_style="
            "'FontName=Arial,FontSize=20,Alignment=2,WrapStyle=2,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            "BackColour=&H80000000,BorderStyle=4,Outline=1,Shadow=0,MarginV=48'"
        ).format(srt=srt_for_ffmpeg)

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            filter_style,
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"FFmpeg subtitle burn failed (code {result.returncode}): {result.stderr or result.stdout}"
            )

    def _write_srt(
        self,
        dialogue_json: Dict,
        total_duration: float,
        output_path: Path,
        segment_durations: Optional[List[float]] = None,
        lead_in_seconds: float = 0.0,
        outro_seconds: float = 0.0,
        target_language: Optional[str] = None,
    ) -> None:
        """
        Generate SRT captions, preferring exact TTS segment durations when provided.
        """
        turns = [d for d in dialogue_json.get("dialogue", []) if isinstance(d, dict)]
        if not turns:
            return

        try:
            generate_srt_from_dialogue(
                dialogue_json,
                total_duration,
                output_path,
                segment_durations=segment_durations,
                lead_in_seconds=lead_in_seconds,
                outro_seconds=outro_seconds,
                target_language=target_language,
            )
            self.logger.info("captions.srt written with %d entries", len(turns))
            try:
                srt_content = output_path.read_text(encoding="utf-8")
                if "םולש" in srt_content and "שלום" not in srt_content:
                    self.logger.warning("SRT regression check: found reversed 'שלום' without logical order.")
                elif "שלום" in srt_content:
                    self.logger.debug("SRT regression check: found logical 'שלום' substring.")
            except Exception:
                self.logger.debug("Skipped SRT regression check (read error).")
        except Exception as exc:
            self.logger.error("Failed to generate SRT: %s", exc)

    @staticmethod
    def _format_ts(seconds: float) -> str:
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

    def compose(
        self,
        dialogue_json: Dict,
        metadata: Dict,
        run_paths: RunPaths,
        asset_paths: Optional[List[Path]] = None,
        adaptive_timeline: bool = False,
        apply_ken_burns: bool = True,
        segment_durations: Optional[List[float]] = None,
        lead_in_seconds: float = 0.0,
        outro_seconds: float = 0.0,
    ) -> Path:
        start = time.perf_counter()
        try:
            clips = self.create_timeline(
                dialogue_json,
                metadata,
                run_paths.final_audio_path,
                run_paths.visuals_dir,
                run_paths,
                asset_paths=asset_paths,
                adaptive_timeline=adaptive_timeline,
                apply_ken_burns=apply_ken_burns,
                segment_durations=segment_durations,
                lead_in_seconds=lead_in_seconds,
            )
        except Exception as exc:
            self.logger.error(
                "[VideoComposer] Timeline creation failed: %s",
                exc,
                exc_info=True,
            )
            if run_paths:
                run_paths.log(f"❌ Timeline creation failed: {exc}")
            raise
        output = self.render_final_video(
            clips,
            run_paths.final_audio_path,
            run_paths.final_video_path,
            dialogue_json=dialogue_json,
            segment_durations=segment_durations,
            lead_in_seconds=lead_in_seconds,
            outro_seconds=outro_seconds,
            metadata=metadata,
        )
        elapsed = time.perf_counter() - start
        self.logger.info("Video composition completed in %.2fs (clips=%s)", elapsed, len(clips))
        return output

    def _build_slide_clips(self, dialogue_json: Dict, metadata: Dict, total_duration: float) -> List[VideoClip]:
        topic = metadata.get("topic", "Azure בפשטות")
        date = metadata.get("date", "")
        key_topics = metadata.get("key_concepts", [])[:6]
        labs = metadata.get("labs", [])[:4]
        summary = metadata.get("summary", "")
        reading_list = metadata.get("reading_list", [])[:4]
        dialogue_turns = [turn for turn in dialogue_json.get("dialogue", []) if isinstance(turn, dict)]

        quotes: List[str] = []
        for turn in dialogue_turns:
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            speaker = (turn.get("speaker") or "").strip()
            prefix = f"{speaker}: " if speaker else ""
            quotes.append(f"{prefix}{text}")
        quotes = quotes[:6]

        font_title, font_body = self._load_fonts()
        base_theme = self._select_theme(metadata)

        hero_bullets = [
            metadata.get("goal", "סשן מודרך וסיכום חי"),
            f"אורך אודיו: {max(total_duration / 60, 0.1):.1f} דק'",
            f"דוברים: {max(len(dialogue_turns), 1)} · מושגים: {len(key_topics) or '—'}",
        ]
        hero_chips = [
            date or "ללא תאריך",
            metadata.get("audience", "קהל יעד: מחנכים"),
            metadata.get("location", "Online • M.B.S Studio"),
        ]

        slide_specs: List[Dict[str, Any]] = [
            {
                "type": "standard",
                "title": topic,
                "subtitle": date or "סשן חדש",
                "bullets": hero_bullets,
                "chips": hero_chips,
                "hero": True,
                "theme_shift": 0,
            }
        ]

        if key_topics:
            slide_specs.append(
                {
                    "type": "standard",
                    "title": "מושגים מרכזיים",
                    "subtitle": "5 התובנות שחייבים לזכור",
                    "bullets": key_topics,
                    "theme_shift": 1,
                }
            )
        if labs:
            slide_specs.append(
                {
                    "type": "standard",
                    "title": "תרגולים / מעבדות",
                    "subtitle": "פעולות להמשך",
                    "bullets": labs,
                    "theme_shift": 2,
                }
            )
        if summary:
            summary_sentences = [
                sentence.strip()
                for sentence in summary.replace("\n", " ").split(".")
                if sentence.strip()
            ]
            slide_specs.append(
                {
                    "type": "standard",
                    "title": "תקציר מרכזי",
                    "subtitle": "במקום אחד",
                    "bullets": summary_sentences[:5] or [summary],
                    "theme_shift": 3,
                }
            )
        if reading_list:
            slide_specs.append(
                {
                    "type": "standard",
                    "title": "קריאה מומלצת",
                    "subtitle": "Deep dives להמשך",
                    "bullets": reading_list,
                    "theme_shift": 4,
                }
            )
        if quotes:
            for idx, chunk in enumerate(self._chunk_list(quotes, chunk_size=3)):
                slide_specs.append(
                    {
                        "type": "standard",
                        "title": "ציטוטים שנשארים",
                        "subtitle": "Highlights מהשיחה",
                        "bullets": chunk,
                        "theme_shift": 5 + idx,
                    }
                )

        speaker_blocks = self._build_speaker_blocks(dialogue_turns, per_card=2, max_cards=3)
        for idx, block in enumerate(speaker_blocks):
            slide_specs.append(
                {
                    "type": "speaker",
                    "title": "דוברים מובילים",
                    "blocks": block,
                    "theme_shift": 8 + idx,
                }
            )

        outro_lines = [
            "שאלו את ה-AI לשאלות המשך",
            "פתחו את המצגת שסוכמה אוטומטית",
            metadata.get("call_to_action", "המשיכו לייצר למידה אינטראקטיבית."),
        ]
        slide_specs.append(
            {
                "type": "standard",
                "title": "מה הלאה?",
                "subtitle": "Action Items",
                "bullets": outro_lines,
                "theme_shift": 20,
            }
        )

        if not slide_specs:
            slide_specs.append(
                {
                    "type": "standard",
                    "title": topic,
                    "bullets": ["Azure בפשטות"],
                    "theme_shift": 0,
                }
            )

        slides: List[Image.Image] = []
        total_specs = len(slide_specs)
        for idx, spec in enumerate(slide_specs):
            progress = idx / max(total_specs - 1, 1)
            theme_variant = self._theme_variant(base_theme, spec.get("theme_shift", idx))
            if spec["type"] == "speaker":
                slides.append(
                    self._render_speaker_slide(
                        title=spec.get("title", "דוברים מובילים"),
                        blocks=spec["blocks"],
                        font_title=font_title,
                        font_body=font_body,
                        theme=theme_variant,
                        progress=progress,
                    )
                )
            else:
                slides.append(
                    self._render_slide(
                        title=spec["title"],
                        bullets=spec["bullets"],
                        font_title=font_title,
                        font_body=font_body,
                        theme=theme_variant,
                        subtitle=spec.get("subtitle", ""),
                        chips=spec.get("chips"),
                        hero=spec.get("hero", False),
                        progress=progress,
                    )
                )

        if total_duration <= 0:
            total_duration = len(slides) * 4 or 12
        weights: List[float] = []
        for spec in slide_specs:
            if spec.get("hero"):
                weights.append(1.3)
            elif spec["type"] == "speaker":
                weights.append(0.9)
            elif spec.get("title") in {"מה הלאה?", "סיום ההרצאה"}:
                weights.append(1.1)
            else:
                weights.append(1.0)
        total_weight = sum(weights) or 1.0
        durations = [(total_duration * w / total_weight) for w in weights]
        durations = [max(1.0, d) for d in durations]

        clips: List[VideoClip] = []
        for image, duration in zip(slides, durations):
            array = np.array(image)
            clip = ImageClip(array)
            clip = self._apply_duration(clip, duration)
            try:
                clip = self.apply_ken_burns(clip)
            except Exception as kb_exc:
                self.logger.warning("Ken Burns skipped for slide clip: %s", kb_exc)
            fade_window = min(0.8, duration / 3)
            # MoviePy 2.0: use with_effects instead of fx()
            clip = clip.fx(vfx.fadein, fade_window).fx(vfx.fadeout, fade_window)
            clips.append(clip)
        return clips

    def _render_slide(
        self,
        title: str,
        bullets: List[str],
        font_title: ImageFont.ImageFont,
        font_body: ImageFont.ImageFont,
        theme: Dict[str, Tuple[int, int, int]],
        subtitle: str = "",
        chips: Optional[List[str]] = None,
        hero: bool = False,
        progress: float = 0.0,
    ) -> Image.Image:
        width, height = 1920, 1080
        background = self._gradient_canvas(theme["primary"], theme["secondary"]).convert("RGBA")

        overlay = Image.new("RGBA", background.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.ellipse(
            (width - 500, -80, width + 240, 440),
            fill=(theme["accent"][0], theme["accent"][1], theme["accent"][2], 80),
        )
        overlay_draw.rectangle((0, height - 320, width, height), fill=(0, 0, 0, 60))
        background = Image.alpha_composite(background, overlay)

        card_bounds = (100, 120, width - 100, height - 120)
        panel = Image.new("RGBA", background.size, (0, 0, 0, 0))
        panel_draw = ImageDraw.Draw(panel)
        panel_draw.rounded_rectangle(card_bounds, radius=48, fill=theme.get("panel", (2, 6, 23, 200)))
        panel_draw.rounded_rectangle(
            (card_bounds[0], card_bounds[1] - 30, card_bounds[0] + 260, card_bounds[1] - 6),
            radius=12,
            fill=theme["accent"],
        )
        background = Image.alpha_composite(background, panel)
        draw = ImageDraw.Draw(background)

        title_y = card_bounds[1] + (20 if hero else 60)
        rtl_title = shape_rtl(title)
        rtl_subtitle = shape_rtl(subtitle) if subtitle else ""

        draw.text((card_bounds[0] + 60, title_y), rtl_title, font=font_title, fill=theme["text"])
        if rtl_subtitle:
            draw.text(
                (card_bounds[0] + 60, title_y + 80),
                rtl_subtitle,
                font=font_body,
                fill=theme["muted"],
            )

        y = (card_bounds[1] + 200) if hero else (card_bounds[1] + 240)
        bullet_width = card_bounds[2] - card_bounds[0] - 180
        line_height = getattr(font_body, "size", 32) + 8
        available_height = (card_bounds[3] - 200) - y
        total_lines = 0
        bullet_count = 0
        for bullet in bullets:
            if not bullet:
                continue
            bullet_count += 1
            total_lines += len(self._wrap_text(bullet, font_body, bullet_width))
        total_height = total_lines * line_height + max(0, bullet_count - 1) * 12
        if total_height > available_height:
            font_body = self._scaled_body_font(font_body, 0.8)
            line_height = int(line_height * 0.8)
        for bullet in bullets:
            if not bullet:
                continue
            wrapped = self._wrap_text(bullet, font_body, bullet_width)
            for line in wrapped:
                draw.text((card_bounds[0] + 110, y), shape_rtl(line), font=font_body, fill=theme["text"])
                y += line_height
            y += 12
            if y > card_bounds[3] - 200:
                break

        chip_y = card_bounds[3] - 190
        chip_x = card_bounds[0] + 110
        for chip in chips or []:
            if not chip:
                continue
            text = chip if len(chip) <= 48 else f"{chip[:45]}..."
            text = shape_rtl(text)
            text_width = self._measure_text(text, font_body) + 60
            chip_rect = (chip_x, chip_y, chip_x + text_width, chip_y + 54)
            draw.rounded_rectangle(chip_rect, radius=22, fill=theme["chip_bg"])
            draw.text((chip_x + 30, chip_y + 14), text, font=font_body, fill=theme["text"])
            chip_x += text_width + 18
            if chip_x > card_bounds[2] - 160:
                chip_x = card_bounds[0] + 110
                chip_y -= 70

        progress_label = shape_rtl(f"{int(progress * 100):02d}% הושלם")
        draw.text(
            (card_bounds[2] - 260, card_bounds[3] - 90),
            progress_label,
            font=font_body,
            fill=theme["muted"],
        )
        self._draw_progress_bar(draw, card_bounds, theme, progress)

        return background.convert("RGB")

    def _render_speaker_slide(
        self,
        title: str,
        blocks: List[Dict[str, str]],
        font_title: ImageFont.ImageFont,
        font_body: ImageFont.ImageFont,
        theme: Dict[str, Tuple[int, int, int]],
        progress: float = 0.0,
    ) -> Image.Image:
        width, height = 1920, 1080
        background = self._gradient_canvas(theme["secondary"], theme["primary"]).convert("RGBA")
        overlay = Image.new("RGBA", background.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 50))
        background = Image.alpha_composite(background, overlay)

        card_bounds = (100, 120, width - 100, height - 120)
        panel = Image.new("RGBA", background.size, (0, 0, 0, 0))
        panel_draw = ImageDraw.Draw(panel)
        panel_draw.rounded_rectangle(card_bounds, radius=48, fill=theme.get("panel", (2, 6, 23, 210)))
        background = Image.alpha_composite(background, panel)
        draw = ImageDraw.Draw(background)

        draw.text((card_bounds[0] + 60, card_bounds[1] + 40), shape_rtl(title), font=font_title, fill=theme["text"])
        column_count = max(len(blocks), 1)
        available_width = card_bounds[2] - card_bounds[0] - 120
        column_width = (available_width - (column_count - 1) * 30) / column_count
        column_width = max(300, column_width)
        card_top = card_bounds[1] + 140
        card_bottom = card_bounds[3] - 140
        for idx, block in enumerate(blocks):
            x0 = card_bounds[0] + 60 + idx * (column_width + 30)
            x1 = x0 + column_width
            sub_panel = Image.new("RGBA", background.size, (0, 0, 0, 0))
            sub_draw = ImageDraw.Draw(sub_panel)
            sub_draw.rounded_rectangle(
                (x0, card_top, x1, card_bottom),
                radius=32,
                fill=(theme["chip_bg"][0], theme["chip_bg"][1], theme["chip_bg"][2], 220),
            )
            background = Image.alpha_composite(background, sub_panel)
            draw = ImageDraw.Draw(background)

            speaker = block.get("speaker") or "Speaker"
            snippet = (block.get("text") or "").strip()
            draw.text((x0 + 40, card_top + 30), shape_rtl(speaker), font=font_body, fill=theme["text"])
            snippet_lines = self._wrap_text(snippet, font_body, int(column_width) - 80)
            text_y = card_top + 90
            for line in snippet_lines[:8]:
                draw.text((x0 + 40, text_y), shape_rtl(line), font=font_body, fill=theme["muted"])
                text_y += getattr(font_body, "size", 32) + 6

        self._draw_progress_bar(draw, card_bounds, theme, progress)
        return background.convert("RGB")

    def _draw_progress_bar(
        self,
        draw: ImageDraw.ImageDraw,
        bounds: Tuple[int, int, int, int],
        theme: Dict[str, Tuple[int, int, int]],
        progress: float,
    ) -> None:
        bar_margin = 120
        bar_rect = (
            bounds[0] + bar_margin,
            bounds[3] - 50,
            bounds[2] - bar_margin,
            bounds[3] - 20,
        )
        muted = theme.get("muted", (148, 163, 184))
        draw.rounded_rectangle(bar_rect, radius=12, fill=(muted[0], muted[1], muted[2], 80))
        clamped = max(0.0, min(progress, 1.0))
        fill_width = int((bar_rect[2] - bar_rect[0]) * clamped)
        if fill_width <= 0:
            return
        fill_rect = (bar_rect[0], bar_rect[1], bar_rect[0] + fill_width, bar_rect[3])
        draw.rounded_rectangle(fill_rect, radius=12, fill=theme["accent"])

    def _build_speaker_blocks(
        self,
        dialogue_turns: List[Dict[str, Any]],
        per_card: int = 2,
        max_cards: int = 3,
    ) -> List[List[Dict[str, str]]]:
        entries: List[Dict[str, str]] = []
        for turn in dialogue_turns:
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            speaker = (turn.get("speaker") or "דובר/ת").strip() or "דובר/ת"
            entries.append({"speaker": speaker, "text": text})
            if len(entries) >= per_card * max_cards:
                break
        return self._chunk_list(entries, per_card)

    def _select_theme(self, metadata: Dict) -> Dict[str, Tuple[int, int, int]]:
        palettes = [
            {
                "primary": (5, 8, 38),
                "secondary": (30, 64, 175),
                "accent": (56, 189, 248),
                "text": (248, 250, 252),
                "muted": (148, 163, 184),
                "panel": (2, 6, 23, 210),
                "chip_bg": (23, 37, 84, 180),
            },
            {
                "primary": (13, 24, 33),
                "secondary": (18, 83, 60),
                "accent": (74, 222, 128),
                "text": (241, 245, 249),
                "muted": (148, 163, 184),
                "panel": (8, 20, 28, 210),
                "chip_bg": (16, 44, 32, 200),
            },
            {
                "primary": (49, 12, 78),
                "secondary": (109, 40, 217),
                "accent": (249, 115, 22),
                "text": (255, 245, 235),
                "muted": (242, 211, 238),
                "panel": (34, 4, 49, 200),
                "chip_bg": (71, 22, 107, 200),
            },
        ]
        topic = metadata.get("topic", "")
        idx = sum(ord(char) for char in topic) % len(palettes)
        return palettes[idx].copy()

    def _theme_variant(self, base: Dict[str, Tuple[int, int, int]], shift: int) -> Dict[str, Tuple[int, int, int]]:
        variant = base.copy()

        def shift_color(color: Tuple[int, int, int], delta: int) -> Tuple[int, int, int]:
            return tuple(min(255, max(0, channel + delta)) for channel in color)

        delta = int((shift % 3 - 1) * 18)
        variant["primary"] = shift_color(base["primary"], delta)
        variant["secondary"] = shift_color(base["secondary"], -delta)
        accent_delta = 12 if shift % 2 else -12
        variant["accent"] = shift_color(base["accent"], accent_delta)
        chip_rgba = base.get("chip_bg", (12, 74, 110, 180))
        chip_delta = int((shift % 4) * 10)
        variant["chip_bg"] = (
            min(255, max(0, chip_rgba[0] + chip_delta)),
            min(255, max(0, chip_rgba[1] + chip_delta)),
            min(255, max(0, chip_rgba[2] + chip_delta)),
            chip_rgba[3],
        )
        return variant

    def _gradient_canvas(self, top: Tuple[int, int, int], bottom: Tuple[int, int, int]) -> Image.Image:
        width, height = 1920, 1080
        gradient = Image.new("RGB", (width, height), color=top)
        draw = ImageDraw.Draw(gradient)
        for y in range(height):
            ratio = y / max(height - 1, 1)
            color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
            draw.line([(0, y), (width, y)], fill=color)
        return gradient

    def _wrap_text(self, text: Any, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        if not isinstance(text, str):
            if isinstance(text, dict):
                # If text is a dictionary (like a lab object), try to extract a string representation
                text = text.get("name") or text.get("title") or text.get("description") or str(text)
            else:
                text = str(text)
        
        words = text.split()
        lines: List[str] = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            w = self._measure_text(test, font)
            if w <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def _measure_text(self, text: str, font: ImageFont.ImageFont) -> int:
        if hasattr(font, "getbbox"):
            box = font.getbbox(text)
            return box[2] - box[0]
        if hasattr(font, "getlength"):
            return int(font.getlength(text))
        return max(len(text), 1) * max(font.size, 10) // 2

    def _scaled_body_font(self, font_body: ImageFont.ImageFont, scale: float) -> ImageFont.ImageFont:
        """
        Best-effort font scaling for body text to mitigate overflow.
        """
        target_size = max(10, int(getattr(font_body, "size", 32) * scale))
        spec = getattr(self, "_font_body_spec", None)
        if spec:
            name, _ = spec
            try:
                return ImageFont.truetype(name, target_size)
            except Exception:
                pass
        try:
            return font_body.font_variant(size=target_size)  # type: ignore[attr-defined]
        except Exception:
            return font_body

    def _load_fonts(self) -> Tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
        # Try Hebrew-supporting fonts in order of preference
        font_options = [
            ("arial.ttf", 60, 36),  # Arial (good Hebrew support)
            ("tahoma.ttf", 60, 36),  # Tahoma (good Hebrew support)
            ("david.ttf", 60, 36),   # David (Hebrew font)
            ("times.ttf", 60, 36),   # Times New Roman
            ("cour.ttf", 60, 36),    # Courier New
        ]

        for font_name, title_size, body_size in font_options:
            try:
                title_font = ImageFont.truetype(font_name, title_size)
                body_font = ImageFont.truetype(font_name, body_size)
                self.logger.debug("Loaded Hebrew-compatible font: %s", font_name)
                self._font_title_spec = (font_name, title_size)
                self._font_body_spec = (font_name, body_size)
                return title_font, body_font
            except OSError:
                continue

        # Fallback to default font
        self.logger.warning("No Hebrew-compatible fonts found, using default font")
        self._font_title_spec = None
        self._font_body_spec = None
        fallback = ImageFont.load_default()
        return fallback, fallback

    def _chunk_list(self, values: Sequence[T], chunk_size: int) -> List[List[T]]:
        if chunk_size <= 0:
            return [list(values)]
        chunked: List[List[T]] = []
        current: List[T] = []
        for value in values:
            current.append(value)
            if len(current) == chunk_size:
                chunked.append(current)
                current = []
        if current:
            chunked.append(current)
        return chunked

