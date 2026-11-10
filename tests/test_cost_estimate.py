"""The cost estimate shown before an episode, and the check that stops the voices before they fail part-way."""

from __future__ import annotations

from datetime import date

from src.audio.dialogue_tts import check_elevenlabs_ready
from src.utils.pricing import estimate_podcast


def test_estimate_grows_with_length_and_names_the_voice_characters():
    short = estimate_podcast(10_000, 12, today=date(2026, 11, 10))
    long = estimate_podcast(10_000, 30, today=date(2026, 11, 10))
    assert 0 < short["script"]["low"] < short["script"]["high"] < long["script"]["high"]
    assert long["voice"]["characters"] > 2 * short["voice"]["characters"] * 0.9
    assert 20_000 < long["voice"]["characters"] < 30_000  # about 6 billed characters per spoken Hebrew word
    assert long["total"]["low"] == round(long["script"]["low"] + long["voice"]["cost"], 2)

    gemini = estimate_podcast(10_000, 30, voice="gemini", today=date(2026, 11, 10))
    assert gemini["voice"]["cost"] < long["voice"]["cost"]  # Gemini voices bill by audio seconds, far cheaper
    assert estimate_podcast(10_000, 30, writer="local")["script"]["high"] == 0
    later = estimate_podcast(10_000, 30, today=date(2027, 1, 2))
    assert later["script"]["low"] == round(long["script"]["low"] * 2, 2)  # Gemini's 2027 price doubling


class _Reply:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class _Eleven:
    def __init__(self, tier, used, limit, categories):
        self.tier, self.used, self.limit, self.categories = tier, used, limit, categories

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/subscription"):
            return _Reply(200, {"tier": self.tier, "character_count": self.used, "character_limit": self.limit,
                                "next_character_count_reset_unix": 1796860800})
        voice_id = url.rsplit("/", 1)[-1]
        return _Reply(200, {"name": f"{voice_id} - Hebrew", "category": self.categories[voice_id]})


def test_voices_check_stops_before_spending_on_a_short_or_unusable_plan():
    voices = {"Roee": "nimrod", "Noa": "danit"}
    free = _Eleven("free", 0, 10_000, {"nimrod": "professional", "danit": "premade"})
    problems = check_elevenlabs_ready("key", voices, 25_000, http=free)
    assert len(problems) == 2
    assert "25,000" in problems[0] and "10,000" in problems[0]
    assert "רועי" in problems[1] and "nimrod" in problems[1]  # the library voice, by host and name

    paid = _Eleven("starter", 2_000, 30_000, {"nimrod": "professional", "danit": "professional"})
    assert check_elevenlabs_ready("key", voices, 25_000, http=paid) == []  # library voices and enough characters
    assert check_elevenlabs_ready("key", voices, None, http=paid) == []  # resuming: nothing to count
