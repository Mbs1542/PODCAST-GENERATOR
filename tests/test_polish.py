"""The whole-episode editor: only safe, small edits get through."""

from src.engine.polish import EpisodePolisher, apply_edits, check_edit

SEGMENTS = [
    {"id": "intro", "title": "פתיחה", "turns": [
        {"speaker": "Roee", "text": "שלום וברוכים הבאים! היום מדברים על התא."},
        {"speaker": "Noa", "text": "רגע, רגע. התא? זה נשמע בסיסי."},
        {"speaker": "Roee", "text": "בדיוק."},
    ]},
    {"id": "S1", "title": "המיטוכונדריה", "turns": [
        {"speaker": "Noa", "text": "שלום שוב! אז היום נדבר על המיטוכונדריה."},
        {"speaker": "Roee", "text": "המיטוכונדריה מייצרת אנרגיה לתא בצורה של ATP, וזה תהליך שקורה כל הזמן בכל תא ותא שלנו, "
                                   "גם עכשיו כשאנחנו מדברים, וזה מה שמאפשר לשריר לזוז ולמוח לחשוב."},
        {"speaker": "Noa", "text": "רגע, רגע."},
        {"speaker": "Roee", "text": "בדיוק."},
    ]},
]


def test_unsafe_edits_are_rejected():
    long_line = SEGMENTS[1]["turns"][1]
    assert not check_edit(long_line, {"action": "delete"})[0]               # explanations are never dropped
    assert not check_edit(long_line, {"action": "replace", "text": "יש 37 טריליון תאים"})[0]  # new number
    assert not check_edit(SEGMENTS[0]["turns"][2], {"action": "replace", "text": " ".join(["מילה"] * 20)})[0]
    assert not check_edit(SEGMENTS[0]["turns"][2], {"action": "insert", "text": "x"})[0]
    assert check_edit(SEGMENTS[0]["turns"][2], {"action": "delete"})[0]


def test_edits_fix_seams_and_repetition_without_losing_content():
    edits = [
        {"line": 4, "action": "replace", "text": "אז בואו נצלול פנימה: המיטוכונדריה.", "why": "seam"},
        {"line": 6, "action": "delete", "why": "repetition"},
        {"line": 5, "action": "delete", "why": "nope"},              # rejected: long explanation
        {"line": 99, "action": "delete"},                            # out of range
        {"line": 7, "action": "replace", "text": "נכון.", "delivery": "warm!!"},
    ]
    logged = []
    polished, applied = apply_edits(SEGMENTS, edits, logged.append)
    assert applied == 3
    s1 = polished[1]["turns"]
    assert s1[0]["text"].startswith("אז בואו נצלול")
    assert len(s1) == 3 and s1[1] == SEGMENTS[1]["turns"][1]       # the explanation is untouched
    assert s1[2] == {"speaker": "Roee", "text": "נכון.", "delivery": "warm"}
    assert any("only short lines" in line for line in logged)
    assert SEGMENTS[1]["turns"][0]["text"].startswith("שלום שוב")  # the input is not modified


def test_speakable_text_and_episode_memory():
    from src.engine.verifier import unspeakable_parts
    from src.engine.writer import overused_phrases

    assert unspeakable_parts("בשנת 1769 ג'יימס וואט שיפר את מנוע הקיטור") == []   # a year is fine
    assert unspeakable_parts("Web3 ו-H2O") == []                                  # digits inside names are fine
    assert unspeakable_parts("הלחץ הוא 120 על 80, כלומר 70% מהמקרים") == ["120", "80", "70", "%"]
    assert unspeakable_parts("x = y + 2")[:1] == ["2"] and "=" in unspeakable_parts("x = y + 2")

    turns = [{"speaker": "Noa", "text": t} for t in
             ("רגע, רגע.", "בדיוק.", "רגע, רגע.", "אז בעצם המיטוכונדריה מייצרת אנרגיה", "אז בעצם זה אומר משהו",
              "אז בעצם למה זה קורה")]
    assert overused_phrases(turns) == ["רגע רגע", "אז בעצם"]


def test_pilot_findings_are_checked_in_code():
    from src.engine.planner import assign_word_budget
    from src.engine.verifier import ScriptVerifier, _token_set, concept_mentioned, normalize_text

    # A concept named with its expansion in brackets counts as covered when either form is said
    text = normalize_text("ואז יש PEA, מצב שבו יש קצב על המוניטור ואין דופק")
    assert concept_mentioned("PEA (Pulseless Electrical Activity)", text, _token_set(text))
    assert not concept_mentioned("Preload (עומס קדם)", text, _token_set(text))

    # A stray Arabic letter (seen in the pilot: "متועתק") must be caught
    report = ScriptVerifier(None).check({"id": "P2", "target_words": 0, "concepts": []},
                                        [{"speaker": "Roee", "text": "המידע متועתק למולקולה"},
                                         {"speaker": "Noa", "text": "אה, הבנתי."}], "", [])
    assert any("another alphabet" in issue for issue in report.issues)

    # Body segments alternate who leads; debates do not get a lead
    plan = {"format": "exam_prep", "target_minutes": 6, "segments": [
        {"kind": "intro"}, {"kind": "body"}, {"kind": "body"}, {"kind": "body"}, {"kind": "closing"}]}
    leads = [s.get("lead") for s in assign_word_budget(plan, "he")["segments"]]
    assert leads == [None, "Roee", "Noa", "Roee", None]
    plan["format"] = "debate"
    assert all("lead" not in s for s in assign_word_budget(plan, "he")["segments"])


def test_a_failed_polish_keeps_the_script():
    class Broken:
        model, name = "x", "x"

        def chat_json(self, *args, **kwargs):
            raise RuntimeError("network")

    result = EpisodePolisher(Broken(), "he").run(SEGMENTS, {"title": "t"})
    assert result["segments"] == SEGMENTS and result["edits"] == 0
