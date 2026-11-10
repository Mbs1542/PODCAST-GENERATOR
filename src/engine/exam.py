"""Exam mode: practice questions from one or more analysed lectures.

Stages (each saved in the work folder with a content key, so a stopped run resumes):
  E1 collect    the lectures' sections and lesson profiles, with ids unique across lectures (L2:S14)
  E2 topics     concepts merged across lectures into topics (a model call only for several lectures)
  E3 blueprint  how many questions per topic, at which difficulty and of which type (code, no model)
  E4 write      questions per topic, grounded in that topic's sections only
  E5 check      layer 1: item-writing rules in code (exam_rules); layer 2: a blind solver that does not see
                the answer, an equivalent-options check and a Bloom-level check
  E6 repair     one rewrite for each failing question, checked again; still failing -> dropped
  E7 assemble   order, answer positions balanced across the exam (the app owns the letters), numbering

See docs/plans/exam-mode.plan.en.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .exam_prompts import (
    BLOOM_BANDS,
    CHECK_SYSTEM,
    REPAIR_SYSTEM,
    TOPICS_SYSTEM,
    WRITE_SYSTEM,
    check_prompt,
    repair_prompt,
    topics_prompt,
    write_prompt,
)
from .exam_rules import KEYS, assign_positions, blocking, check_item, duplicates, words
from .llm import LLMClient, LLMError

EXAM_VERSION = "1.0"
DIFFICULTIES = ("easy", "medium", "hard")
MIXED_SPLIT = {"easy": 0.3, "medium": 0.5, "hard": 0.2}
MAX_SOURCE_CHARS = 9000
# Phrases with which lecturers flag exam material; a topic whose sections contain them weighs more
EMPHASIS_RE = re.compile(r"במבחן|למבחן|חשוב לזכור|חשוב מאוד|שימו לב|זה יופיע|תזכרו|on the exam|important to remember|will be tested",
                         re.IGNORECASE)
ITEMS_PER_CALL = 5

Log = Callable[[str], None]


@dataclass
class ExamOptions:
    count: int = 10
    mcq_share: float = 1.0          # 1.0 = all multiple-choice, 0.0 = all open, 0.7 = 70/30
    difficulty: str = "medium"      # easy | medium | hard | mixed
    language: str = "he"
    num_options: int = 4
    focus: List[str] = field(default_factory=list)    # topic names to cover first (and at least once each)
    exclude: List[str] = field(default_factory=list)  # topic names to leave out
    style: str = "final"            # final | quick | oral
    instructions: str = ""
    seed: int = 0

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ExamOptions":
        options = cls()
        options.count = max(1, min(60, int(raw.get("count") or options.count)))
        options.mcq_share = max(0.0, min(1.0, float(raw.get("mcq_share", options.mcq_share))))
        options.difficulty = raw.get("difficulty") if raw.get("difficulty") in (*DIFFICULTIES, "mixed") else "medium"
        options.language = "en" if raw.get("language") == "en" else "he"
        options.num_options = 5 if int(raw.get("num_options") or 4) == 5 else (3 if int(raw.get("num_options") or 4) == 3 else 4)
        options.focus = [str(t)[:120] for t in (raw.get("focus") or [])][:30]
        options.exclude = [str(t)[:120] for t in (raw.get("exclude") or [])][:30]
        options.style = raw.get("style") if raw.get("style") in ("final", "quick", "oral") else "final"
        options.instructions = " ".join(str(raw.get("instructions") or "").split())[:500]
        options.seed = int(raw.get("seed") or 0)
        return options


@dataclass
class Lecture:
    label: str                      # L1, L2, ... in the order chosen
    title: str
    sections: List[Dict[str, Any]]  # 01_clean.json sections: id, title, text, start, end
    profile: Dict[str, Any]         # 02_profile.json


def _key(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(json.dumps(part, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()[:16]


def _clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"


def _norm(text: str) -> str:
    return " ".join(words(text, "he"))


class ExamBuilder:
    def __init__(self, llm: LLMClient, work_dir: Path, log: Optional[Log] = None, checker: Optional[LLMClient] = None) -> None:
        self.llm = llm
        self.checker = checker or llm
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.log = log or (lambda _m: None)

    # ------------------------------------------------------------------ cache

    def _stage(self, name: str, key: str, compute: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        path = self.work_dir / f"{name}.json"
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("_key") == key:
                    self.log(f"{name}: reusing saved result")
                    cached.pop("_key", None)
                    return cached
            except (OSError, ValueError):
                pass
        result = compute()
        path.write_text(json.dumps({**result, "_key": key}, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    # ------------------------------------------------------------------ run

    def run(self, lectures: Sequence[Lecture], options: ExamOptions) -> Dict[str, Any]:
        if not lectures:
            raise ValueError("No lectures were chosen.")
        material_key = _key([(l.label, l.title, l.sections, l.profile) for l in lectures])
        material = self._stage("E1_material", material_key, lambda: self._collect(lectures))
        topics = self._stage("E2_topics", _key(material_key, options.language),
                             lambda: self._topics(material, options.language))
        blueprint = self._blueprint(topics["topics"], options, material)
        self.log(f"Exam E3: blueprint of {sum(len(c['items']) for c in blueprint)} questions over {len(blueprint)} topics")
        written = self._stage("E4_written", _key(material_key, topics, blueprint, asdict(options)),
                              lambda: self._write_all(blueprint, material, options))
        checked = self._stage("E5_checked", _key(written, EXAM_VERSION),
                              lambda: self._check_all(written["questions"], material, options))
        final = self._stage("E6_repaired", _key(checked, EXAM_VERSION),
                            lambda: self._repair_all(checked["questions"], material, options))
        return self._assemble(final["questions"], topics["topics"], blueprint, material, options, written, checked, final)

    # ------------------------------------------------------------------ E1

    def _collect(self, lectures: Sequence[Lecture]) -> Dict[str, Any]:
        sections: Dict[str, Dict[str, Any]] = {}
        concepts: List[Dict[str, Any]] = []
        for lecture in lectures:
            for section in lecture.sections:
                sid = f"{lecture.label}:{section['id']}"
                sections[sid] = {"id": sid, "lecture": lecture.label, "title": section.get("title", ""),
                                 "text": section.get("text", ""), "start": section.get("start"), "end": section.get("end")}
            profile = lecture.profile or {}
            misconceptions = [m for m in profile.get("misconceptions", []) if m]
            objections = [o for a in profile.get("arguments", []) for o in a.get("objections", []) if o]
            for index, concept in enumerate(profile.get("concepts", [])):
                concepts.append({
                    "id": f"{lecture.label}#{index}", "lecture": lecture.label, "name": concept.get("name", ""),
                    "definition": concept.get("definition", ""), "importance": int(concept.get("importance") or 2),
                    "sections": [f"{lecture.label}:{s}" for s in concept.get("section_ids", [])
                                 if f"{lecture.label}:{s}" in sections],
                    "depends_on": concept.get("depends_on", []),
                    "misconceptions": misconceptions + objections,
                })
        self.log(f"Exam E1: {len(lectures)} lecture(s), {len(sections)} sections, {len(concepts)} concepts")
        return {"lectures": [{"label": l.label, "title": l.title} for l in lectures], "sections": sections, "concepts": concepts}

    # ------------------------------------------------------------------ E2

    def _topics(self, material: Dict[str, Any], language: str) -> Dict[str, Any]:
        concepts = material["concepts"]
        groups: List[Dict[str, Any]] = []
        if len(material["lectures"]) > 1 and concepts:
            try:
                reply = self.llm.chat_json("exam_topics", TOPICS_SYSTEM, topics_prompt(
                    [{"id": c["id"], "name": c["name"], "definition": c["definition"]} for c in concepts], language),
                    max_tokens=4000, temperature=0.1)
                known = {c["id"] for c in concepts}
                used: set = set()
                for topic in reply.get("topics", []):
                    members = [m for m in topic.get("members", []) if m in known and m not in used]
                    if members:
                        used.update(members)
                        groups.append({"name": str(topic.get("name") or ""), "definition": str(topic.get("definition") or ""),
                                       "members": members})
                groups += [{"name": "", "definition": "", "members": [c["id"]]} for c in concepts if c["id"] not in used]
            except LLMError as exc:
                self.log(f"Exam E2: merging by meaning failed ({exc}); merging by name")
                groups = []
        if not groups:  # one lecture, or the merge failed: same name = same topic
            by_name: Dict[str, Dict[str, Any]] = {}
            for concept in concepts:
                by_name.setdefault(_norm(concept["name"]), {"name": "", "definition": "", "members": []})["members"].append(concept["id"])
            groups = list(by_name.values())
        by_id = {c["id"]: c for c in concepts}
        topics = []
        for number, group in enumerate(groups, start=1):
            members = [by_id[m] for m in group["members"]]
            first = members[0]
            sections = list(dict.fromkeys(s for m in members for s in m["sections"]))
            neighbours = list(dict.fromkeys(
                [d for m in members for d in m["depends_on"]]
                + [c["name"] for c in concepts if c["id"] not in group["members"]
                   and set(c["sections"]) & set(sections)]))
            topics.append({
                "id": f"T{number}", "name": group["name"] or first["name"],
                "definition": group["definition"] or first["definition"],
                "importance": max(m["importance"] for m in members),
                "lectures": list(dict.fromkeys(m["lecture"] for m in members)),
                "sections": sections, "neighbours": [n for n in neighbours if n][:10],
                "misconceptions": list(dict.fromkeys(x for m in members for x in m["misconceptions"]))[:10],
                "emphasis": sum(len(EMPHASIS_RE.findall(material["sections"][s]["text"])) for s in sections
                                if s in material["sections"]),
            })
        self.log(f"Exam E2: {len(topics)} topics")
        return {"topics": topics}

    # ------------------------------------------------------------------ E3

    def _blueprint(self, topics: List[Dict[str, Any]], options: ExamOptions, material: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Questions per topic by importance (focus topics first and at least once), difficulty and type."""
        excluded = {_norm(t) for t in options.exclude}
        focus = {_norm(t) for t in options.focus}
        pool = [t for t in topics if _norm(t["name"]) not in excluded] or list(topics)
        if not pool:
            raise ValueError("The chosen lectures have no analysed concepts to ask about.")
        if focus:
            pool.sort(key=lambda t: _norm(t["name"]) not in focus)
        weights = [(3.0 if _norm(t["name"]) in focus else 1.0) * (1.5 if t.get("emphasis") else 1.0) * t["importance"] ** 1.5
                   for t in pool]
        count = options.count
        shares = [count * w / sum(weights) for w in weights]
        allocation = [int(math.floor(s)) for s in shares]
        for index in sorted(range(len(pool)), key=lambda i: shares[i] - allocation[i], reverse=True)[: count - sum(allocation)]:
            allocation[index] += 1
        for index, topic in enumerate(pool):  # every focus topic gets at least one question
            if _norm(topic["name"]) in focus and allocation[index] == 0:
                donor = max(range(len(pool)), key=lambda i: allocation[i])
                if allocation[donor] > 1:
                    allocation[donor] -= 1
                    allocation[index] += 1

        # Difficulty for each question, core topics leaning harder in a mixed exam
        if options.difficulty == "mixed":
            levels = (["easy"] * round(count * MIXED_SPLIT["easy"]) + ["hard"] * round(count * MIXED_SPLIT["hard"]))
            levels += ["medium"] * (count - len(levels))
            levels = levels[:count]
        else:
            levels = [options.difficulty] * count
        order = sorted(range(len(pool)), key=lambda i: -pool[i]["importance"])
        slots = [i for i in order for _ in range(allocation[i])]
        hard_first = sorted(levels, key=lambda l: {"hard": 0, "medium": 1, "easy": 2}[l])
        types = ["mcq" if (n + 0.5) / count <= options.mcq_share else "open" for n in range(count)]
        rng = random.Random(options.seed)
        rng.shuffle(types)
        cells: Dict[int, List[Dict[str, str]]] = {}
        for n, index in enumerate(slots):
            cells.setdefault(index, []).append({"type": types[n], "difficulty": hard_first[n]})
        return [{"topic": pool[i], "items": cells[i]} for i in order if cells.get(i)]

    # ------------------------------------------------------------------ E4

    def _sources(self, topic: Dict[str, Any], material: Dict[str, Any]) -> str:
        sections = material["sections"]
        chosen = [s for s in topic["sections"] if s in sections]
        if not chosen:  # no section ids in the analysis: sections that mention the topic
            name = _norm(topic["name"])
            chosen = [sid for sid, s in sections.items() if name and name in _norm(s["text"])][:3] or list(sections)[:2]
        parts, used = [], 0
        for sid in chosen:
            section = sections[sid]
            text = section["text"][: max(800, MAX_SOURCE_CHARS - used)]
            parts.append(f"[{sid}] {section['title']} ({_clock(section.get('start'))})\n{text}")
            used += len(text)
            if used >= MAX_SOURCE_CHARS:
                break
        return "\n\n".join(parts)

    def _write_all(self, blueprint: List[Dict[str, Any]], material: Dict[str, Any], options: ExamOptions) -> Dict[str, Any]:
        questions: List[Dict[str, Any]] = []
        calls = [(cell, cell["items"][i:i + ITEMS_PER_CALL]) for cell in blueprint
                 for i in range(0, len(cell["items"]), ITEMS_PER_CALL)]
        for number, (cell, items) in enumerate(calls, start=1):
            topic = cell["topic"]
            self.log(f"Exam E4: writing part {number}/{len(calls)} ({topic['name']})")
            sources = self._sources(topic, material)
            try:
                reply = self.llm.chat_json("exam_write", WRITE_SYSTEM, write_prompt(
                    topic, items, sources, options.language, options.num_options, options.style, options.instructions),
                    max_tokens=1200 * len(items) + 800, temperature=0.5)
            except LLMError as exc:
                self.log(f"Exam E4: part {number} failed ({exc}); skipped")
                continue
            for wanted, raw in zip(items, reply.get("questions", [])):
                if not isinstance(raw, dict):
                    continue
                question = {k: raw.get(k) for k in ("stem", "correct_answer", "distractors", "explanation",
                                                    "model_answer", "rubric", "sources", "bloom")}
                question.update(type=wanted["type"], difficulty=wanted["difficulty"], topic=topic["id"],
                                topic_name=topic["name"], language=options.language)
                question["distractors"] = [d if isinstance(d, dict) else {"text": str(d)} for d in question.get("distractors") or []]
                question["sources"] = [s for s in question.get("sources") or [] if isinstance(s, dict)]
                question["id"] = f"w{len(questions) + 1}"
                questions.append(question)
        self.log(f"Exam E4: {len(questions)} questions written")
        return {"questions": questions}

    # ------------------------------------------------------------------ E5

    def _check_all(self, questions: List[Dict[str, Any]], material: Dict[str, Any], options: ExamOptions) -> Dict[str, Any]:
        sections = material["sections"]
        texts = {sid: s["text"] for sid, s in sections.items()}
        by_topic: Dict[str, List[Dict[str, Any]]] = {}
        for question in questions:
            question["flags"] = check_item(question, options.language, set(sections), texts)
            by_topic.setdefault(question["topic"], []).append(question)
        for number, (topic_id, group) in enumerate(by_topic.items(), start=1):
            self.log(f"Exam E5: checking part {number}/{len(by_topic)}")
            self._blind_check(group, material, options)
        failing = sum(1 for q in questions if blocking(q["flags"]))
        self.log(f"Exam E5: {len(questions) - failing} of {len(questions)} passed the checks")
        return {"questions": questions, "passed": len(questions) - failing}

    def _blind_check(self, group: List[Dict[str, Any]], material: Dict[str, Any], options: ExamOptions) -> None:
        """The solver sees each question with its options in a random order, never the answer."""
        rng = random.Random(f"{options.seed}:{group[0]['topic']}")
        shown, keys = [], {}
        for question in group:
            if question["type"] == "mcq":
                texts = [question.get("correct_answer") or ""] + [d.get("text", "") for d in question["distractors"]]
                order = list(range(len(texts)))
                rng.shuffle(order)
                keys[question["id"]] = KEYS[order.index(0)]
                shown.append({"id": question["id"], "type": "mcq", "stem": question.get("stem"),
                              "options": {KEYS[i]: texts[j] for i, j in enumerate(order)}})
            else:
                shown.append({"id": question["id"], "type": "open", "stem": question.get("stem"),
                              "model_answer": question.get("model_answer")})
        topic = {"sections": list(dict.fromkeys(s.get("section") for q in group for s in q.get("sources", [])
                                                if s.get("section") in material["sections"]))}
        sources = self._sources({"name": group[0].get("topic_name", ""), **topic}, material)
        try:
            reply = self.checker.chat_json("exam_check", CHECK_SYSTEM, check_prompt(shown, sources),
                                           max_tokens=400 * len(group) + 400, temperature=0.0)
        except LLMError as exc:
            for question in group:
                question["flags"].append({"rule": "R1", "severity": "warn", "message": f"הבדיקה העיוורת לא רצה ({exc})"})
            return
        reviews = {r.get("id"): r for r in reply.get("reviews", []) if isinstance(r, dict)}
        for question in group:
            review = reviews.get(question["id"])
            if review is None:
                question["flags"].append({"rule": "R1", "severity": "warn", "message": "לא התקבלה בדיקה עיוורת"})
                continue
            question["review"] = review
            if question["type"] == "mcq":
                defensible = [str(k).strip().upper()[:1] for k in review.get("defensible") or []]
                key = keys[question["id"]]
                if defensible != [key]:
                    reason = ("אף תשובה לא נתמכת בשיעור" if not defensible else
                              "יותר מתשובה אחת נכונה" if len(defensible) > 1 else "הפותר העיוור בחר תשובה אחרת")
                    question["flags"].append({"rule": "R1", "severity": "block", "message": reason})
                if any(key in pair for pair in review.get("equivalent") or [] if isinstance(pair, list)):
                    question["flags"].append({"rule": "R3", "severity": "block", "message": "מסיח שקול לתשובה הנכונה"})
                elif review.get("equivalent"):
                    question["flags"].append({"rule": "R3", "severity": "fix", "message": "שני מסיחים שקולים"})
            else:
                if not review.get("answerable", True) or not review.get("answer_supported", True):
                    question["flags"].append({"rule": "R2", "severity": "block", "message": "התשובה לא נתמכת בשיעור"})
            bloom = str(review.get("bloom") or "").lower()
            if bloom and bloom not in BLOOM_BANDS[question["difficulty"]]:
                question["flags"].append({"rule": "D1", "severity": "fix",
                                          "message": f"רמת הקושי בפועל ({bloom}) לא מתאימה ל-{question['difficulty']}"})
            for issue in (review.get("issues") or [])[:3]:
                question["flags"].append({"rule": "R11", "severity": "warn", "message": str(issue)[:200]})

    # ------------------------------------------------------------------ E6

    def _repair_all(self, questions: List[Dict[str, Any]], material: Dict[str, Any], options: ExamOptions) -> Dict[str, Any]:
        failing = [q for q in questions if blocking(q["flags"])]
        if not failing:
            return {"questions": questions, "repaired": 0, "dropped": 0}
        sections = material["sections"]
        texts = {sid: s["text"] for sid, s in sections.items()}
        repaired, dropped = 0, 0
        for number, question in enumerate(failing, start=1):
            self.log(f"Exam E6: rewriting {number}/{len(failing)} ({', '.join(f['message'] for f in blocking(question['flags']))[:120]})")
            sources = self._sources({"name": question.get("topic_name", ""), "sections": [
                s.get("section") for s in question.get("sources", [])]}, material)
            draft = {k: question.get(k) for k in ("type", "difficulty", "stem", "correct_answer", "distractors",
                                                  "explanation", "model_answer", "rubric", "sources")}
            try:
                reply = self.llm.chat_json("exam_repair", REPAIR_SYSTEM, repair_prompt(
                    draft, [f["message"] for f in blocking(question["flags"])], sources, options.language, options.num_options),
                    max_tokens=1600, temperature=0.4)
            except LLMError:
                question["dropped"] = True
                dropped += 1
                continue
            for field_name in ("stem", "correct_answer", "distractors", "explanation", "model_answer", "rubric", "sources", "bloom"):
                if reply.get(field_name):
                    question[field_name] = reply[field_name]
            question["distractors"] = [d if isinstance(d, dict) else {"text": str(d)} for d in question.get("distractors") or []]
            question["flags"] = check_item(question, options.language, set(sections), texts)
            self._blind_check([question], material, options)
            remaining = blocking(question["flags"])
            quote_only = [f for f in remaining if f["rule"] == "R2" and f["severity"] == "fix" and "ציטוט" in f["message"]]
            if quote_only and (question.get("review") or {}).get("section") in material["sections"]:
                # The solver confirmed the answer from a real section: a loosely copied quote is not a reason to drop it
                for f in quote_only:
                    f["severity"] = "warn"
                remaining = blocking(question["flags"])
            if remaining and all(f["rule"] == "D1" for f in remaining):
                # Only the level disagrees: a sound question is kept, labelled with the level it really tests
                measured = str((question.get("review") or {}).get("bloom") or "").lower()
                level = next((d for d in DIFFICULTIES if measured in BLOOM_BANDS[d]), question["difficulty"])
                question["flags"] = [f for f in question["flags"] if f["rule"] != "D1"] + [
                    {"rule": "D1", "severity": "warn", "message": f"סווגה מחדש כ-{level} (בודק הרמה: {measured})"}]
                question["difficulty"] = level
                remaining = []
            if remaining:
                question["dropped"] = True
                dropped += 1
            else:
                question["repaired"] = True
                repaired += 1
        self.log(f"Exam E6: {repaired} rewritten, {dropped} dropped")
        return {"questions": questions, "repaired": repaired, "dropped": dropped}

    # ------------------------------------------------------------------ E7

    def _assemble(self, questions, topics, blueprint, material, options, written, checked, final) -> Dict[str, Any]:
        kept = [q for q in questions if not q.get("dropped")]
        for index in reversed(duplicates(kept)):
            kept.pop(index)
        rank = {"easy": 0, "medium": 1, "hard": 2}
        if options.difficulty == "mixed":  # easy to hard, topics interleaved within a level
            kept.sort(key=lambda q: (rank[q["difficulty"]], q["topic"]))
        assign_positions(kept, options.seed, options.num_options)
        sections = material["sections"]
        for number, question in enumerate(kept, start=1):
            question.pop("review", None)  # the solver's letters refer to its own shuffle, not the exam's
            question["number"] = number
            question["id"] = f"q{number}"
            for source in question.get("sources", []):
                section = sections.get(source.get("section"))
                if section:
                    source.update(start=section.get("start"), time=_clock(section.get("start")), lecture=section["lecture"],
                                  section_title=section.get("title", ""))
        positions = {k: sum(1 for q in kept if q.get("answer") == k) for k in KEYS[:options.num_options]}
        stats = {
            "requested": options.count, "written": len(written["questions"]), "final": len(kept),
            "repaired": final.get("repaired", 0), "dropped": final.get("dropped", 0),
            "passed_first_check": checked.get("passed", 0),
            "by_difficulty": {d: sum(1 for q in kept if q["difficulty"] == d) for d in DIFFICULTIES},
            "by_type": {t: sum(1 for q in kept if q["type"] == t) for t in ("mcq", "open")},
            "answer_positions": positions,
        }
        self.log(f"Exam E7: {len(kept)} questions ready ({stats['repaired']} rewritten, {stats['dropped']} dropped)")
        return {
            "version": EXAM_VERSION, "options": asdict(options), "lectures": material["lectures"],
            "topics": [{**{k: t[k] for k in ("id", "name", "definition", "importance", "lectures")},
                        "emphasis": t.get("emphasis", 0)} for t in topics],
            "questions": kept, "stats": stats,
        }
