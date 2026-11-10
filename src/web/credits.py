"""Credits: what is left to spend, not only what things cost.

ElevenLabs reports its credits through the API (see dialogue_tts.elevenlabs_account). Google does not
expose a prepaid Gemini balance to an API key, so this ledger keeps it: the user enters the amount they
loaded, every job records what it actually spent on Google (Gemini text, Gemini voices, Gemini images,
measured from the tokens and seconds the job used), and the balance left is the amount minus that spend.
Entering the balance again (after topping up, or to match Google's billing page) restarts the count.

  <outputs>/private/credits.json   {"google": {"balance", "currency", "usd_rate", "set_at"}, "spend": [...]}
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

MAX_ENTRIES = 1000
DEFAULT_USD_RATE = 3.7   # shekels per dollar, editable in the settings (Gemini prices are in dollars)


def google_spend_usd(costs: Any) -> float:
    """What a job spent on Google, from its CostTracker: Gemini text tokens and Gemini voice seconds."""
    from ..utils.pricing import gemini_tts_cost, llm_token_cost

    text = sum(llm_token_cost(model, int(prompt), int(completion))
               for model, (prompt, completion) in (getattr(costs, "model_tokens", {}) or {}).items()
               if str(model).lower().startswith("gemini"))
    voices = gemini_tts_cost(float(getattr(costs, "gemini_tts_seconds", 0) or 0))
    return round(text + voices, 6)


class CreditsLedger:
    def __init__(self, private_dir: Path) -> None:
        self.path = Path(private_dir) / "credits.json"
        self._lock = threading.Lock()

    def _read(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def set_google_balance(self, balance: float, usd_rate: Optional[float] = None) -> Dict[str, Any]:
        if not 0 <= float(balance) <= 1_000_000:
            raise ValueError("יתרה לא תקינה")
        with self._lock:
            data = self._read()
            current = data.get("google", {})
            rate = float(usd_rate) if usd_rate else float(current.get("usd_rate") or DEFAULT_USD_RATE)
            if not 0.5 <= rate <= 20:
                raise ValueError("שער לא תקין")
            data["google"] = {"balance": round(float(balance), 2), "currency": "ILS", "usd_rate": rate, "set_at": time.time()}
            self._write(data)
        return self.view()

    def record(self, provider: str, usd: float, project: str = "", what: str = "") -> None:
        """Remember money a job spent (only Google spend is kept: ElevenLabs reports its own credits)."""
        if provider != "google" or not usd or usd <= 0:
            return
        with self._lock:
            data = self._read()
            spend = data.setdefault("spend", [])
            spend.append({"t": time.time(), "provider": provider, "usd": round(float(usd), 6), "project": project, "what": what})
            data["spend"] = spend[-MAX_ENTRIES:]
            self._write(data)

    def view(self) -> Dict[str, Any]:
        data = self._read()
        google = data.get("google") or {}
        rate = float(google.get("usd_rate") or DEFAULT_USD_RATE)
        since = float(google.get("set_at") or 0)
        spent_usd = sum(e["usd"] for e in data.get("spend", []) if e.get("provider") == "google" and e["t"] >= since)
        result: Dict[str, Any] = {"usd_rate": rate, "spent_usd": round(spent_usd, 4), "spent": round(spent_usd * rate, 2),
                                  "balance": None, "left": None, "set_at": since or None}
        if "balance" in google:
            result.update(balance=google["balance"], left=round(google["balance"] - spent_usd * rate, 2))
        return result
