"""Item-writing rules an exam question must pass, checked in code (no model, no cost).

The rule ids follow docs/research/exam-mode-oss.en.md §4 (R1-R21). Rules that need judgement (a second
defensible answer, equivalent options, the Bloom level) are the model check's job (exam.py, stage E5);
everything here is mechanical and runs on every item. The Hebrew word lists are proposals from that
research and should be reviewed by a native Hebrew-speaking educator.

Severity: "block" = rewrite or drop the item; "fix" = rewrite (or fixed in code); "warn" = shown only.
"""

from __future__ import annotations

import random
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence

KEYS = "ABCDE"
HEBREW_PREFIXES = "והבכלמש"
_WORD_RE = re.compile(r"[\w֐-׿'\"״׳-]+")

# R4: "all / none of the above"
ALL_NONE = [r"all of the above", r"none of the above", r"both a and b", r"neither",
            r"כל התשובות", r"כל האמור לעיל", r"כל הנ\"?ל", r"אף (?:אחת|תשובה)", r"אף אחד מהנ\"?ל", r"אף אחת מהתשובות",
            r"כל האפשרויות"]
# R5: options that combine other options ("A and B", "1 ו-3")
COMBINED = r"(?:^|\s)(?:[א-ד]|[A-E]|[1-5])\s*(?:,|ו-?|וגם|and|or|או)\s*(?:[א-ד]|[A-E]|[1-5])(?:\s|$|\.)"
# R8: absolute determiners in options (a cue that the option is false)
ABSOLUTE = {"he": ["תמיד", "אף פעם", "לעולם", "בהכרח", "אך ורק", "בשום מקרה", "לחלוטין", "ודאי"],
            "en": ["always", "never", "only", "all", "none", "completely", "certainly", "must"]}
# R9: vague frequency words
VAGUE = {"he": ["לפעמים", "לעיתים", "לעתים", "לרוב", "בדרך כלל", "לעיתים רחוקות", "כמעט", "במידה מסוימת", "חלקית"],
         "en": ["sometimes", "often", "usually", "rarely", "frequently", "occasionally", "generally"]}
# R10: negative stems
NEGATIVE = {"he": [r"\bלא\b", r"\bאינו\b", r"\bאינה\b", r"\bאינם\b", r"\bאינן\b", r"\bלמעט\b", r"\bחוץ מ", r"\bשגוי", r"אינה נכונה"],
            "en": [r"\bnot\b", r"\bexcept\b", r"\bfalse\b", r"\bincorrect\b", r"\bleast\b"]}
# R18: true/false disguised as a multiple-choice question
TRUE_FALSE = {"נכון", "לא נכון", "כן", "לא", "true", "false", "yes", "no"}


def flag(rule: str, severity: str, message: str) -> Dict[str, str]:
    return {"rule": rule, "severity": severity, "message": message}


def forms(word: str) -> set:
    """The ways a Hebrew word may be read: as written, without one attached prefix, and without "ו" plus
    one more prefix (והמודל → המודל, מודל). Morphology is ambiguous (בשונות is ב+שונות, not ב+ש+ונות), so
    matching compares all readings instead of guessing one."""
    found = {word}
    if len(word) > 3 and word[0] in HEBREW_PREFIXES:
        found.add(word[1:])
        if word[0] == "ו" and len(word) > 4 and word[1] in HEBREW_PREFIXES:
            found.add(word[2:])
    return found


def strip_prefixes(word: str) -> str:
    """The shortest reading of a Hebrew word (for display and tests); matching uses forms()."""
    return min(forms(word), key=len)


def words(text: str, language: str) -> List[str]:
    return [w for w in (w.strip("'\"״׳-").lower() for w in _WORD_RE.findall(text or "")) if w]


def _all_forms(text: str, language: str) -> set:
    tokens = words(text, language)
    return set().union(*(forms(t) for t in tokens)) if language == "he" and tokens else set(tokens)


def _has_any(text: str, phrases: Iterable[str], language: str) -> List[str]:
    tokens = _all_forms(text, language)
    joined = " ".join(words(text, language))
    hits = []
    for phrase in phrases:
        parts = words(phrase, language)
        if len(parts) == 1 and parts[0] in tokens:
            hits.append(phrase)
        elif len(parts) > 1 and " ".join(parts) in joined:
            hits.append(phrase)
    return hits


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, " ".join(words(a, "he")), " ".join(words(b, "he"))).ratio()


def check_item(item: Dict[str, Any], language: str, known_sections: Optional[set] = None,
               source_text: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    """Every rule this item breaks. An empty list means the item passed layer 1."""
    flags: List[Dict[str, str]] = []
    stem = str(item.get("stem") or "").strip()
    if len(stem) < 8:
        flags.append(flag("R0", "block", "השאלה ריקה או קצרה מדי"))
    if re.search(r"_{2,}|\.\.\.\s*$", stem):
        flags.append(flag("R18", "block", "שאלת השלמה במקום שאלה"))
    if any(re.search(p, stem) for p in NEGATIVE.get(language, [])) and "**" not in stem:
        flags.append(flag("R10", "fix", "שאלה בניסוח שלילי בלי הדגשה"))

    sources = item.get("sources") or []
    if not sources:
        flags.append(flag("R2", "block", "אין הפניה לקטע בשיעור"))
    if known_sections is not None:
        missing = [s.get("section") for s in sources if s.get("section") not in known_sections]
        if missing:
            flags.append(flag("R2", "block", f"הפניה לקטע שלא קיים: {', '.join(map(str, missing))}"))
    if source_text is not None:
        for source in sources:
            quote = " ".join(str(source.get("quote") or "").split())
            text = source_text.get(source.get("section"), "")
            if not quote or len(quote) <= 12 or not text or quote in " ".join(text.split()):
                continue
            # Models copy quotes loosely (punctuation, a changed word): most of the quote's words must be there
            quoted = [w for w in words(quote, language) if len(w) > 1]
            present = _all_forms(text, language)
            found = sum(1 for w in quoted if forms(w) & present) / max(1, len(quoted))
            if found < 0.5:
                flags.append(flag("R2", "fix", "הציטוט לא נמצא בקטע שהשאלה מפנה אליו"))
                break
            if found < 0.8:
                flags.append(flag("R2", "warn", "הציטוט מנוסח מעט אחרת מהשיעור"))
                break

    if item.get("type") == "open":
        if not str(item.get("model_answer") or "").strip():
            flags.append(flag("R0", "block", "חסרה תשובה לדוגמה"))
        rubric = item.get("rubric") or []
        if not 2 <= len(rubric) <= 6:
            flags.append(flag("R0", "fix", "מחוון הציון צריך 2-6 נקודות"))
        return flags

    key = str(item.get("correct_answer") or "").strip()
    distractors = [str(d.get("text") if isinstance(d, dict) else d or "").strip() for d in item.get("distractors") or []]
    options = [key] + distractors
    if not key:
        flags.append(flag("R1", "block", "חסרה תשובה נכונה"))
    if not 2 <= len(distractors) <= 4 or any(not d for d in distractors):
        flags.append(flag("R19", "block", "צריך 2-4 מסיחים לא ריקים"))
    normalized = [" ".join(words(o, language)) for o in options]
    if len(set(normalized)) < len(normalized):
        flags.append(flag("R3", "block", "שתי תשובות זהות"))
    elif any(_similar(a, b) > 0.92 and re.sub(r"\d", "", a) != re.sub(r"\d", "", b)
             for i, a in enumerate(options) for b in options[i + 1:]):
        # options that differ only in a number ("R² = 0.8" / "R² = 0.6") are legitimate, not near-duplicates
        flags.append(flag("R3", "fix", "שתי תשובות כמעט זהות"))
    for option in options:
        if any(re.search(p, option, re.I) for p in ALL_NONE):
            flags.append(flag("R4", "block", "\"כל התשובות\" / \"אף תשובה\" אסורים"))
            break
    if any(re.search(COMBINED, o, re.I) for o in options):
        flags.append(flag("R5", "block", "תשובה שמשלבת תשובות אחרות"))
    if {o.strip(" .").lower() for o in options} <= TRUE_FALSE:
        flags.append(flag("R18", "block", "נכון/לא נכון במסווה של שאלה אמריקאית"))
    if key and distractors:
        longest = max(len(d) for d in distractors)
        if len(key) > 1.33 * longest and len(key) - longest > 12:
            flags.append(flag("R7", "fix", "התשובה הנכונה ארוכה בבירור מהשאר"))
    absolutes = [o for o in distractors if _has_any(o, ABSOLUTE.get(language, []), language)]
    if absolutes and not _has_any(key, ABSOLUTE.get(language, []), language):
        flags.append(flag("R8", "warn", "מילים מוחלטות רק במסיחים (רמז שהם שגויים)"))
    if any(_has_any(o, VAGUE.get(language, []), language) for o in options):
        flags.append(flag("R9", "warn", "מילת תדירות מעורפלת בתשובות"))
    stem_words = _all_forms(stem, language) - set(words("מה איזה מהו מהי the a of is what which", language))
    in_distractors = set().union(*(_all_forms(d, language) for d in distractors)) if distractors else set()
    key_only = (_all_forms(key, language) & stem_words) - in_distractors
    if len([w for w in key_only if len(w) > 3]) >= 2:
        flags.append(flag("R12", "fix", "התשובה הנכונה חוזרת על מילים מהשאלה (רמז)"))
    if language == "he" and key and len(distractors) >= 2 and all(o.split() for o in options):
        # R6 (one visible case of a grammatical cue): only the correct answer differs in definiteness
        definite = [o.split()[0].startswith("ה") and len(o.split()[0]) > 3 for o in options]
        if len(set(definite[1:])) == 1 and definite[0] != definite[1]:
            flags.append(flag("R6", "warn", "רק התשובה הנכונה שונה ביידוע (ה'): רמז אפשרי"))
    return flags


def blocking(flags: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    return [f for f in flags if f["severity"] in ("block", "fix")]


def order_numeric(options: List[str]) -> List[str]:
    """R15: numeric options in ascending order (a lost sequence is a cue)."""
    def value(text: str) -> Optional[float]:
        match = re.fullmatch(r"\s*[-+]?\d+(?:[.,]\d+)?\s*%?\s*", text)
        return float(match.group(0).strip(" %").replace(",", ".")) if match else None

    values = [value(o) for o in options]
    if all(v is not None for v in values):
        return [o for _, o in sorted(zip(values, options))]
    return options


def assign_positions(items: List[Dict[str, Any]], seed: int, num_options: int = 4) -> List[Dict[str, Any]]:
    """R16: the app, not the model, decides where the correct answer goes.

    Options are shuffled with a seeded random order, then the correct answer is moved so every
    letter is the key about equally often across the exam and never more than 3 times in a row.
    """
    rng = random.Random(seed)
    mcqs = [i for i in items if i.get("type") == "mcq"]
    counts: Dict[str, int] = {}
    previous: List[str] = []
    for item in mcqs:
        texts = [item["correct_answer"]] + [d["text"] if isinstance(d, dict) else d for d in item["distractors"]]
        texts = texts[:num_options]
        ordered = order_numeric(texts)
        size = len(ordered)
        if ordered == texts:  # not numeric: shuffle, then put the key in the least used position
            rest = texts[1:]
            rng.shuffle(rest)
            letters = KEYS[:size]
            candidates = sorted(letters, key=lambda k: (counts.get(k, 0), rng.random()))
            target = next((k for k in candidates if previous[-3:] != [k, k, k]), candidates[0])
            position = letters.index(target)
            ordered = rest[:position] + [item["correct_answer"]] + rest[position:]
        key = KEYS[ordered.index(item["correct_answer"])]
        counts[key] = counts.get(key, 0) + 1
        previous.append(key)
        why = {(d["text"] if isinstance(d, dict) else d): (d.get("why_wrong", "") if isinstance(d, dict) else "")
               for d in item["distractors"]}
        item["options"] = [{"key": KEYS[i], "text": text, "correct": text == item["correct_answer"],
                            "feedback": "" if text == item["correct_answer"] else why.get(text, "")}
                           for i, text in enumerate(ordered)]
        item["answer"] = key
    return items


def duplicates(items: Sequence[Dict[str, Any]]) -> List[int]:
    """Indexes of items that repeat an earlier one (R20).

    Similar wording alone is not a repeat ("מהי ההגדרה של רגרסיה?" and "...של מתאם?" are different
    questions): a repeat has a near-identical stem on the same topic, or a similar stem with a similar answer.
    """
    def answer(item: Dict[str, Any]) -> str:
        return str(item.get("correct_answer") or item.get("model_answer") or "")

    repeated = []
    for index, item in enumerate(items):
        for earlier in items[:index]:
            stem = _similar(str(item.get("stem") or ""), str(earlier.get("stem") or ""))
            same_topic = item.get("topic") == earlier.get("topic")
            if (same_topic and stem > 0.92) or (stem > 0.75 and _similar(answer(item), answer(earlier)) > 0.8):
                repeated.append(index)
                break
    return repeated
