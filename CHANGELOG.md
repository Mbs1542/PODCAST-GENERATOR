# Changelog

Every release passes the release gate first (`python -m scripts.gate`); the version lives in `src/version.py`.

## [3.7.1] - 2026-11-13

- Phone app: themes (auto, dark, black, light) and six accents, a color per podcast, an equalizer while playing; iOS 26 home-screen bottom gap fixed

## [3.7.0] - 2026-11-13

- A phone app at `/m/`, installable to the home screen (PWA), on the same server and API as the desktop page:
  - Podcasts: a player with play and skip from the lock screen (Media Session), speed (0.8×–2×), chapters,
    the line being spoken, and a resume point per episode. Opening another episode does not interrupt the one
    playing until its own play button is pressed.
  - Offline: "Save" keeps the episode's audio, summary and transcript on the phone (Cache API). A service
    worker serves them with Range requests, so a saved episode plays and seeks with no connection, and the
    app opens to what is saved.
  - Reading: the summary drawn for a small screen (`/api/projects/<id>/summary?format=json`, new), the
    podcast script that follows the audio, and the lecture transcript with in-text search and a text size.
  - New: a file from the phone, a recording made in the app, or a link, either as a transcription only (the
    Tools queue) or as a full podcast. Files over 100 MB are refused up front (Cloudflare's per-request cap).
  - Downloads: MP3, summary as PDF or Word, transcript, script.

## [3.6.0] - 2026-11-12

- Transcription on a Mac's GPU from the container: `src/transcribe/gpu_worker.py` runs natively on Apple
  Silicon (mlx-whisper, the same ivrit.ai weights converted to MLX) and the app sends it the audio when
  `MBS_GPU_URL` and `MBS_GPU_TOKEN` are set. Measured on an M1 Ultra, 10 minutes of Hebrew: 12-14 s against
  about 90 s on 6 CPU threads, same word error rate. If the worker does not answer, the CPU path runs as before.
- `MBS_EXTERNAL_AUTH=cloudflare-access`: behind a reverse proxy that verifies a Cloudflare Access login, the
  app needs no password of its own and refuses any request that did not come through that gate.

## [3.5.2] - 2026-11-12

- Release gate: a live check that real links still download the way the app downloads them (saved cookies, retry from the embedding site), without downloading.

## [3.5.1] - 2026-11-12

- New project from a link downloads like the Tools downloader: the site's saved cookies are used, and a Vimeo video that plays only inside a course site (embed-only) is fetched as embedded there. Errors are shown in Hebrew.

## [3.5.0] - 2026-11-10

- Exam mode: exams from one or more analysed lessons, multiple-choice and open questions, difficulty and language
  choices, a blind solver check with automatic repair, and answer positions balanced by the app.
- Practice one question at a time with instant feedback and keyboard shortcuts; scores by topic and level.
- Spaced repetition (FSRS-5): practised questions come back exactly when recall is expected to fall to 90%.
- "Practice again" never repeats an earlier question and aims the difficulty at 70-85% expected success.
- Study summary across lessons; exports to PDF (print), Word, Markdown, Anki, Moodle XML, GIFT and QTI.
- A fixed exam-quality evaluation set with a history (`python -m scripts.exam_eval`).
- The release gate with a version number shown in the app.

## [3.4.6]

- Lecture-to-podcast chain, lesson summaries, output folders, Google Drive upload, macOS support, credits ledger.
