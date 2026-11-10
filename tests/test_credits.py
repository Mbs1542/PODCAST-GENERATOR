"""The credits ledger: the Google balance the user loaded, minus what jobs really spent."""

from __future__ import annotations

import time

import pytest

from src.utils.costs import CostTracker
from src.utils.pricing import llm_token_cost
from src.web.credits import CreditsLedger, google_spend_usd


def test_balance_left_is_what_was_loaded_minus_real_spend(tmp_path):
    ledger = CreditsLedger(tmp_path)
    assert ledger.view()["left"] is None  # nothing entered yet
    ledger.set_google_balance(60, usd_rate=3.7)
    ledger.record("google", 1.0, "p1", "כתיבת התסריט")
    ledger.record("google", 0.5, "p1", "קולות")
    ledger.record("elevenlabs", 9.0, "p1", "קולות")  # ElevenLabs keeps its own credits: not in this ledger
    view = ledger.view()
    assert view["spent_usd"] == 1.5 and view["spent"] == 5.55 and view["left"] == 54.45

    time.sleep(0.01)
    ledger.set_google_balance(100)  # topped up: the count restarts, the rate is kept
    assert ledger.view() == {**ledger.view(), "balance": 100, "left": 100, "spent": 0, "usd_rate": 3.7}
    with pytest.raises(ValueError):
        ledger.set_google_balance(-5)


def test_real_spend_counts_only_google_from_a_run():
    costs = CostTracker()
    costs.add_openai_usage(200_000, 40_000, model="gemini-3.8-flash")
    costs.add_openai_usage(10_000, 2_000, model="gpt-4o")  # Azure: billed elsewhere
    costs.add_gemini_tts_seconds(60)
    expected = llm_token_cost("gemini-3.8-flash", 200_000, 40_000) + 60 * 0.000225 * (2 if time.localtime().tm_year >= 2027 else 1)
    assert google_spend_usd(costs) == pytest.approx(expected, rel=1e-6)
    assert google_spend_usd(CostTracker()) == 0
