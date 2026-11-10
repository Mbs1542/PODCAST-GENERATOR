"""Background jobs for the web app, run one at a time on a worker thread.

prepare: (download) -> transcribe -> engine stages 1-3, stopping at the plan for review
write:   engine stages 4-5 from the (possibly edited) plan -> dialogue.json
audio:   TTS for every turn -> stitched MP3 -> chapter times
images:  AI illustrations for the project's gallery, from the plan
video:   the episode audio over the gallery's chosen images, with titles and the logo

A failed job keeps the project's last good status and records a readable error, so
the user can fix the cause (usually settings) and retry the same step.
"""

from __future__ import annotations

import inspect
import json
import queue
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..audio.dialogue_tts import DIALOGUE_PROVIDERS, DialogueTTSError, build_dialogue_synth, render_dialogue
from ..engine import ScriptEngine
from ..transcribe import HebrewTranscriber, TranscribeError, download_url, model_for_language, normalize_quality, to_srt, to_text
from ..utils import CostTracker, Settings, VoiceProfileManager, get_logger
from ..utils.keep_awake import keep_awake
from .projects import Project, ProjectStore
from .settings_store import SettingsStore, apply_project_options

JOB_KINDS = ("prepare", "write", "audio", "images", "video")
MAX_AI_IMAGES = 12
_SEGMENT_RE = re.compile(r"segment (\d+)/(\d+)")
_CLEAN_RE = re.compile(r"cleaning part (\d+)/(\d+)")
# The analysis runs three steps; each finished step names the next one
_NEXT_STEP = {"Stage 1:": "מנתח את השיעור: מושגים, דוגמאות ומטרות (שלב 2 מתוך 3)",
              "Stage 2:": "בונה תוכנית לפרק (שלב 3 מתוך 3)"}
_CHUNK_RE = re.compile(r"Chunk (\d+)/(\d+)")


class JobError(RuntimeError):
    """An error whose message is already written for the user."""


def _audio_seconds(parts: List[Path]) -> float:
    """Length of rendered audio (for pricing); unreadable files count as zero."""
    total = 0.0
    for part in parts:
        try:
            import soundfile

            total += soundfile.info(str(part)).duration
        except Exception:
            try:
                from pydub import AudioSegment

                total += AudioSegment.from_file(part).duration_seconds
            except Exception:
                continue
    return total


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, (JobError, TranscribeError)):
        return str(exc)
    from ..engine.llm import is_daily_quota_error

    if is_daily_quota_error(exc):
        return ("נגמרה המכסה היומית של Gemini לחשבון הזה (במסלול החינמי יש מספר קטן של בקשות ביום לכל מודל, "
                "ופרק אחד צורך כ-15 עד 25). המכסה מתחדשת פעם ביום. אפשר להמשיך מחר מאותה נקודה, "
                "להפעיל חיוב בפרויקט ב-Google AI Studio, או לבחור בהגדרות מודל כתיבה אחר.")
    if getattr(exc, "code", None) == 429:
        return "Gemini הגביל זמנית את קצב הבקשות. נסה שוב בעוד דקה."
    try:
        from openai import APIConnectionError, AuthenticationError, NotFoundError, RateLimitError

        if isinstance(exc, AuthenticationError):
            return "מפתח Azure OpenAI שגוי (401). בדוק אותו בהגדרות."
        if isinstance(exc, NotFoundError):
            return "Azure OpenAI: ה-Endpoint או שם הפריסה שגויים (404). בדוק אותם בהגדרות."
        if isinstance(exc, APIConnectionError):
            return "אין חיבור ל-Azure OpenAI. בדוק את החיבור לאינטרנט ואת ה-Endpoint."
        if isinstance(exc, RateLimitError):
            return "Azure OpenAI הגביל את קצב הבקשות. נסה שוב בעוד דקה."
    except ImportError:
        pass
    return f"{exc.__class__.__name__}: {exc}"


class JobRunner:
    def __init__(
        self,
        store: ProjectStore,
        settings_store: SettingsStore,
        llms_factory: Optional[Callable[[Settings, CostTracker], Any]] = None,
        tts_factory: Optional[Callable[[Settings, Optional[str], CostTracker], Any]] = None,
        transcriber_factory: Optional[Callable[[Callable[[str], None]], Any]] = None,
        dialogue_factory: Optional[Callable[[Settings, str], Any]] = None,
        image_maker_factory: Optional[Callable[[Settings], Any]] = None,
        video_renderer: Optional[Callable[..., Path]] = None,
    ) -> None:
        self.store = store
        self.settings_store = settings_store
        self.brand: Any = None  # set by the app: logo, fonts and accent for the video
        self.delivery: Any = None  # set by the app: the podcast's own folder and Google Drive
        self._llms_factory = llms_factory
        self._tts_factory = tts_factory
        self._dialogue_factory = dialogue_factory
        self._image_maker_factory = image_maker_factory
        self._video_renderer = video_renderer
        self._params: Dict[str, Dict[str, Any]] = {}
        self._transcriber_factory = transcriber_factory or (
            lambda log, quality="balanced": HebrewTranscriber(model=model_for_language("he", quality), quality=quality, log=log))
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._busy = threading.Event()
        self.logger = get_logger(self.__class__.__name__)
        threading.Thread(target=self._worker, name="mbs-jobs", daemon=True).start()

    @property
    def busy(self) -> bool:
        return self._busy.is_set() or not self._queue.empty()

    def submit(self, project_id: str, kind: str, force: bool = False, params: Optional[Dict[str, Any]] = None) -> Project:
        if kind not in JOB_KINDS:
            raise JobError(f"Unknown job: {kind}")
        project = self.store.get(project_id)
        if project is None:
            raise JobError("הפרויקט לא נמצא")
        if project.running:
            raise JobError("כבר רץ תהליך על הפרויקט הזה")
        self._params[project_id] = dict(params or {})
        project = self.store.update(project_id, running=kind, stage="ממתין בתור", progress=None, error="", notice="")
        self._queue.put((project_id, kind, force))
        return project

    def recover_interrupted(self) -> None:
        """After a restart, no job is running: clear flags left by a crash or a closed app."""
        for project in self.store.list():
            if project.running:
                self.store.update(project.id, running=None, stage="", progress=None,
                                  error="התהליך הופסק כשהאפליקציה נסגרה. אפשר להפעיל אותו שוב.")

    # ------------------------------------------------------------------ worker

    def _worker(self) -> None:
        while True:
            project_id, kind, force = self._queue.get()
            self._busy.set()
            try:
                with keep_awake():  # a 40-minute transcription must not be cut off by sleep
                    getattr(self, f"_run_{kind}")(project_id, force)
            except Exception as exc:
                message = friendly_error(exc)
                self.logger.exception("Job %s failed for %s", kind, project_id)
                self.store.append_log(project_id, f"שגיאה: {message}")
                self.store.update(project_id, error=message)
            finally:
                try:
                    self.store.update(project_id, running=None, stage="", progress=None)
                except KeyError:
                    pass  # deleted while running
                self._busy.clear()

    def _stage(self, project_id: str, label: str, progress: Optional[float] = None) -> None:
        self.store.update(project_id, stage=label, progress=progress)
        self.store.append_log(project_id, label)

    def _engine_log(self, project_id: str) -> Callable[[str], None]:
        def log(message: str) -> None:
            self.store.append_log(project_id, message)
            match = _SEGMENT_RE.search(message)
            if match:
                done, total = int(match.group(1)), int(match.group(2))
                self.store.update(project_id, progress=done / total, stage=f"כותב ובודק מקטע {done} מתוך {total}")
                return
            match = _CLEAN_RE.search(message)
            if match:
                done, total = int(match.group(1)), int(match.group(2))
                self.store.update(project_id, progress=(done - 1) / total,
                                  stage="מנקה את התמלול (שלב 1 מתוך 3)")
                return
            for prefix, next_step in _NEXT_STEP.items():
                if message.startswith(prefix) and "cleaning part" not in message:
                    self.store.update(project_id, progress=None, stage=next_step)
        return log

    def _settings(self, project: Project) -> Settings:
        settings, error = self.settings_store.load_settings()
        if settings is None:
            raise JobError("אין מודל לכתיבת התסריט. במסך ההגדרות בחר מי כותב (למשל Gemini) ומלא את המפתח שלו.")
        return apply_project_options(settings, project.options)

    def _engine(self, project_id: str, settings: Settings, costs: CostTracker) -> ScriptEngine:
        llms = self._llms_factory(settings, costs) if self._llms_factory else None
        log = self._engine_log(project_id)
        engine = ScriptEngine(settings, costs, llms=llms, log=log)
        for client in (engine.llms.cloud, engine.llms.local):
            if client is not None:
                client.on_retry = log
        return engine

    def _transcript_text(self, project: Project) -> str:
        path = self.store.transcript_path(project)
        if path is None:
            raise JobError("אין תמלול לפרויקט הזה")
        return path.read_text(encoding="utf-8-sig")

    def _hints(self, project: Project) -> Dict[str, Any]:
        # The title is a useful hint only when the user typed it, not when it is a file name
        return {"topic": project.title} if project.options.get("title_given") else {}

    # ------------------------------------------------------------------ jobs

    def _run_prepare(self, project_id: str, force: bool) -> None:
        project = self.store.get(project_id)
        if self.store.transcript_path(project) is None:
            self._transcribe(project)
            project = self.store.update(project_id, status="transcribed")

        settings, _ = self.settings_store.load_settings()
        if settings is None:
            self.store.update(project_id, notice="התמלול מוכן. כדי להמשיך לפודקאסט, בחר במסך ההגדרות מי כותב את התסריט (למשל Gemini), מלא את המפתח ולחץ \"המשך\".")
            return

        settings = apply_project_options(settings, project.options)
        costs = CostTracker()
        self._stage(project_id, "מנתח את השיעור ובונה תוכנית לפרק")
        self._engine(project_id, settings, costs).run(
            self._transcript_text(project), self._hints(project), self.store.engine_dir(project),
            force=force, stop_after="plan",
        )
        self.store.append_log(project_id, f"עלות עד כאן: ${costs.openai_cost:.3f}")
        self.store.update(project_id, status="plan_ready")

    def _transcribe(self, project: Project) -> None:
        source_dir = self.store.source_dir(project)
        if project.source_kind == "url":
            self._stage(project.id, "מוריד את ההקלטה")
            media = download_url(project.source_name, source_dir)
        else:
            files = [p for p in source_dir.iterdir() if p.is_file()]
            if not files:
                raise JobError("קובץ המקור חסר")
            media = files[0]

        self._stage(project.id, "מתמלל (בפעם הראשונה המודל העברי יורד, כ-1.6GB)", 0.0)
        last = [0.0]

        def progress(position: float, duration: float) -> None:
            if duration and time.time() - last[0] > 2:
                last[0] = time.time()
                self.store.update(project.id, progress=min(position / duration, 1.0),
                                  stage=f"מתמלל: {position / 60:.0f} מתוך {duration / 60:.0f} דקות")

        log = lambda message: self.store.append_log(project.id, message)  # noqa: E731
        quality = normalize_quality(self.settings_store.read_env().get("TRANSCRIBE_QUALITY"))
        if "quality" in inspect.signature(self._transcriber_factory).parameters:
            transcriber = self._transcriber_factory(log, quality=quality)
        else:  # factories that predate quality levels
            transcriber = self._transcriber_factory(log)
        segments = transcriber.transcribe(media, progress)
        if not segments:
            raise JobError("לא זוהה דיבור בהקלטה")
        folder = self.store.folder(project)
        (folder / "transcript.srt").write_text(to_srt(segments), encoding="utf-8")
        (folder / "transcript.txt").write_text(to_text(segments), encoding="utf-8")
        self.store.append_log(project.id, f"התמלול הושלם: {len(segments)} קטעים")

    def _run_write(self, project_id: str, force: bool) -> None:
        project = self.store.get(project_id)
        settings = self._settings(project)
        engine_dir = self.store.engine_dir(project)
        if force:
            (engine_dir / "04_script.json").unlink(missing_ok=True)
        costs = CostTracker()
        self._stage(project_id, "כותב את התסריט")
        result = self._engine(project_id, settings, costs).run(
            self._transcript_text(project), self._hints(project), engine_dir
        )
        paths = self.store.run_paths(project)
        paths.dialogue_path.write_text(json.dumps(result.dialogue, ensure_ascii=False, indent=2), encoding="utf-8")
        # A new script makes earlier per-line audio stale (conversation chunks are keyed by content)
        for wav in paths.audio_dir.glob("*.wav"):
            wav.unlink()
        (self.store.folder(project) / "chapters.json").unlink(missing_ok=True)
        self.store.append_log(project_id, f"עלות הכתיבה: ${costs.openai_cost:.3f}")
        self.store.update(project_id, status="script_ready")

    def _voice_provider(self, project: Project, settings: Settings) -> str:
        chosen = project.options.get("voice") or "auto"
        provider = settings.default_tts_provider if chosen == "auto" else chosen
        if not settings.tts_ready(provider):
            raise JobError(
                f"אין מפתח לשירות הקולות ({provider}). מלא אותו במסך ההגדרות, או בחר שירות אחר."
            )
        return provider

    def _run_audio(self, project_id: str, force: bool) -> None:
        from ..audio import PodcastStitcher, create_tts_synthesizer

        project = self.store.get(project_id)
        settings = self._settings(project)
        paths = self.store.run_paths(project)
        if not paths.dialogue_path.exists():
            raise JobError("אין תסריט עדיין")
        provider = self._voice_provider(project, settings)
        costs = CostTracker(tts_provider=provider)
        paths.log(f"Using TTS provider: {provider}")

        if provider in DIALOGUE_PROVIDERS:
            self._check_voices_ready(project, settings, provider, paths, force)
            parts, starts = self._render_conversation(project, settings, provider, paths, costs, force)
        else:
            self._stage(project_id, f"מפיק קולות ({provider})")
            if self._tts_factory:
                tts = self._tts_factory(settings, provider, costs)
            else:
                voices = VoiceProfileManager(settings.voice_profile_path, settings.default_voice_profile)
                tts = create_tts_synthesizer(settings=settings, cost_tracker=costs, voice_manager=voices, provider=provider)
            results = tts.synthesize(paths.dialogue_path, paths, force=force)
            parts = [Path(r["path"]) for r in results]
            starts, position = [], 0.0
            for item in results:
                starts.append(position)
                position += float(item.get("duration") or 0.0)

        self._stage(project_id, "מחבר את הפרק")
        stitcher = PodcastStitcher(settings, costs)
        lead_in = stitcher._load_music(settings.intro_music, duration_ms=3000).duration_seconds
        lesson = self.store.read_json(self.store.lesson_metadata_path(project)) or {}
        stitcher.build(parts, lesson, paths)
        self._write_chapters(project, paths, [lead_in + s for s in starts])
        if costs.tts_cost:
            self.store.append_log(project_id, f"עלות הקולות: ${costs.tts_cost:.2f} (חלקים שכבר הוקלטו לא נספרים שוב)")
        self.store.append_log(project_id, f"הפרק מוכן ({provider}).")
        self.store.update(project_id, status="audio_ready")
        self.deliver(project_id)

    def deliver(self, project_id: str, upload: Optional[bool] = None) -> Optional[Path]:
        """The lesson summary, then the podcast's own folder (lecture, transcripts, podcast, video, summary),
        then Google Drive when automatic upload is on (or upload=True).

        Nothing here can fail a finished episode: problems are written to the project's log.
        """
        from ..video.slideshow import ACCENTS
        from .delivery import DeliveryError, MEDIA_SUFFIXES, build_output_folder, output_folder_for
        from .summary import SummaryError, project_summary, render_html, write_docx

        project = self.store.get(project_id)
        paths = self.store.run_paths(project)
        log = lambda message: self.store.append_log(project_id, message)  # noqa: E731
        try:
            model = project_summary(self.store, project)
        except SummaryError as exc:
            log(f"סיכום השיעור לא נוצר: {exc}")
            return None
        try:
            accent = ACCENTS.get(self.brand.prefs().get("accent", "teal"), ACCENTS["teal"]) if self.brand else ACCENTS["teal"]
            page = render_html(model, accent)
            (self.store.folder(project) / "summary.html").write_text(page, encoding="utf-8")
            if self.delivery is None:
                return None
            docx = write_docx(model, self.store.folder(project) / "summary.docx", accent)
            options = project.options
            lectures = [Path(m) for m in options.get("source_media", []) if Path(m).exists()]
            lectures += [f for f in self.store.source_dir(project).glob("*") if f.suffix.lower() in MEDIA_SUFFIXES]
            transcripts = [(Path(t), Path(t).name) for t in options.get("source_transcripts", []) if Path(t).exists()]
            if not transcripts:  # a project that transcribed its own upload
                name = re.sub(r'[\\/:*?"<>|]+', "_", project.title).strip() or "podcast"
                folder = self.store.folder(project)
                transcripts = [(folder / f"transcript{ext}", f"{name} - תמלול{ext}") for ext in (".srt", ".txt")
                               if (folder / f"transcript{ext}").exists()]
            target = output_folder_for(self.delivery.root_for(options), project.title, project.id)
            video = paths.final_video_path if paths.final_video_path.exists() else None
            files = build_output_folder(target, project.id, project.title, lectures=lectures, transcripts=transcripts,
                                        audio=paths.final_audio_path, video=video, summary_html=page,
                                        summary_docx=docx, log=log)
            self.store.update(project_id, options={**self.store.get(project_id).options, "output_folder": str(target)})
            log(f"תיקיית הפרק מוכנה ({len(files)} קבצים): {target}")
        except (DeliveryError, OSError) as exc:
            log(f"תיקיית הפרק לא נוצרה: {exc}")
            return None
        except Exception as exc:  # never fail a finished episode
            self.logger.exception("Delivery failed for %s", project_id)
            log(f"תיקיית הפרק לא נוצרה ({exc.__class__.__name__})")
            return None
        if upload or (upload is None and self.delivery.should_upload()):
            def done(url: str) -> None:
                self.store.update(project_id, options={**self.store.get(project_id).options, "drive_url": url})
            self.delivery.upload_async(project_id, target, log, done)
        return target

    def _check_voices_ready(self, project: Project, settings: Settings, provider: str, paths: Any, force: bool) -> None:
        """Stop before the first character is spent when the voices would fail part-way.

        Checks the account only for real ElevenLabs runs; if the check itself cannot reach ElevenLabs the
        run goes on as before (the old behaviour), with a note in the log.
        """
        if provider != "elevenlabs" or self._dialogue_factory is not None:
            return
        from ..audio.dialogue_tts import check_elevenlabs_ready

        dialogue = self.store.read_json(paths.dialogue_path) or {}
        resuming = not force and any((paths.audio_dir / "conversation").glob("*.mp3"))
        needed = None if resuming else sum(len(t.get("text", "")) for t in dialogue.get("dialogue", []))
        self._stage(project.id, "בודק את חשבון ElevenLabs (תווים וקולות)")
        try:
            problems = check_elevenlabs_ready(settings.elevenlabs_api_key, {
                "Roee": settings.elevenlabs_voice_roee, "Noa": settings.elevenlabs_voice_noa}, needed)
        except Exception as exc:  # no answer from ElevenLabs: do not block, the voices step reports real errors
            self.store.append_log(project.id, f"לא הצלחתי לבדוק את חשבון ElevenLabs מראש ({exc}); ממשיך")
            return
        if problems:
            raise JobError("לא התחלתי להקליט, כדי לא לבזבז תווים: " + " ".join(problems))
        if needed:
            self.store.append_log(project.id, f"ElevenLabs: יש מספיק תווים לפרק (צריך כ-{needed:,})")

    def _render_conversation(
        self, project: Project, settings: Settings, provider: str, paths: Any, costs: CostTracker, force: bool
    ) -> tuple:
        """Both hosts in one generation per chunk (ElevenLabs Text to Dialogue / Gemini multi-speaker)."""
        chunk_dir = paths.audio_dir / "conversation"
        if force and chunk_dir.exists():
            shutil.rmtree(chunk_dir)
        synth = self._dialogue_factory(settings, provider) if self._dialogue_factory else build_dialogue_synth(settings, provider)
        dialogue = self.store.read_json(paths.dialogue_path) or {}
        self._stage(project.id, f"מפיק את השיחה ({provider})", 0.0)

        def log(message: str) -> None:
            self.store.append_log(project.id, message)
            match = _CHUNK_RE.search(message)
            if match:
                done, total = int(match.group(1)), int(match.group(2))
                self.store.update(project.id, progress=(done - 1) / total, stage=f"מפיק את השיחה: קטע {done} מתוך {total}")

        try:
            audio = render_dialogue(synth, dialogue, chunk_dir, log)
        except DialogueTTSError as exc:
            raise JobError(str(exc)) from exc
        if provider == "elevenlabs":
            costs.add_elevenlabs_characters(sum(len(t["text"]) for t in dialogue.get("dialogue", [])))
        elif provider == "gemini":
            costs.add_gemini_tts_seconds(_audio_seconds(audio.parts))
        return audio.parts, audio.turn_starts

    def _write_chapters(self, project: Project, paths: Any, starts: List[float]) -> None:
        dialogue = self.store.read_json(paths.dialogue_path) or {}
        chapters = []
        for chapter in dialogue.get("metadata", {}).get("chapters", []):
            index = chapter.get("start_index", 0)
            if index < len(starts):
                chapters.append({"title": chapter.get("title", ""), "start": round(starts[index], 2), "turn": index})
        turns = [round(t, 2) for t in starts]
        (self.store.folder(project) / "chapters.json").write_text(
            json.dumps({"chapters": chapters, "turn_starts": turns}, ensure_ascii=False), encoding="utf-8"
        )

    def _run_images(self, project_id: str, force: bool) -> None:
        from ..video.ai_images import GeminiImageMaker, ImageGenError, generate_images, image_prompts
        from .gallery import Gallery

        project = self.store.get(project_id)
        params = self._params.pop(project_id, {})
        settings, _ = self.settings_store.load_settings()
        if settings is None or not getattr(settings, "gemini_api_key", ""):
            raise JobError("ליצירת תמונות צריך מפתח Gemini. הוסף אותו במסך ההגדרות.")
        plan = self.store.read_json(self.store.engine_dir(project) / "03_plan.json") or {}
        count = max(1, min(int(params.get("count") or 4), MAX_AI_IMAGES))
        prompts = image_prompts(plan, project.title, count, str(params.get("style") or "illustration"))
        if self._image_maker_factory:
            maker = self._image_maker_factory(settings)
        else:
            maker = GeminiImageMaker(settings.gemini_api_key, getattr(settings, "gemini_image_model", "") or "")
        gallery = Gallery(self.store.folder(project))
        self._stage(project_id, f"יוצר {count} תמונות", 0.0)

        def progress(done: int, total: int) -> None:
            self.store.update(project_id, progress=done / total, stage=f"יוצר תמונה {done + 1} מתוך {total}")

        try:
            made = generate_images(
                maker, prompts,
                save=lambda data, suffix, prompt: gallery.add(f"ai{suffix}", data, source="ai", prompt=prompt),
                log=lambda message: self.store.append_log(project_id, message),
                progress=progress,
            )
        except ImageGenError as exc:
            raise JobError(str(exc)) from exc
        from ..utils.pricing import gemini_image_cost

        self.store.append_log(project_id, f"נוספו לגלריה {made} תמונות (עלות: ${gemini_image_cost(made):.2f})")

    def _run_video(self, project_id: str, force: bool) -> None:
        from ..video import SlideStyle, VideoError, render_video
        from ..video.slideshow import ACCENTS
        from .gallery import Gallery

        project = self.store.get(project_id)
        paths = self.store.run_paths(project)
        if not paths.final_audio_path.exists():
            raise JobError("קודם מפיקים את הפודקאסט, ואז את הווידאו.")
        timing = self.store.read_json(self.store.folder(project) / "chapters.json") or {}
        dialogue = self.store.read_json(paths.dialogue_path) or {}
        language = (project.options or {}).get("language", "he")
        gallery = Gallery(self.store.folder(project))
        state = gallery.view()
        images = gallery.selected_paths()

        style = SlideStyle(rtl=language != "en")
        if self.brand is not None:
            prefs = self.brand.prefs()
            style = SlideStyle(
                title_font=self.brand.resolve_font(language),
                body_font=self.brand.resolve_font(language),
                logo=self.brand.logo_path(),
                accent=ACCENTS.get(prefs["accent"], ACCENTS["teal"]),
                rtl=language != "en",
            )
        title = dialogue.get("metadata", {}).get("title") or project.title
        self._stage(project_id, f"יוצר וידאו ({len(images)} תמונות, החלפה כל {state['rotate_seconds']:g} שניות)", 0.0)
        render = self._video_renderer or render_video
        try:
            render(
                audio=paths.final_audio_path,
                output=paths.final_video_path,
                images=images,
                chapters=timing.get("chapters", []),
                episode_title=title,
                style=style,
                rotate_seconds=state["rotate_seconds"],
                work_dir=self.store.folder(project) / "slides",
                log=lambda message: self.store.append_log(project_id, message),
                progress=lambda fraction: self.store.update(project_id, progress=fraction),
            )
        except VideoError as exc:
            raise JobError(str(exc)) from exc
        if not paths.final_video_path.exists():
            raise JobError("יצירת הווידאו הסתיימה בלי קובץ. פרטים ביומן העבודה.")
        self.store.append_log(project_id, "הווידאו מוכן")
        self.store.update(project_id, status="video_ready")
        self.deliver(project_id)  # the video joins the podcast's folder (and Drive)
