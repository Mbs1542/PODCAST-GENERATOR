# Changelog

Every release passes the release gate first (`python -m scripts.gate`); the version lives in `src/version.py`.

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
