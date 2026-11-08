"""Twenty test lessons that span what the pipeline must handle well.

Each scenario names what it tests, so a low score points at a specific weakness.
The source is a real transcript at data/eval/lessons/<id>.txt when one exists;
otherwise a realistic raw transcript is generated once and kept, so every run is
judged on exactly the same material.

"language" is the lecture's language. The podcast is always Hebrew (the product's
default) unless a scenario sets "output_language": "en".
"""

from __future__ import annotations

from typing import Any, Dict, List

SCENARIOS: List[Dict[str, Any]] = [
    {"id": "01-bio-intro", "title": "מבנה התא: אברונים ותפקידיהם", "language": "he", "lecture_minutes": 20,
     "level": "intro", "lesson_type": "lecture", "format": "auto", "target_minutes": 0, "focus": "",
     "tests": "Baseline intro science lecture; should pick beginner_explainer and use everyday analogies."},
    {"id": "02-cs-dijkstra", "title": "אלגוריתם דייקסטרה למציאת מסלול קצר ביותר", "language": "he", "lecture_minutes": 30,
     "level": "intermediate", "lesson_type": "lecture", "format": "deep_dive", "target_minutes": 0, "focus": "",
     "tests": "Explaining a step-by-step algorithm by ear, without a whiteboard."},
    {"id": "03-math-eigen", "title": "ערכים עצמיים ווקטורים עצמיים", "language": "he", "lecture_minutes": 35,
     "level": "advanced", "lesson_type": "lecture", "format": "deep_dive", "target_minutes": 0, "focus": "",
     "tests": "Formulas and derivations in audio: must convey intuition without reading equations aloud."},
    {"id": "04-physics-exam", "title": "חוקי ניוטון: הכנה לבגרות", "language": "he", "lecture_minutes": 25,
     "level": "intro", "lesson_type": "tutorial", "format": "exam_prep", "target_minutes": 8, "focus": "",
     "tests": "Exam prep: precise definitions, distinctions between laws, self-check questions."},
    {"id": "05-history-industrial", "title": "המהפכה התעשייתית בבריטניה", "language": "he", "lecture_minutes": 30,
     "level": "intermediate", "lesson_type": "story", "format": "story", "target_minutes": 0, "focus": "",
     "tests": "Narrative format: chronology, causes and consequences, people; dates must be faithful."},
    {"id": "06-ethics-debate", "title": "תועלתנות מול דאונטולוגיה", "language": "he", "lecture_minutes": 30,
     "level": "intermediate", "lesson_type": "discussion", "format": "debate", "target_minutes": 0, "focus": "",
     "tests": "Debate format: two grounded positions, fair to both, ending with when each view holds."},
    {"id": "07-econ-inflation", "title": "אינפלציה וריבית: איך הבנק המרכזי משפיע", "language": "he", "lecture_minutes": 25,
     "level": "intermediate", "lesson_type": "lecture", "format": "deep_dive", "target_minutes": 0,
     "focus": "ההשלכות המעשיות על משקי בית: משכנתא, חיסכון ומחירים",
     "tests": "Focus adherence: the episode should be organized around the listener's practical question."},
    {"id": "08-med-cardio", "title": "יסודות הקרדיולוגיה לסטודנטים לסיעוד", "language": "he", "lecture_minutes": 30,
     "level": "intermediate", "lesson_type": "lecture", "format": "exam_prep", "target_minutes": 0, "focus": "",
     "tests": "Accuracy-critical medical content: zero tolerance for invented numbers or clinical claims."},
    {"id": "09-law-contracts", "title": "דיני חוזים: הצעה וקיבול", "language": "he", "lecture_minutes": 25,
     "level": "intermediate", "lesson_type": "lecture", "format": "auto", "target_minutes": 0, "focus": "",
     "tests": "Legal precision: distinctions and conditions must stay exact; examples from the lecture."},
    {"id": "10-cloud-workshop", "title": "סדנה: בניית רשת וירטואלית ב-Azure", "language": "he", "lecture_minutes": 40,
     "level": "intermediate", "lesson_type": "workshop", "format": "deep_dive", "target_minutes": 0, "focus": "",
     "tests": "Hands-on workshop with demos: turn 'as you see on screen' into audio-friendly explanation."},
    {"id": "11-lit-poem", "title": "ניתוח השיר 'כנרת' של רחל המשוררת", "language": "he", "lecture_minutes": 20,
     "level": "intermediate", "lesson_type": "lecture", "format": "auto", "target_minutes": 0, "focus": "",
     "tests": "Humanities interpretation: themes and imagery, quoting carefully, no invented biography."},
    {"id": "12-psych-biases", "title": "הטיות קוגניטיביות בקבלת החלטות", "language": "he", "lecture_minutes": 25,
     "level": "intro", "lesson_type": "lecture", "format": "beginner_explainer", "target_minutes": 0, "focus": "",
     "tests": "Many parallel concepts: must not become a list; each bias needs a vivid everyday example."},
    {"id": "13-seminar-messy", "title": "סמינר: האם בינה מלאכותית תחליף מורים?", "language": "he", "lecture_minutes": 30,
     "level": "intermediate", "lesson_type": "discussion", "format": "critique", "target_minutes": 0, "focus": "",
     "transcript_style": "a messy multi-speaker class discussion: lecturer plus several students interrupting, "
                         "tangents, unresolved disagreements, crosstalk marked as 'סטודנט:' and 'סטודנטית:'",
     "tests": "Messy multi-speaker source in the critique direction: extract the real arguments, attribute views, "
              "weigh each claim fairly using only what was said, ignore noise."},
    {"id": "14-micro-lesson", "title": "מיקרו-שיעור: מה זה ריבית דריבית", "language": "he", "lecture_minutes": 4,
     "level": "intro", "lesson_type": "lecture", "format": "auto", "target_minutes": 0, "focus": "",
     "tests": "Very short source: the episode must stay short and must not pad with invented content."},
    {"id": "15-long-lecture", "title": "מבוא לרשתות תקשורת: מודל השכבות", "language": "he", "lecture_minutes": 60,
     "level": "intermediate", "lesson_type": "lecture", "format": "brief", "target_minutes": 0, "focus": "",
     "tests": "A long lecture cut to a ~5-minute brief: only the essentials, in proportion, still a real conversation, "
              "nothing invented to bridge the cuts."},
    {"id": "16-excel-tutorial", "title": "טבלאות ציר באקסל: הדרכה מעשית", "language": "he", "lecture_minutes": 20,
     "level": "intro", "lesson_type": "tutorial", "format": "beginner_explainer", "target_minutes": 0, "focus": "",
     "transcript_style": "a screen-share tutorial full of 'click here', 'as you can see', 'drag this field to the rows area'",
     "tests": "Visual, click-by-click tutorial: convey the mental model, not the clicks."},
    {"id": "17-en-ml-overfitting", "title": "Overfitting and regularization in machine learning", "language": "en",
     "lecture_minutes": 25, "level": "intermediate", "lesson_type": "lecture", "format": "deep_dive", "target_minutes": 0,
     "focus": "", "tests": "English lecture, Hebrew podcast (the default): must sound like native Hebrew, never "
                           "a translation, keeping the English terms Hebrew speakers actually use."},
    {"id": "18-react-codeswitch", "title": "React Hooks: useState ו-useEffect", "language": "he", "lecture_minutes": 30,
     "level": "intermediate", "lesson_type": "lecture", "format": "deep_dive", "target_minutes": 0, "focus": "",
     "transcript_style": "heavy Hebrew-English code-switching, English technical terms mid-sentence, code read aloud",
     "tests": "Code-switching and code: English terms must stay speakable; no code read character by character."},
    {"id": "19-bio-focus-exam", "title": "פוטוסינתזה ונשימה תאית", "language": "he", "lecture_minutes": 30,
     "level": "intermediate", "lesson_type": "lecture", "format": "exam_prep", "target_minutes": 6,
     "focus": "יש לי מבחן מחר: רק ההבדלים בין פוטוסינתזה לנשימה תאית, עם שאלות לבדיקה עצמית",
     "tests": "Narrow focus plus short length: must cut aggressively to what was asked."},
    {"id": "20-astro-general", "title": "חורים שחורים לקהל הרחב", "language": "he", "lecture_minutes": 20,
     "level": "intro", "lesson_type": "lecture", "format": "beginner_explainer", "target_minutes": 10, "focus": "",
     "tests": "General audience, wonder and curiosity: engagement without sacrificing accuracy."},
]

# The default run: ten scenarios that still cover every direction (auto, deep dive, story, debate,
# exam prep, brief, critique), focus, a foreign-language source, code-switching and accuracy-critical content.
# The other ten run on request (--all, or by id).
CORE_IDS = [
    "01-bio-intro", "02-cs-dijkstra", "05-history-industrial", "06-ethics-debate", "07-econ-inflation",
    "08-med-cardio", "13-seminar-messy", "15-long-lecture", "17-en-ml-overfitting", "18-react-codeswitch",
]

LESSON_SYSTEM = (
    "You write realistic raw transcripts of real classes, exactly as speech recognition would capture them, "
    "to test an educational podcast pipeline. Reply with JSON only."
)


def outline_prompt(scenario: Dict[str, Any], parts: int) -> str:
    return f"""Plan a realistic {scenario['lecture_minutes']}-minute {scenario['lesson_type']} titled "{scenario['title']}" for a {scenario['level']}-level audience, in {'English' if scenario['language'] == 'en' else 'Hebrew'}.
The transcript will be written in {parts} consecutive parts. Decide what each part covers: concepts, the lecturer's own examples and analogies, a demo or story, a student question, a common misconception the lecturer warns about.
Return JSON: {{"lecturer": "short description of the lecturer's speaking style", "parts": ["what part 1 covers", "..."]}}"""


def part_prompt(scenario: Dict[str, Any], outline: Dict[str, Any], index: int, words: int, previous_tail: str) -> str:
    language = "English" if scenario["language"] == "en" else "Hebrew"
    style = scenario.get("transcript_style") or "a single lecturer speaking naturally to a class"
    return f"""Write part {index + 1} of {len(outline['parts'])} of the raw transcript of "{scenario['title']}" in {language}.
This part covers: {outline['parts'][index]}
Lecturer: {outline.get('lecturer', '')}
Style: {style}.

Make it sound like real recorded speech, not prose: fillers ({'um, uh, like, you know' if language == 'English' else 'אממ, אה, כאילו, בעצם'}), restarts, repetitions, digressions, references to slides, and one or two speech-recognition errors per part (a misheard technical term). Student remarks start with 'סטודנט:' or 'סטודנטית:' (or 'Student:' in English). No headings, no summaries, no stage directions.
Facts must be correct: this is a real lesson. Length: about {words} words. Continue directly from the previous part{':' if previous_tail else '.'}
{previous_tail}

Return JSON: {{"text": "..."}}"""
