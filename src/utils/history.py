from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .logging import get_logger


class HistoryManager:
    """
    Persist run history for dashboard display.
    
    Manages history.json and history_stats.json files for tracking
    project runs, their metadata, chat logs, and accumulated costs.
    
    Features:
        - Record new runs with full metadata
        - Update existing entries (chat_log, transcript_path, etc.)
        - Track aggregated costs across deleted runs
        - Auto-persist on changes
    """

    def __init__(self, base_dir: Path) -> None:
        """
        Initialize history manager.
        
        Args:
            base_dir: Base directory for history files
        """
        self.logger = get_logger(self.__class__.__name__)
        self.history_path = base_dir / "history.json"
        self.stats_path = base_dir / "history_stats.json"
        self.history: List[Dict] = []
        self.aggregated_stats = {"openai_cost_usd": 0.0, "tts_characters": 0}
        
        self.logger.debug("[HistoryManager.__init__] Initializing with base_dir: %s", base_dir)
        self._load()
        self._load_stats()
        self.logger.info("[HistoryManager.__init__] Loaded %d history entries", len(self.history))

    def _load(self) -> None:
        """Load history from JSON file."""
        if self.history_path.exists():
            try:
                self.history = json.loads(self.history_path.read_text(encoding="utf-8"))
                self.logger.debug("[HistoryManager._load] Loaded history from %s", self.history_path)
            except json.JSONDecodeError as e:
                self.logger.warning("[HistoryManager._load] history.json is corrupted: %s; starting fresh", e)
                self.history = []
        else:
            self.logger.debug("[HistoryManager._load] No history file found at %s", self.history_path)

    def _load_stats(self) -> None:
        """Load aggregated statistics from JSON file."""
        if self.stats_path.exists():
            try:
                self.aggregated_stats = json.loads(self.stats_path.read_text(encoding="utf-8"))
                self.logger.debug("[HistoryManager._load_stats] Loaded stats: %s", self.aggregated_stats)
            except Exception as e:
                self.logger.warning("[HistoryManager._load_stats] Failed to load stats: %s", e)
                self.aggregated_stats = {"openai_cost_usd": 0.0, "tts_characters": 0}

    def _save_stats(self) -> None:
        """Save aggregated statistics to JSON file."""
        try:
            self.stats_path.write_text(json.dumps(self.aggregated_stats, indent=2), encoding="utf-8")
            self.logger.debug("[HistoryManager._save_stats] Stats saved successfully")
        except OSError as e:
            self.logger.error("[HistoryManager._save_stats] Failed to save stats: %s", e)

    def _aggregate_run_cost(self, entry: Dict) -> None:
        """Aggregate costs from an entry into total stats."""
        costs = entry.get("costs", {})
        try:
            cost_usd = float(costs.get("total_cost_usd", 0.0))
        except (ValueError, TypeError):
            cost_usd = 0.0
            
        try:
            chars = int(costs.get("tts_characters", 0))
        except (ValueError, TypeError):
            chars = 0
        
        if cost_usd > 0 or chars > 0:
            self.logger.debug("[HistoryManager._aggregate_run_cost] Adding $%.2f, %d chars", cost_usd, chars)
            
        self.aggregated_stats["openai_cost_usd"] += cost_usd
        self.aggregated_stats["tts_characters"] += chars
        self._save_stats()

    def record(self, entry: Dict) -> None:
        """
        Record a new run entry.
        
        Each run creates a new history entry, even if run_dir is the same.
        This allows tracking multiple runs of the same project.
        
        Args:
            entry: Run entry dictionary with topic, run_dir, etc.
        """
        entry.setdefault("timestamp", datetime.utcnow().isoformat() + "Z")
        topic = entry.get("topic", "unknown")
        run_dir = str(entry.get("run_dir", "unknown"))

        # Always create a new entry - don't de-duplicate by run_dir
        # Multiple runs of the same project should be tracked separately
        self.logger.info("[HistoryManager.record] Recording new entry: topic='%s', run_dir='%s', timestamp='%s'", 
                        topic, Path(run_dir).name if run_dir else "N/A", entry.get("timestamp", "unknown"))

        self.history.insert(0, entry)
        self.history = self.history[:200]  # Keep max 200 entries
        self._persist()

    def all(self) -> List[Dict]:
        """Get all history entries."""
        return list(self.history)

    def find(self, run_dir: Path | str) -> Optional[Dict]:
        """
        Find a history entry by run directory.
        
        Args:
            run_dir: Path to the run directory
            
        Returns:
            Entry dict if found, None otherwise
        """
        target = str(run_dir)
        for entry in self.history:
            if entry.get("run_dir") == target:
                return entry
        return None

    def remove_run(self, run_dir: Path) -> None:
        """
        Remove a run from history.
        
        Args:
            run_dir: Path to the run directory to remove
        """
        target = str(run_dir)
        before = len(self.history)
        
        self.logger.info("[HistoryManager.remove_run] Removing: %s", run_dir)
        
        # Find and aggregate costs before removing
        for entry in self.history:
            if entry.get("run_dir") == target:
                self._aggregate_run_cost(entry)
                break
                
        self.history = [entry for entry in self.history if entry.get("run_dir") != target]
        
        if len(self.history) != before:
            self.logger.debug("[HistoryManager.remove_run] Removed 1 entry, %d remaining", len(self.history))
            self._persist()
        else:
            self.logger.warning("[HistoryManager.remove_run] Entry not found in history")

    def clear_all_runs(self) -> None:
        """Clear all runs but preserve their costs in aggregated stats."""
        self.logger.info("[HistoryManager.clear_all_runs] Clearing all %d entries", len(self.history))
        for entry in self.history:
            self._aggregate_run_cost(entry)
        self.history = []
        self._persist()

    def get_aggregated_costs(self) -> Dict[str, float]:
        """Get total historical costs (including deleted runs)."""
        return self.aggregated_stats

    def update(self, run_dir: Path | str, patch: Dict) -> bool:
        """
        Update an existing history entry.
        
        Args:
            run_dir: Path to the run directory
            patch: Dictionary of fields to update
            
        Returns:
            True if entry was found and updated, False otherwise
        """
        target = str(run_dir)
        updated = False
        
        # Log what's being updated (with truncation for large values)
        patch_summary = {}
        for key, value in patch.items():
            if isinstance(value, list):
                patch_summary[key] = f"[{len(value)} items]"
            elif isinstance(value, str) and len(value) > 50:
                patch_summary[key] = f"{value[:50]}..."
            else:
                patch_summary[key] = value
        
        self.logger.debug("[HistoryManager.update] Updating %s with: %s", 
                         Path(target).name, patch_summary)
        
        for entry in self.history:
            if entry.get("run_dir") == target:
                entry.update(patch)
                updated = True
                break
                
        if updated:
            self._persist()
            self.logger.info("[HistoryManager.update] Entry updated successfully")
        else:
            self.logger.warning("[HistoryManager.update] Entry not found for: %s", target)
            
        return updated

    def update_chat_log(self, run_dir: Path | str, chat_log: List[str]) -> bool:
        """
        Update chat log for a specific entry.
        
        Convenience method for updating just the chat_log field.
        
        Args:
            run_dir: Path to the run directory
            chat_log: List of chat messages
            
        Returns:
            True if updated successfully
        """
        self.logger.info("[HistoryManager.update_chat_log] Saving %d messages for %s", 
                        len(chat_log), Path(str(run_dir)).name)
        return self.update(run_dir, {"chat_log": chat_log})

    def _persist(self) -> None:
        """Persist history to disk."""
        start_time = time.time()
        try:
            self.history_path.write_text(
                json.dumps(self.history, ensure_ascii=False, indent=2), 
                encoding="utf-8"
            )
            elapsed = time.time() - start_time
            self.logger.debug("[HistoryManager._persist] Saved %d entries in %.3fs", 
                             len(self.history), elapsed)
        except OSError as exc:
            self.logger.error("[HistoryManager._persist] Failed to write history file: %s", exc)

    def get_costs_since(self, start_date: datetime) -> Dict[str, float]:
        """
        Aggregate costs from history entries occurring on/after start_date.

        Args:
            start_date: Inclusive lower bound for entry dates (UTC-aware or naive).

        Returns:
            Dictionary of summed cost fields (e.g., openai_cost_usd, tts_characters).
        """
        totals: Dict[str, float] = {}

        # Normalize baseline to naive UTC to avoid offset-aware vs naive comparison errors
        if start_date.tzinfo:
            start_cmp = start_date.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            start_cmp = start_date

        for entry in self.history:
            date_str = entry.get("date") or entry.get("timestamp") or ""
            try:
                # Handle timestamps with trailing 'Z'
                parsed_raw = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                parsed_raw = None

            if parsed_raw:
                parsed = (
                    parsed_raw.astimezone(timezone.utc).replace(tzinfo=None)
                    if parsed_raw.tzinfo
                    else parsed_raw
                )
            else:
                parsed = None

            if parsed and parsed < start_cmp:
                continue

            costs = entry.get("costs") or {}
            for key, value in costs.items():
                if isinstance(value, (int, float)):
                    totals[key] = totals.get(key, 0.0) + float(value)

        return totals

