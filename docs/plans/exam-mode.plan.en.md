# Exam Mode: Implementation Plan

*M.B.S Studio · plan date 2026-11-10 · status: proposal, not yet built*

## 1. Goal

Turn one or more finished lectures into exam preparation: a combined study summary and a set of
practice questions (multiple-choice and/or open-ended) at a chosen difficulty, in a chosen language,
that the student can take inside the app, review, and export.

Success means: every question is answerable from the lectures (and cites where), each multiple-choice
question has exactly one defensible answer with plausible wrong options, difficulty matches what was
asked for, the Hebrew reads naturally, and a 10-question set for one lecture costs cents and takes
about a minute when the model is not overloaded.

## 2. What the app already provides (the foundation)

For every project that has passed the analysis stage, the engine has stored:

| File | What it holds | How exam mode uses it |
|---|---|---|
| `engine/01_clean.json` | the cleaned transcript as sections: `id`, `title`, `text`, `start`, `end` (seconds) | the ground truth each question is generated from and checked against; timestamps for "hear it at 34:12" |
| `engine/02_profile.json` | title, summary, learning objectives, concepts (definition, importance 1-3, `section_ids`, `depends_on`), lecturer examples, key facts, misconceptions, open questions, arguments | the blueprint: which concepts to test, how much, and the raw material for wrong answers (misconceptions, neighbouring concepts) |
| `summary.html` and `src/web/summary.py` | a designed lesson summary per project | reused and extended for a multi-lecture study summary |
| `src/engine/llm.py` | JSON chat clients (Gemini, Azure, local), retries with log lines | all generation and checking calls |
| `src/utils/pricing.py`, `src/web/credits.py` | price tables, episode estimate, Google credit ledger, ElevenLabs credits | the exam cost estimate and real-spend recording |

Lectures that were only transcribed (no podcast project yet) need stages 1-2 first. Exam mode offers
"Analyze for exam" that runs only the cleanup and analysis (cached, reused later by a podcast).

## 3. User flow

1. **Choose material.** A new top-level screen, "Exam mode", lists every project and transcription.
   The student ticks several (e.g. lessons 1-6 of a course). Items without an analysis show
   "needs analysis (≈N min, ≈₪X)".
2. **Choose the exam.**
   - Question types: multiple-choice, open-ended, or a mix (e.g. 70/30).
   - Count: 5-60 questions.
   - Difficulty: easy / medium / hard / mixed (see §5).
   - Language of the questions and of the summary, independently: Hebrew or English
     (a Hebrew lecture can produce an English exam and vice versa).
   - Personalization ("התאמה"): focus topics (pick concepts from the merged list), topics to skip,
     exam style (university final, quick self-check, oral-exam prep), number of options (4 or 5),
     whether to include "explain your answer" follow-ups, and a free-text instruction
     (e.g. "the lecturer emphasised definitions; ask about the differences between X and Y").
   - A live cost/credit estimate (§9), as on the podcast screen.
3. **Generate.** Progress with stages, time left and a work log (existing ETA/log components).
4. **Study summary** first: one combined, designed page (HTML/PDF/Word/Markdown), organised by topic
   across lectures, with "where in which lecture" references.
5. **Practice.** Take the exam in the app: one question at a time or the whole sheet; instant or
   end-of-exam feedback; every answer shows an explanation and a link to the lecture moment.
6. **Results.** Score per topic and per difficulty; "practice my weak topics" builds a follow-up set
   from wrong answers (adaptive practice, §8).
7. **Export.** Printable exam and answer key (PDF via print, Word), Markdown, Anki deck (CSV; `.apkg`
   via `genanki` later), Moodle XML, GIFT and a QTI 1.2 package for course platforms, plain JSON. All are
   written from our own JSON with plain templates, so no new dependency is needed. GIFT's special
   characters (`~ = # { } :`) are escaped; every format keeps right-to-left text intact (`dir="rtl"`,
   `<bdi>` around English terms). Google Forms export is left for later (it needs Google sign-in).

## 4. Generation pipeline (new engine module `src/engine/exam.py`)

Stages are cached like the script engine's (`_stage` with a content key), so a failed or cancelled
run resumes, and changing only the count or the export never regenerates everything.

| Stage | Input | Output | Model calls |
|---|---|---|---|
| E1 Collect | selected projects' `01_clean` + `02_profile` | merged material with globally unique section ids (`L3:S14`) and lecture labels | none |
| E2 Merge topics | all profiles | one topic map: concepts deduplicated across lectures (same idea, different wording), importance, the lectures/sections each appears in, misconceptions attached | 1 (small) |
| E3 Blueprint | topic map, options | a test blueprint: a topic × cognitive-level matrix that allocates the N questions by importance and the requested difficulty and type mix (deterministic code, no model) | none |
| E4 Write | one blueprint cell at a time, with only that topic's source sections | questions in a strict JSON schema (§6). Wrong options follow the error-modelling method (solve first, list specific student errors, derive the wrong answer each error produces, keep the most plausible), seeded from the profile's `misconceptions`, `arguments[].objections` and `depends_on` neighbours | 1 per 3-5 questions |
| E5 Check | each question + its cited sections | layer 1: deterministic rules (§7); layer 2, only for items a rule flags or every hard item: a **blind solver** (sees stem, shuffled options and sources, not the key; must pick exactly the keyed option and cite a section), an **option-equivalence** check (no distractor means the same as the key or another distractor), and a Bloom-level classifier | 1-2 per batch |
| E6 Repair | failed questions | one rewrite attempt per failed question, then re-check; still failing → dropped and replaced from the same cell | as needed |
| E7 Assemble | passing questions | final order (spread topics, easy→hard or mixed), balanced answer positions, numbering, answer key | none |
| S1 Study summary | topic map + sections | a combined summary in the requested language: per topic, definitions, key facts, examples, misconceptions, cross-lecture links | 1 per 3-4 topics |

The writing and checking prompts live with the others in `src/engine/prompts.py` and follow the same
house rule: instructions in English, output in the requested language.

## 5. Difficulty: a definition the model can follow and the checker can verify

Difficulty is defined by the cognitive level (revised Bloom's taxonomy) plus the item's features,
not by a vague adjective:

| Level | Bloom levels | Multiple-choice shape | Open question shape |
|---|---|---|---|
| Easy | remember, understand | recall a definition or a fact stated in the lecture; distractors are clearly different ideas | "Define…", "Explain in your words…" |
| Medium | apply, analyze | a short scenario or example not given verbatim; distractors are related concepts or common misconceptions | "Compare X and Y…", "What would happen if…" |
| Hard | analyze, evaluate | multi-step reasoning across two concepts or two lectures; distractors are near-misses that are each partly true | "Evaluate the claim…", "Design / justify…" |

Difficulty is created by the task (cognitive level, number of concepts integrated, how close the
wrong options are), never by rewording: rarer words or passive voice make a question harder to read,
not harder to answer. The checker labels each item's Bloom level independently; mismatches are
rewritten. After the student answers, classical item statistics (share answered correctly per item)
recalibrate the labels, so "hard" comes to mean hard for this student. A "mixed" exam uses a 30/50/20
split by default.

## 6. Question schema (JSON, versioned)

```json
{
  "id": "q07",
  "type": "mcq",
  "difficulty": "medium",
  "bloom": "apply",
  "topic": "linear regression",
  "language": "he",
  "stem": "...",
  "correct_answer": "...",
  "distractors": [
    {"text": "...", "error": "misconception: correlation implies causation", "why_wrong": "..."},
    {"text": "...", "error": "related concept: MSE", "why_wrong": "..."},
    {"text": "...", "error": "partly true: ignores the intercept", "why_wrong": "..."}
  ],
  "explanation": "why the correct answer is right",
  "sources": [{"lecture": "L3", "section": "S14", "start": 2052.4, "quote": "short supporting quote"}],
  "checks": {"answerable": true, "single_answer": true, "flaws": []}
}
```

The model returns the correct answer and the wrong options as separate fields and never chooses a
letter: language models place correct answers in a skewed way, so the app shuffles the options with a
seeded random order and balances answer positions across the whole exam (§7). Letters exist only in
the assembled exam. Open questions replace `correct_answer`/`distractors` with `model_answer` and a
`rubric` (3-5 scoring points with weights), so self-grading or model-grading is consistent.

## 7. Quality: automatic rules (enforced in code where possible, by the checker otherwise)

Enforced deterministically in code:
- exactly one `answer`, which is one of the option keys; 4 (or 5) unique options; no empty text;
- no "all of the above" / "none of the above" (and their Hebrew forms "כל התשובות נכונות", "אף תשובה");
- option length balance: the correct option is not the longest by a wide margin (a classic cue);
- answer positions balanced across the exam (seeded shuffle, then rebalance so no key exceeds ~35%);
- every `sources[].section` exists in the selected material; quotes are found in that section;
- no duplicate questions (normalized stem similarity) and no two questions that give away each other;
- language check: Hebrew output is mostly Hebrew letters (technical terms allowed), English output English;
- Hebrew-aware word rules: words are matched after stripping attached prefixes (ו/ה/ב/כ/ל/מ/ש), with
  Hebrew lists for "all/none of the above", absolute words ("תמיד", "אף פעם", "בלבד"), vague words and
  negative stems ("איזה מהבאים אינו"); options must agree in gender, number and definiteness, since a
  mismatch gives the answer away in Hebrew. These word lists are proposals to be reviewed by a native
  Hebrew-speaking educator.

Checked by the model (E5), with a strict JSON verdict:
- the stem is answerable from the cited sources alone, and the keyed answer is correct;
- no other option is also defensible; distractors are wrong for a stated reason yet plausible;
- no negative stems unless emphasised ("which is NOT…" in bold), no double negatives;
- no grammatical or wording cues that point to the answer (in Hebrew: gender/number agreement
  between stem and options);
- the Bloom level matches the requested band.

Every rejection is logged with its reason, and the result screen shows a quality line
("38 of 40 passed the checks; 2 were rewritten").

## 8. Personalization and practice

- **Focus and exclusion** come from the merged topic map, so they are concrete choices, not free text.
- **Weak-topic practice:** results are stored per topic; "practice again" builds a new set weighted
  toward topics answered wrong, at the same or the next-lower difficulty, never repeating an item
  until the pool for that topic is used up.
- **Spaced repetition (phase 2):** Anki export first; an in-app review queue with the FSRS algorithm
  (the `py-fsrs` library, MIT) later.
- **Target difficulty:** follow-up sets aim at roughly 70-85% expected success, the range in which
  practice is effortful but not discouraging.
- **Lecturer emphasis:** an optional instruction field and, when available, the lecturer's own
  "this will be on the exam" moments (detected in the transcript by phrases such as "למבחן",
  "חשוב לזכור") get extra weight in the blueprint.

## 9. Cost, credits and time

- Reuse `estimate_podcast`'s method in a new `estimate_exam(words, questions, types, language)`;
  show it with the Google balance (credit ledger) before generating.
- Rough order of magnitude with Gemini 3.8 Flash at 2026 prices: one lecture, 20 questions plus the
  check and the summary ≈ 0.05-0.15 USD (≈₪0.2-0.6). Six lectures, 40 questions ≈ 0.2-0.5 USD.
  To be calibrated from the first real runs (the ledger records actual spend).
- Record actual Google spend per exam in the ledger, as the podcast jobs do.
- Progress, ETA and retry log lines reuse the components added for podcasts.

## 10. Storage, API and screens

- Storage: `outputs/exams/<id>/` with `exam.json` (settings, selected projects, status, log),
  `engine/E2_topics.json` … `E7_exam.json`, `summary.html`, `attempts/<n>.json` (answers and score).
- API (in a new `src/web/exam_api.py`, registered like the media routes): create, get, list, delete,
  run/retry, estimate, submit attempt, practice-again, export (`?format=pdf|docx|md|anki|moodle|gift|json`).
- Jobs: an `ExamRunner` beside the podcast runner, one job at a time, keep-awake, interrupted-job
  recovery, same error/notice pattern.
- Screens: "Exam mode" in the top navigation; setup screen (material + options + estimate);
  progress; study summary; practice view (RTL, keyboard-friendly, large touch targets for phones);
  results; export menu.

## 11. Tests

- Schema and deterministic rules (§7) on fixed sample items, including Hebrew cue cases.
- Blueprint allocation: counts per topic follow importance and difficulty split; focus/exclude work.
- Answer-position balance over many generated exams with a fake model.
- Grounding: every source section exists; quotes are found; a fabricated source is rejected.
- End-to-end with the fake LLM used by the existing web tests: two lectures → exam → attempt → score
  → practice-again → every export format parses (Moodle XML validates, GIFT and Anki CSV re-read).
- A small real-model evaluation set (5 lectures) scored by the existing English critic, tracked over
  time like the podcast quality runs.

## 12. Phases and effort

| Phase | Scope | Effort (focused work) |
|---|---|---|
| 1 | Engine E1-E7 for one or more lectures, MCQ + open, difficulty, language, deterministic checks, model check and repair; JSON + printable HTML export; tests | 2-3 days |
| 2 | Exam-mode screens: setup with estimate, progress, practice and results, study summary across lectures | 2 days |
| 3 | Exports: Word, Markdown, Anki CSV, Moodle XML, GIFT; weak-topic practice | 1-2 days |
| 4 | "Analyze for exam" for plain transcriptions; lecturer-emphasis weighting; in-app spaced repetition | 1-2 days |

## 13. Risks and how the design handles them

| Risk | Mitigation |
|---|---|
| Wrong keyed answers or two correct options | grounding to cited sections, model check (E5), repair or drop; the explanation must justify every option |
| Questions about things the lecture did not say | generation sees only the cited sections; code rejects sources that do not exist |
| Weak or silly distractors | distractors drawn from the profile's misconceptions and neighbouring concepts, each with a stated source |
| Answer-position and length cues | deterministic shuffle and rebalance; length check |
| Unnatural Hebrew (gender agreement, translated phrasing) | Hebrew style rules from the podcast prompts reused; agreement cues checked |
| Gemini overload (503) | the existing retry and log lines; cached stages resume where they stopped |
| Cost surprises | estimate before running; credit ledger records real spend |

## 14. Lessons from open-source projects

Full report: `docs/research/exam-mode-oss.en.md` (39 sources). Six repositories were cloned and read
(not run): educhain, EduAid, SAQUET, ECTEL23, llm-student-modeling-strategies (ETH Zürich), text2qti.

| Project | License | What we take | What we avoid |
|---|---|---|---|
| educhain | MIT | planning by topic → objective → count; study-guide structure | its bulk template hard-codes "5th grade" and "medium difficulty", and its worked example puts the answer at option A |
| EduAid | GPL-3.0 (ideas only, no code) | the Google Forms flow as a later option | "hard" mode made by rarer synonyms and passive voice; the model choosing the answer letter; Forms export without grading turned on |
| SAQUET / ECTEL23 | no license (re-implemented from the papers) | the two-layer item-writing-flaw check: rules first, an LLM judge only when a rule fires; "list every correct option, pass only if exactly one" | — |
| llm-student-modeling-strategies (ETH Zürich) | MIT | wrong options by modelling student errors: solve first, list errors, derive each wrong answer, keep the most plausible | similar-word distractors, which can be synonyms of the key |
| text2qti | BSD-3 | the QTI 1.2 package layout | no right-to-left support, which we add |

What the research changed in this plan:
1. The model returns `correct_answer` and `distractors[]`; the app owns letters and balances positions (§6, §7).
2. Wrong options come from modelled student errors seeded by the lesson profile (§4, E4).
3. Quality is checked in two layers, with a blind solver as the main guard against wrong keys (§4, E5).
4. Difficulty comes from the task and is recalibrated from the student's answers (§5).
5. Hebrew-specific rules: prefix-aware word matching and agreement checks across options (§7).
6. Exports are written from our own JSON with no new dependencies; QTI 1.2 is added (§3).
7. Practice targets 70-85% success and uses FSRS for spaced repetition (§8).

No open-source tooling was found for Hebrew quiz writing or Hebrew item-quality checks, which is a
place where M.B.S Studio can lead. Several paper results in the report come from abstracts only and
are marked as such there.
