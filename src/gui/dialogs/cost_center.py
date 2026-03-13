"""
M.B.S Studio - Cost Center Dialog
===================================

Centralized cost management dialog with budget tracking,
clear tables, analytics charts, and cost calculator.

Author: M.B.S Studio
Version: 2.0.0 - Redesigned with tabbed interface
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QByteArray, QSettings, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from pyqtgraph import PlotWidget

from src.utils import HistoryManager, Settings
from src.utils.costs import get_cycle_start_date
from src.utils.pricing import (
    PRICING,
    azure_vs_elevenlabs_diff,
    gpt_cost,
    tts_cost,
    visual_cost,
)
from ..constants import (
    AZURE_GPT_INPUT_COST,
    AZURE_GPT_OUTPUT_COST,
    AZURE_TTS_COST_PER_THOUSAND,
    CHARS_PER_WORD,
    ELEVENLABS_COST_PER_THOUSAND,
    GEMINI_INPUT_COST,
    GEMINI_OUTPUT_COST,
    IMAGEN_COST_PER_IMAGE,
    OUTPUT_TOKEN_RATIO,
    PPT_EXTRA_COST,
    TOKENS_PER_WORD,
    TTS_COST_PER_MILLION,
    VEO_COST_PER_SECOND,
    VIDEO_OVERHEAD_COST,
    WORDS_PER_MINUTE,
    QSETTINGS_APP,
    QSETTINGS_ORG,
)


# Table styling constants
TABLE_STYLE = """
QTableWidget {
    background-color: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 8px;
    gridline-color: #1e293b;
    color: #e2e8f0;
    font-size: 13px;
}
QTableWidget::item {
    padding: 8px 12px;
    border-bottom: 1px solid #1e293b;
}
QTableWidget::item:selected {
    background-color: #1e3a8a;
}
QTableWidget::item:alternate {
    background-color: #0b1220;
}
QHeaderView::section {
    background-color: #1e293b;
    color: #94a3b8;
    padding: 10px 12px;
    border: none;
    border-bottom: 2px solid #334155;
    font-weight: 600;
    font-size: 12px;
}
QHeaderView::section:first {
    border-top-left-radius: 8px;
}
QHeaderView::section:last {
    border-top-right-radius: 8px;
}
"""


@dataclass
class ProviderBudget:
    """Container for budget row rendering."""
    service: str
    usage: str
    cost: str
    budget: str
    pct: float
    reset_day: int
    days_left: int
    time_pct: float


class CostCenterDialog(QDialog):
    """
    Cost management and analytics dialog with tabbed interface.
    
    Provides:
        - Tab 1: Clear tables for budget and history
        - Tab 2: Charts and analytics
        - Cost calculator with Azure + ElevenLabs support
    """

    def __init__(
        self,
        settings: Settings,
        history: HistoryManager,
        cost_totals: Dict[str, float],
        section_label_factory: Callable[[str], QLabel],
        card_widget_factory: Callable[[], QWidget],
        helper_label_factory: Callable[[str], QLabel],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.history = history
        self.cost_totals = cost_totals
        self._section_label = section_label_factory
        self._card_widget = card_widget_factory
        self._helper_label = helper_label_factory
        self._last_scroll_pos: int = 0
        self._last_tab_index: int = 0
        self._settings_store = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        self._restored_geometry: Optional[QByteArray] = None
        self._load_persisted_settings()
        
        self._setup_window()
        self._setup_ui()
        self._restore_state()
        self._refresh_all()

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.setWindowTitle("מרכז עלויות וניתוח")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        # Friendlier default size that fits on smaller screens, still resizable
        available = self.screen().availableGeometry() if self.screen() else None
        target_w, target_h = 900, 640
        if available:
            target_w = min(target_w, max(600, available.width() - 80))
            target_h = min(target_h, max(440, available.height() - 80))
        self.resize(target_w, target_h)
        self.setMinimumSize(max(600, target_w - 260), max(440, target_h - 260))
        self.setSizeGripEnabled(True)

    def _load_persisted_settings(self) -> None:
        """Load persisted geometry and reset days from QSettings."""
        try:
            def _clamp_day(value: object, fallback: int) -> int:
                try:
                    return max(1, min(31, int(value)))
                except Exception:
                    return fallback

            azure_day = self._settings_store.value("cost_center/reset_day_azure")
            gemini_day = self._settings_store.value("cost_center/reset_day_gemini")
            eleven_day = self._settings_store.value("cost_center/reset_day_elevenlabs")

            if azure_day is not None:
                self.settings.reset_day_azure = _clamp_day(azure_day, getattr(self.settings, "reset_day_azure", 14))
            if gemini_day is not None:
                self.settings.reset_day_gemini = _clamp_day(gemini_day, getattr(self.settings, "reset_day_gemini", 1))
            if eleven_day is not None:
                self.settings.reset_day_elevenlabs = _clamp_day(eleven_day, getattr(self.settings, "reset_day_elevenlabs", 1))

            stored_tab = self._settings_store.value("cost_center/tab_index")
            if stored_tab is not None:
                try:
                    self._last_tab_index = max(0, int(stored_tab))
                except Exception:
                    self._last_tab_index = 0

            stored_scroll = self._settings_store.value("cost_center/scroll_pos")
            if stored_scroll is not None:
                try:
                    self._last_scroll_pos = max(0, int(stored_scroll))
                except Exception:
                    self._last_scroll_pos = 0

            geom_val = self._settings_store.value("cost_center/geometry", type=QByteArray)
            if isinstance(geom_val, QByteArray) and not geom_val.isEmpty():
                self._restored_geometry = geom_val
            elif isinstance(geom_val, (bytes, bytearray)):
                self._restored_geometry = QByteArray(geom_val)
            elif isinstance(geom_val, str) and geom_val:
                try:
                    self._restored_geometry = QByteArray.fromHex(geom_val.encode("ascii"))
                except Exception:
                    self._restored_geometry = None
        except Exception:
            # If QSettings is unreadable, continue with defaults
            self._restored_geometry = None

    def _setup_ui(self) -> None:
        """Build the tabbed dialog UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)
        
        # Title row with date
        title_row = QHBoxLayout()
        title = QLabel("מרכז עלויות וניתוח")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #22c55e;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        
        # Current date display
        self.date_label = QLabel()
        self._update_date_display()
        self.date_label.setStyleSheet(
            "font-size: 13px; color: #94a3b8; background: #1e293b; "
            "padding: 6px 12px; border-radius: 6px;"
        )
        title_row.addWidget(self.date_label)
        main_layout.addLayout(title_row)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #1e293b;
                border-radius: 8px;
                background: #0f172a;
            }
            QTabBar::tab {
                background: #1e293b;
                color: #94a3b8;
                padding: 10px 24px;
                margin-right: 4px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QTabBar::tab:selected {
                background: #0f172a;
                color: #22c55e;
                border-bottom: 2px solid #22c55e;
            }
            QTabBar::tab:hover:!selected {
                background: #334155;
            }
        """)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # Tab 1: Tables
        self.tabs.addTab(self._build_tables_tab(), "טבלאות עלויות")
        
        # Tab 2: Charts
        self.tabs.addTab(self._build_charts_tab(), "גרפים וניתוח")
        self.tabs.currentChanged.connect(lambda idx: setattr(self, "_last_tab_index", idx))
        main_layout.addWidget(self.tabs, 1)

        # Calculator section (always visible)
        calculator = self._build_calculator()
        calculator.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        main_layout.addWidget(calculator)

    def _restore_state(self) -> None:
        """Restore geometry, tab, and scroll position for the dialog."""
        if self._restored_geometry:
            try:
                self.restoreGeometry(self._restored_geometry)
            except Exception:
                pass
        else:
            geo_hex = getattr(self.settings, "cost_center_geometry", "") or ""
            if isinstance(geo_hex, str) and geo_hex:
                try:
                    self.restoreGeometry(QByteArray.fromHex(geo_hex.encode("ascii")))
                except Exception:
                    # Ignore corrupted geometry
                    pass

        stored_tab = self._last_tab_index or getattr(self.settings, "cost_center_tab_index", 0)
        if isinstance(stored_tab, int) and 0 <= stored_tab < self.tabs.count():
            self._last_tab_index = stored_tab
            self.tabs.setCurrentIndex(stored_tab)

        # Scroll position retained for backward compatibility; no-op without a scroll area
        stored_scroll = getattr(self.settings, "cost_center_scroll_pos", 0)
        try:
            self._last_scroll_pos = max(0, int(stored_scroll))
        except Exception:
            self._last_scroll_pos = 0

    def _persist_reset_days(self) -> None:
        """Persist reset-day spin boxes immediately."""
        try:
            self.settings.reset_day_azure = int(self.reset_day_azure_spin.value())
            self.settings.reset_day_gemini = int(self.reset_day_gemini_spin.value())
            self.settings.reset_day_elevenlabs = int(self.reset_day_elevenlabs_spin.value())
            # Maintain legacy budget_reset_day alignment
            self.settings.budget_reset_day = self.settings.reset_day_azure

            payload = {
                "budget_reset_day": self.settings.budget_reset_day,
                "reset_day_azure": self.settings.reset_day_azure,
                "reset_day_gemini": self.settings.reset_day_gemini,
                "reset_day_elevenlabs": self.settings.reset_day_elevenlabs,
            }
            self.settings.save_ui_preferences(payload)

            self._settings_store.setValue("cost_center/reset_day_azure", self.settings.reset_day_azure)
            self._settings_store.setValue("cost_center/reset_day_gemini", self.settings.reset_day_gemini)
            self._settings_store.setValue("cost_center/reset_day_elevenlabs", self.settings.reset_day_elevenlabs)
            self._settings_store.sync()
        except Exception:
            # Non-fatal persistence failure
            pass

    def _update_counters(self) -> None:
        """Update run/metadata counters for the dashboard badges."""
        entries = self.history.all() or []
        run_count = len(entries)
        metadata_count = 0
        visual_metadata_count = 0

        for entry in entries:
            run_dir = entry.get("run_dir") or ""
            if not run_dir:
                continue
            path = Path(run_dir)
            if (path / "metadata.json").exists():
                metadata_count += 1
            if (path / "visual_metadata.json").exists():
                visual_metadata_count += 1

        self.run_count_label.setText(f"סה\"כ ריצות: {run_count}")
        self.metadata_count_label.setText(f"מטא דאטה: {metadata_count}")
        self.visual_metadata_count_label.setText(f"מטא דאטה ויזואלי: {visual_metadata_count}")

    def _update_date_display(self) -> None:
        """Update the current date display in the header."""
        try:
            today = datetime.now(timezone.utc).date()
            # Format: יום, תאריך (Hebrew format)
            days_he = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
            day_name = days_he[today.weekday()]
            date_str = today.strftime("%d.%m.%Y")
            self.date_label.setText(f"{day_name}, {date_str}")
        except Exception:
            # Fallback to simple date if formatting fails
            try:
                today = datetime.now(timezone.utc).date()
                self.date_label.setText(today.strftime("%d.%m.%Y"))
            except Exception:
                self.date_label.setText("")

    def _save_geometry_qsettings(self) -> None:
        """Persist geometry to QSettings without touching disk-heavy JSON."""
        try:
            geometry_bytes = self.saveGeometry()
            self._settings_store.setValue("cost_center/geometry", geometry_bytes)
            self._settings_store.sync()
        except Exception:
            pass

    def _build_tables_tab(self) -> QWidget:
        """Build the tables tab content."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Budget Summary Table
        layout.addWidget(self._section_label("סיכום תקציב חודשי"))

        counters_row = QHBoxLayout()
        counters_row.setSpacing(12)
        self.run_count_label = QLabel()
        self.metadata_count_label = QLabel()
        self.visual_metadata_count_label = QLabel()
        for lbl in (self.run_count_label, self.metadata_count_label, self.visual_metadata_count_label):
            lbl.setStyleSheet(
                "font-size: 14px; font-weight: 600; color: #38bdf8; "
                "background: #1e293b; padding: 8px 12px; border-radius: 8px;"
            )
            counters_row.addWidget(lbl)
        counters_row.addStretch(1)
        layout.addLayout(counters_row)
        self.budget_table = self._create_budget_table()
        self.budget_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.budget_table.setMaximumHeight(220)
        layout.addWidget(self.budget_table)
        
        # Budget limits controls (grid for clean alignment)
        limits_grid = QGridLayout()
        limits_grid.setHorizontalSpacing(12)
        limits_grid.setVerticalSpacing(8)

        self.openai_spin = QDoubleSpinBox()
        self.openai_spin.setPrefix("$ ")
        self.openai_spin.setRange(10.0, 5000.0)
        self.openai_spin.setValue(self.settings.monthly_openai_cost_limit)
        limits_grid.addWidget(QLabel("מגבלת AI:"), 0, 0)
        limits_grid.addWidget(self.openai_spin, 0, 1)
        
        self.tts_spin = QDoubleSpinBox()
        self.tts_spin.setRange(50000, 5000000)
        self.tts_spin.setDecimals(0)
        self.tts_spin.setSuffix(" תווים")
        self.tts_spin.setValue(float(self.settings.monthly_tts_character_limit))
        limits_grid.addWidget(QLabel("מגבלת TTS:"), 0, 2)
        limits_grid.addWidget(self.tts_spin, 0, 3)

        self.elevenlabs_spin = QDoubleSpinBox()
        self.elevenlabs_spin.setRange(50000, 5000000)
        self.elevenlabs_spin.setDecimals(0)
        self.elevenlabs_spin.setSuffix(" תווים")
        self.elevenlabs_spin.setValue(float(getattr(self.settings, "monthly_elevenlabs_character_limit", 100000)))
        limits_grid.addWidget(QLabel("מגבלת ElevenLabs:"), 0, 4)
        limits_grid.addWidget(self.elevenlabs_spin, 0, 5)

        self.reset_day_azure_spin = QSpinBox()
        self.reset_day_azure_spin.setRange(1, 31)
        self.reset_day_azure_spin.setValue(getattr(self.settings, "reset_day_azure", 14))
        self.reset_day_azure_spin.setToolTip("היום בחודש בו Azure Speech/OpenAI מתאפסים (1-31)")
        limits_grid.addWidget(QLabel("יום איפוס Azure:"), 1, 0)
        limits_grid.addWidget(self.reset_day_azure_spin, 1, 1)
        self.reset_day_azure_spin.valueChanged.connect(lambda _: self._persist_reset_days())

        self.reset_day_gemini_spin = QSpinBox()
        self.reset_day_gemini_spin.setRange(1, 31)
        self.reset_day_gemini_spin.setValue(getattr(self.settings, "reset_day_gemini", 1))
        self.reset_day_gemini_spin.setToolTip("היום בחודש בו Gemini מתאפס (1-31)")
        limits_grid.addWidget(QLabel("יום איפוס Gemini:"), 1, 2)
        limits_grid.addWidget(self.reset_day_gemini_spin, 1, 3)
        self.reset_day_gemini_spin.valueChanged.connect(lambda _: self._persist_reset_days())

        self.reset_day_elevenlabs_spin = QSpinBox()
        self.reset_day_elevenlabs_spin.setRange(1, 31)
        self.reset_day_elevenlabs_spin.setValue(getattr(self.settings, "reset_day_elevenlabs", 1))
        self.reset_day_elevenlabs_spin.setToolTip("היום בחודש בו ElevenLabs מתאפס (1-31)")
        limits_grid.addWidget(QLabel("יום איפוס ElevenLabs:"), 1, 4)
        limits_grid.addWidget(self.reset_day_elevenlabs_spin, 1, 5)
        self.reset_day_elevenlabs_spin.valueChanged.connect(lambda _: self._persist_reset_days())
        
        save_btn = QPushButton("שמור מגבלות")
        save_btn.clicked.connect(self._save_limits)
        limits_grid.addWidget(save_btn, 0, 6, 2, 1)
        limits_grid.setColumnStretch(6, 1)
        layout.addLayout(limits_grid)
        
        # Recent Runs Table
        layout.addWidget(self._section_label("היסטוריית הרצות (30 אחרונות)"))
        self.history_table = self._create_history_table()
        # Lower minimum to allow dialog to shrink on shorter screens
        self.history_table.setMinimumHeight(140)
        self.history_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.history_table, 1)
        layout.addStretch(1)

        scroll.setWidget(content)
        tab_layout.addWidget(scroll)
        return tab

    def _create_budget_table(self) -> QTableWidget:
        """Create the budget summary table."""
        table = QTableWidget()
        table.setStyleSheet(TABLE_STYLE)
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "שירות",
            "שימוש",
            "עלות",
            "תקציב",
            "יום איפוס",
            "ימים לסיום",
            "התקדמות מחזור",
            "סטטוס",
        ])
        table.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setMaximumHeight(180)
        return table

    def _create_history_table(self) -> QTreeWidget:
        """Create the grouped history tree."""
        tree = QTreeWidget()
        tree.setStyleSheet(TABLE_STYLE)
        tree.setColumnCount(6)
        tree.setHeaderLabels([
            "תאריך", "נושא", "עלות AI", "עלות TTS", "ספק TTS", "סה״כ"
        ])
        tree.setRootIsDecorated(True)
        tree.setAlternatingRowColors(True)
        tree.setSortingEnabled(False)
        tree.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        return tree

    def _build_charts_tab(self) -> QWidget:
        """Build the charts tab content."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Chart controls
        controls = QHBoxLayout()
        controls.addWidget(QLabel("מדד להצגה:"))
        
        self.metric_combo = QComboBox()
        self.metric_combo.addItem("עלות יומית ($)", "cost")
        self.metric_combo.addItem("תווים לדיבור (באלפים)", "tts")
        self.metric_combo.addItem("עלות TTS בלבד", "tts_cost")
        self.metric_combo.currentIndexChanged.connect(lambda _: self._refresh_chart())
        controls.addWidget(self.metric_combo)
        controls.addStretch(1)
        layout.addLayout(controls)
        
        # Chart
        self.chart = PlotWidget(background="#0f172a")
        # Lower minimum to allow smaller dialog height
        self.chart.setMinimumHeight(150)
        self.chart.showGrid(x=True, y=True, alpha=0.15)
        layout.addWidget(self.chart, 1)
        
        # Placeholder
        self.chart_placeholder = QLabel("אין עדיין נתוני Pipeline להצגה.")
        self.chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chart_placeholder.setProperty("class", "helper")
        self.chart_placeholder.setStyleSheet("color: #64748b; font-size: 14px;")
        layout.addWidget(self.chart_placeholder)
        
        # Summary stats
        stats_row = QHBoxLayout()
        self.avg_label = QLabel("$0.00 ממוצע")
        self.max_label = QLabel("$0.00 שיא")
        self.tts_label = QLabel("0 תווי TTS")
        
        for lbl in (self.avg_label, self.max_label, self.tts_label):
            lbl.setStyleSheet(
                "font-size: 16px; font-weight: 600; color: #38bdf8; "
                "background: #1e293b; padding: 8px 16px; border-radius: 8px;"
            )
            stats_row.addWidget(lbl)
        stats_row.addStretch(1)
        layout.addLayout(stats_row)
        
        return tab

    def _build_calculator(self) -> QGroupBox:
        """Build the cost calculator widget with TTS provider selection."""
        calc_group = QGroupBox("מחשבון עלויות מהיר")
        calc_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                color: #22c55e;
                border: 1px solid #1e293b;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
            }
        """)
        # Allow the calculator section to compress when the dialog is resized
        calc_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        calc_layout = QGridLayout(calc_group)
        calc_layout.setSpacing(12)

        # Row 0: Words and Minutes
        calc_layout.addWidget(QLabel("מספר מילים:"), 0, 0)
        self.words_input = QSpinBox()
        self.words_input.setRange(0, 100000)
        self.words_input.setValue(8000)
        calc_layout.addWidget(self.words_input, 0, 1)

        calc_layout.addWidget(QLabel("משך בדקות:"), 0, 2)
        self.minutes_input = QDoubleSpinBox()
        self.minutes_input.setRange(0.0, 240.0)
        self.minutes_input.setDecimals(1)
        self.minutes_input.setValue(30.0)
        calc_layout.addWidget(self.minutes_input, 0, 3)

        # Row 1: AI Model and TTS Provider
        calc_layout.addWidget(QLabel("מודל AI:"), 1, 0)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Azure GPT-4o", "azure")
        self.provider_combo.addItem("Gemini 2.5 Flash", "gemini")
        calc_layout.addWidget(self.provider_combo, 1, 1)

        calc_layout.addWidget(QLabel("ספק TTS:"), 1, 2)
        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.addItem("Azure Neural ($0.016/1K)", "azure")
        self.tts_provider_combo.addItem("ElevenLabs ($0.30/1K)", "elevenlabs")
        calc_layout.addWidget(self.tts_provider_combo, 1, 3)

        # Row 2: Visual Generator
        calc_layout.addWidget(QLabel("ויזואליזציה:"), 2, 0)
        self.visual_gen_combo = QComboBox()
        self.visual_gen_combo.addItem("Manim (חינם)", "manim")
        self.visual_gen_combo.addItem("Imagen 4 ($0.04/תמונה)", "imagen")
        self.visual_gen_combo.addItem("VEO ($0.75/שנייה)", "veo")
        self.visual_gen_combo.addItem("היברידי (Manim + Google AI)", "hybrid")
        calc_layout.addWidget(self.visual_gen_combo, 2, 1)
        
        calc_layout.addWidget(QLabel("תמונות/שניות:"), 2, 2)
        self.visual_count_input = QSpinBox()
        self.visual_count_input.setRange(0, 100)
        self.visual_count_input.setValue(5)
        self.visual_count_input.setToolTip("מספר תמונות (Imagen) או שניות (VEO)")
        calc_layout.addWidget(self.visual_count_input, 2, 3)

        # Row 3: Output toggles
        toggle_row = QHBoxLayout()
        self.audio_cb = QCheckBox("כולל אודיו")
        self.audio_cb.setChecked(True)
        self.video_cb = QCheckBox("כולל וידאו")
        self.video_cb.setChecked(True)
        self.ppt_cb = QCheckBox("כולל PPTX")
        self.ppt_cb.setChecked(False)
        toggle_row.addWidget(self.audio_cb)
        toggle_row.addWidget(self.video_cb)
        toggle_row.addWidget(self.ppt_cb)
        toggle_row.addStretch(1)
        
        calc_button = QPushButton("חשב עלות משוערת")
        calc_button.setStyleSheet("""
            QPushButton {
                background: #22c55e;
                color: white;
                font-weight: 600;
                padding: 10px 24px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: #16a34a;
            }
        """)
        calc_button.clicked.connect(self._run_calculator)
        toggle_row.addWidget(calc_button)
        calc_layout.addLayout(toggle_row, 3, 0, 1, 4)

        # Row 4: Result
        self.calc_result = QLabel("הזינו נתונים ולחצו על \"חשב עלות משוערת\".")
        self.calc_result.setWordWrap(True)
        self.calc_result.setStyleSheet(
            "color: #94a3b8; background: #1e293b; padding: 12px; "
            "border-radius: 8px; font-size: 13px;"
        )
        calc_layout.addWidget(self.calc_result, 4, 0, 1, 4)

        return calc_group

    def _refresh_all(self) -> None:
        """Refresh all components."""
        self._update_date_display()
        self._update_counters()
        self._fill_budget_table()
        self._fill_history_table()
        self._refresh_chart()

    def _render_budget_row(self, row_idx: int, row: ProviderBudget) -> None:
        """Render a single provider budget row with consistent styling."""
        self.budget_table.setItem(row_idx, 0, QTableWidgetItem(row.service))
        self.budget_table.setItem(row_idx, 1, QTableWidgetItem(row.usage))
        self.budget_table.setItem(row_idx, 2, QTableWidgetItem(row.cost))
        self.budget_table.setItem(row_idx, 3, QTableWidgetItem(row.budget))

        reset_item = QTableWidgetItem(str(row.reset_day))
        reset_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.budget_table.setItem(row_idx, 4, reset_item)

        days_item = QTableWidgetItem(str(row.days_left))
        days_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if row.days_left < 3:
            days_item.setForeground(QColor("#ef4444"))
        self.budget_table.setItem(row_idx, 5, days_item)

        progress_widget = QWidget()
        progress_widget.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        progress_layout = QHBoxLayout(progress_widget)
        progress_layout.setContentsMargins(4, 0, 4, 0)
        progress_layout.setSpacing(6)
        cycle_progress = QProgressBar()
        cycle_progress.setRange(0, 100)
        cycle_progress.setValue(int(row.time_pct))
        cycle_progress.setFormat(f"עוד {row.days_left} ימים לאיפוס")
        cycle_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cycle_progress.setMinimumWidth(140)
        progress_layout.addWidget(cycle_progress)
        self.budget_table.setCellWidget(row_idx, 6, progress_widget)

        status_item = QTableWidgetItem()
        budget_pct = row.pct
        safe_icon = row.time_pct >= 90 and budget_pct < 20
        if safe_icon:
            status_item.setText("✅ Safe")
            status_item.setForeground(QColor("#22c55e"))
        else:
            status_item.setText(f"{budget_pct:.0f}%")
            if budget_pct >= 90:
                status_item.setForeground(QColor("#ef4444"))
            elif budget_pct >= 70:
                status_item.setForeground(QColor("#f97316"))
            else:
                status_item.setForeground(QColor("#22c55e"))
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if budget_pct >= 90 and row.days_left > 5:
            font = status_item.font()
            font.setBold(True)
            status_item.setFont(font)
            days_item.setForeground(QColor("#ef4444"))
            days_font = days_item.font()
            days_font.setBold(True)
            days_item.setFont(days_font)
        self.budget_table.setItem(row_idx, 7, status_item)

    def _fill_budget_table(self) -> None:
        """Fill the budget summary table."""
        totals = self.cost_totals if isinstance(self.cost_totals, dict) else {}
        entries = self.history.all() or []
        today_utc = datetime.now(timezone.utc).date()

        openai_limit = max(self.settings.monthly_openai_cost_limit, 0.01)
        tts_limit = max(float(self.settings.monthly_tts_character_limit), 1.0)
        elevenlabs_limit = max(float(getattr(self.settings, "monthly_elevenlabs_character_limit", 100000)), 1.0)
        google_ai_limit = max(float(getattr(self.settings, "monthly_google_ai_limit", 50.0)), 0.01)

        def _clamp_day(val: int) -> int:
            try:
                return max(1, min(31, int(val)))
            except Exception:
                return 1

        def _parse_entry_date(entry: Dict) -> Optional[datetime]:
            """Parse entry date and normalize to UTC-aware datetime."""
            date_str = entry.get("timestamp") or entry.get("date") or ""
            if not date_str:
                return None
            dt: Optional[datetime] = None
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                except Exception:
                    dt = None
            if dt is None:
                return None
            if dt.tzinfo:
                return dt.astimezone(timezone.utc)
            return dt.replace(tzinfo=timezone.utc)

        def _aggregate_since(start_dt: datetime) -> Dict[str, float]:
            totals_inner: Dict[str, float] = {}
            start_cmp = start_dt if start_dt.tzinfo else start_dt.replace(tzinfo=timezone.utc)
            for entry in entries:
                dt = _parse_entry_date(entry)
                if dt and dt.tzinfo:
                    dt = dt.astimezone(timezone.utc)
                if dt and dt < start_cmp:
                    continue
                costs = entry.get("costs") or {}
                if not isinstance(costs, dict):
                    continue
                for key, value in costs.items():
                    if isinstance(value, (int, float)):
                        totals_inner[key] = totals_inner.get(key, 0.0) + float(value)
            return totals_inner

        def _cycle_window(reset_day: int) -> Tuple[datetime, date, int, float]:
            """Return (start_dt, next_reset_date, days_left, time_pct)."""
            start_dt = get_cycle_start_date(reset_day, today_utc)
            safe_day = _clamp_day(reset_day)
            if today_utc.day >= safe_day:
                year = today_utc.year + (1 if today_utc.month == 12 else 0)
                month = 1 if today_utc.month == 12 else today_utc.month + 1
            else:
                year = today_utc.year
                month = today_utc.month
            day = min(safe_day, calendar.monthrange(year, month)[1])
            next_reset_dt = datetime(year, month, day, tzinfo=timezone.utc)
            next_reset_date = next_reset_dt.date()
            days_left = max(0, (next_reset_date - today_utc).days)
            cycle_len = max(1, (next_reset_date - start_dt.date()).days or 1)
            elapsed_days = min(cycle_len, cycle_len - days_left)
            time_pct = min(100.0, max(0.0, (elapsed_days / cycle_len) * 100))
            return start_dt, next_reset_date, days_left, time_pct

        azure_start, azure_next, azure_days_left, azure_time_pct = _cycle_window(
            getattr(self.settings, "reset_day_azure", getattr(self.settings, "budget_reset_day", 14))
        )
        gemini_start, gemini_next, gemini_days_left, gemini_time_pct = _cycle_window(
            getattr(self.settings, "reset_day_gemini", 1)
        )
        eleven_start, eleven_next, eleven_days_left, eleven_time_pct = _cycle_window(
            getattr(self.settings, "reset_day_elevenlabs", 1)
        )

        azure_cycle = _aggregate_since(azure_start)
        gemini_cycle = _aggregate_since(gemini_start)
        eleven_cycle = _aggregate_since(eleven_start)

        def _as_int(val: object) -> int:
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0

        def _as_float(val: object) -> float:
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0

        # Azure / OpenAI text
        cycle_openai_cost = _as_float(azure_cycle.get("openai_cost_usd", azure_cycle.get("total_cost_usd", 0.0)))
        total_openai = _as_float(totals.get("openai_cost_usd", totals.get("total_cost_usd", 0.0)))
        cycle_tokens = _as_int(azure_cycle.get("prompt_tokens", 0)) + _as_int(azure_cycle.get("completion_tokens", 0))
        total_tokens = _as_int(totals.get("prompt_tokens", 0)) + _as_int(totals.get("completion_tokens", 0))
        ai_pct = min(100.0, (cycle_openai_cost / openai_limit) * 100 if openai_limit else 0.0)

        # Azure TTS
        cycle_azure_tts_chars = _as_int(azure_cycle.get("tts_characters", 0))
        cycle_azure_tts_cost = _as_float(
            azure_cycle.get("azure_tts_cost_usd", cycle_azure_tts_chars * AZURE_TTS_COST_PER_THOUSAND / 1000)
        )
        total_azure_tts_chars = _as_int(totals.get("tts_characters", 0))
        total_azure_tts_cost = _as_float(
            totals.get("azure_tts_cost_usd", total_azure_tts_chars * AZURE_TTS_COST_PER_THOUSAND / 1000)
        )
        azure_tts_pct = min(100.0, (cycle_azure_tts_chars / tts_limit) * 100 if tts_limit else 0.0)

        # ElevenLabs TTS
        cycle_eleven_chars = _as_int(eleven_cycle.get("elevenlabs_characters", 0))
        cycle_eleven_cost = _as_float(
            eleven_cycle.get("elevenlabs_tts_cost_usd", cycle_eleven_chars * ELEVENLABS_COST_PER_THOUSAND / 1000)
        )
        total_eleven_chars = _as_int(totals.get("elevenlabs_characters", 0))
        total_eleven_cost = _as_float(
            totals.get("elevenlabs_tts_cost_usd", total_eleven_chars * ELEVENLABS_COST_PER_THOUSAND / 1000)
        )
        elevenlabs_pct = min(100.0, (cycle_eleven_chars / elevenlabs_limit) * 100 if elevenlabs_limit else 0.0)

        # Google AI visuals (Imagen + VEO) treated as Gemini cycle
        imagen_cycle_cost = _as_float(gemini_cycle.get("imagen_cost_usd", 0.0))
        veo_cycle_cost = _as_float(gemini_cycle.get("veo_cost_usd", 0.0))
        visual_cycle_cost = _as_float(gemini_cycle.get("visual_cost_usd", imagen_cycle_cost + veo_cycle_cost))
        imagen_cycle_images = _as_int(gemini_cycle.get("imagen_images", 0))
        veo_cycle_seconds = _as_float(gemini_cycle.get("veo_seconds", 0.0))

        imagen_total_cost = _as_float(totals.get("imagen_cost_usd", 0.0))
        veo_total_cost = _as_float(totals.get("veo_cost_usd", 0.0))
        visual_total_cost = _as_float(totals.get("visual_cost_usd", imagen_total_cost + veo_total_cost))
        imagen_total_images = _as_int(totals.get("imagen_images", 0))
        veo_total_seconds = _as_float(totals.get("veo_seconds", 0.0))

        google_ai_cycle_cost = imagen_cycle_cost + veo_cycle_cost if visual_cycle_cost == 0 else visual_cycle_cost
        google_ai_total_cost = imagen_total_cost + veo_total_cost if visual_total_cost == 0 else visual_total_cost
        google_ai_pct = min(100.0, (google_ai_cycle_cost / google_ai_limit) * 100 if google_ai_limit else 0.0)

        rows: List[ProviderBudget] = [
            ProviderBudget(
                service="מוח AI (טקסט)",
                usage=f'מחזור: {cycle_tokens:,} טוקנים | סה"כ: {total_tokens:,} טוקנים',
                cost=f'מחזור: ${cycle_openai_cost:.2f} | סה"כ: ${total_openai:.2f}',
                budget=f'${openai_limit:.0f}',
                pct=ai_pct,
                reset_day=_clamp_day(getattr(self.settings, "reset_day_azure", 14)),
                days_left=azure_days_left,
                time_pct=azure_time_pct,
            ),
            ProviderBudget(
                service="יצירת קול (Speech) - Azure",
                usage=f'מחזור: {cycle_azure_tts_chars:,} תווים | סה"כ: {total_azure_tts_chars:,} תווים',
                cost=f'מחזור: ${cycle_azure_tts_cost:.2f} | סה"כ: ${total_azure_tts_cost:.2f}',
                budget=f'{int(tts_limit):,} תווים',
                pct=azure_tts_pct,
                reset_day=_clamp_day(getattr(self.settings, "reset_day_azure", 14)),
                days_left=azure_days_left,
                time_pct=azure_time_pct,
            ),
            ProviderBudget(
                service="יצירת קול (Speech) - ElevenLabs",
                usage=f'מחזור: {cycle_eleven_chars:,} תווים | סה"כ: {total_eleven_chars:,} תווים',
                cost=f'מחזור: ${cycle_eleven_cost:.2f} | סה"כ: ${total_eleven_cost:.2f}',
                budget=f'{int(elevenlabs_limit):,} תווים',
                pct=elevenlabs_pct,
                reset_day=_clamp_day(getattr(self.settings, "reset_day_elevenlabs", 1)),
                days_left=eleven_days_left,
                time_pct=eleven_time_pct,
            ),
            ProviderBudget(
                service="Google AI (Imagen + VEO)",
                usage=f'Imagen: {imagen_cycle_images} / {imagen_total_images} | VEO: {veo_cycle_seconds:.1f}s / {veo_total_seconds:.1f}s',
                cost=f'מחזור: ${google_ai_cycle_cost:.2f} | סה"כ: ${google_ai_total_cost:.2f}',
                budget=f'${google_ai_limit:.0f}',
                pct=google_ai_pct,
                reset_day=_clamp_day(getattr(self.settings, "reset_day_gemini", 1)),
                days_left=gemini_days_left,
                time_pct=gemini_time_pct,
            ),
        ]

        self.budget_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            self._render_budget_row(row_idx, row)

    def _fill_history_table(self) -> None:
        """Fill the history tree grouped by month."""
        entries_raw = self.history.all() or []
        entries = entries_raw if isinstance(entries_raw, list) else []
        was_sorting = self.history_table.isSortingEnabled()
        self.history_table.setSortingEnabled(False)
        self.history_table.setUpdatesEnabled(False)
        self.history_table.clear()
        self.history_table.setColumnCount(6)
        self.history_table.setHeaderLabels([
            "תאריך", "נושא", "עלות AI", "עלות TTS", "ספק TTS", "סה״כ"
        ])

        months_he = [
            "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
            "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"
        ]

        def _parse_date(entry: Dict) -> datetime:
            date_str = entry.get("timestamp") or entry.get("date") or ""
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                try:
                    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    return datetime.now(timezone.utc)

        def _as_float(val: object) -> float:
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0

        grouped: Dict[Tuple[int, int], List[Dict]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            dt = _parse_date(entry)
            grouped.setdefault((dt.year, dt.month), []).append(entry)

        if not grouped:
            empty = QTreeWidgetItem(self.history_table)
            empty.setText(0, "אין נתוני היסטוריה זמינים")
            empty_font = empty.font(0)
            empty_font.setBold(True)
            empty.setFont(0, empty_font)
            self.history_table.setUpdatesEnabled(True)
            self.history_table.setSortingEnabled(was_sorting)
            return

        for key in sorted(grouped.keys(), reverse=True):
            year, month = key
            month_entries = grouped[key]
            month_name = months_he[month - 1] if 1 <= month <= 12 else str(month)

            month_ai = sum(_as_float(e.get("costs", {}).get("openai_cost_usd", 0.0)) for e in month_entries)
            month_azure_tts = sum(_as_float(e.get("costs", {}).get("azure_tts_cost_usd", e.get("costs", {}).get("tts_cost_usd", 0.0))) for e in month_entries)
            month_eleven = sum(_as_float(e.get("costs", {}).get("elevenlabs_tts_cost_usd", 0.0)) for e in month_entries)
            month_tts = month_azure_tts + month_eleven
            month_total = sum(_as_float(e.get("costs", {}).get("total_cost_usd", _as_float(e.get("costs", {}).get("openai_cost_usd", 0.0)) + _as_float(e.get("costs", {}).get("azure_tts_cost_usd", 0.0)) + _as_float(e.get("costs", {}).get("elevenlabs_tts_cost_usd", 0.0)))) for e in month_entries)

            parent = QTreeWidgetItem(self.history_table)
            parent.setText(0, f"{month_name} {year}")
            parent.setText(1, "סה\"כ חודשי")
            parent.setText(5, f"${month_total:.3f}")
            font_parent = parent.font(0)
            font_parent.setBold(True)
            parent.setFont(0, font_parent)
            parent.setFont(1, font_parent)
            parent.setFont(5, font_parent)
            parent.setForeground(5, QColor("#22c55e"))
            parent.setExpanded(True)

            # Sort entries newest first
            month_entries_sorted = sorted(month_entries, key=_parse_date, reverse=True)
            for entry in month_entries_sorted:
                date_obj = _parse_date(entry)
                date_str = date_obj.strftime("%Y-%m-%d")
                topic = entry.get("topic", "לא ידוע")[:30]
                costs = entry.get("costs") or {}
                if not isinstance(costs, dict):
                    costs = {}

                ai_cost = _as_float(costs.get("openai_cost_usd", 0))
                azure_tts = _as_float(costs.get("azure_tts_cost_usd", costs.get("tts_cost_usd", 0)))
                elevenlabs_tts = _as_float(costs.get("elevenlabs_tts_cost_usd", 0))
                tts_cost = azure_tts + elevenlabs_tts
                total = _as_float(costs.get("total_cost_usd", ai_cost + tts_cost))

                tts_provider = "Azure"
                if elevenlabs_tts > 0:
                    tts_provider = "ElevenLabs" if azure_tts == 0 else "Mixed"

                child = QTreeWidgetItem(parent)
                child.setText(0, date_str)
                child.setText(1, topic)
                child.setText(2, f'${ai_cost:.3f}')
                child.setText(3, f'${tts_cost:.3f}')
                child.setText(4, tts_provider)
                child.setText(5, f'${total:.3f}')
                child.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
                child.setTextAlignment(3, Qt.AlignmentFlag.AlignCenter)
                child.setTextAlignment(5, Qt.AlignmentFlag.AlignCenter)

            # Monthly total row
            total_row = QTreeWidgetItem(parent)
            total_row.setText(0, "סה\"כ חודשי")
            total_row.setText(2, f'${month_ai:.3f}')
            total_row.setText(3, f'${month_tts:.3f}')
            total_row.setText(5, f'${month_total:.3f}')
            total_row.setForeground(2, QColor("#38bdf8"))
            total_row.setForeground(3, QColor("#a78bfa"))
            total_row.setForeground(5, QColor("#22c55e"))
        self.history_table.setUpdatesEnabled(True)
        self.history_table.setSortingEnabled(was_sorting)

    def _refresh_chart(self) -> None:
        """Refresh the analytics chart."""
        self.chart.clear()
        metric = self.metric_combo.currentData()
        x_vals, y_vals, labels = self._build_cost_series(metric)
        
        if not x_vals:
            self.chart_placeholder.setVisible(True)
            self.chart.setVisible(False)
            return
        
        self.chart_placeholder.setVisible(False)
        self.chart.setVisible(True)
        
        # Choose color based on metric
        colors = {
            "cost": ("#60a5fa", "#1d4ed855"),
            "tts": ("#a78bfa", "#7c3aed55"),
            "tts_cost": ("#f472b6", "#db277755"),
        }
        pen_color, fill_color = colors.get(metric, colors["cost"])
        
        pen = {"color": pen_color, "width": 2}
        try:
            curve = self.chart.plot(
                x_vals, y_vals,
                pen=pen,
                symbol="o",
                symbolBrush=pen_color,
                symbolPen=pen_color,
            )
            curve.setFillLevel(0)
            curve.setBrush(fill_color)
            
            axis = self.chart.getAxis("bottom")
            max_labels = 10
            step = max(1, len(labels) // max_labels)
            ticks = [(i, labels[i]) for i in range(0, len(labels), step)]
            if ticks and ticks[-1][0] != len(labels) - 1:
                ticks.append((len(labels) - 1, labels[-1]))
            axis.setTicks([ticks])
        except Exception:
            # Fall back to placeholder if plotting fails due to bad data
            self.chart.clear()
            self.chart_placeholder.setVisible(True)
            self.chart.setVisible(False)
            return
        
        # Update summary
        entries_raw = self.history.all() or []
        entries = entries_raw[:30] if isinstance(entries_raw, list) else []
        summary = self._summarize_costs(entries)
        self.avg_label.setText(f"${summary['avg']:.2f} ממוצע")
        self.max_label.setText(f"${summary['max']:.2f} שיא")
        self.tts_label.setText(f"{summary['tts_total']:,} תווי TTS")

    def _build_cost_series(
        self,
        metric: str,
    ) -> Tuple[List[int], List[float], List[str]]:
        """Build time series data for the chart."""
        entries_raw = self.history.all() or []
        if not isinstance(entries_raw, list):
            return [], [], []

        def _parse_dt(entry: Dict) -> datetime:
            date_str = entry.get("timestamp") or entry.get("date") or ""
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                try:
                    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    return datetime.now(timezone.utc)

        dated_entries: List[Tuple[datetime, Dict]] = [(_parse_dt(e), e) for e in entries_raw if isinstance(e, dict)]
        dated_entries.sort(key=lambda pair: pair[0])  # chronological
        dated_entries = dated_entries[-30:]  # keep last 30 chronologically
        if not dated_entries:
            return [], [], []

        x_vals: List[int] = []
        y_vals: List[float] = []
        labels: List[str] = []
        
        def _as_float(val: object) -> float:
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0

        def _as_int(val: object) -> int:
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0

        for idx, (date_obj, entry) in enumerate(dated_entries):
            costs = entry.get("costs") or {}
            if not isinstance(costs, dict):
                costs = {}
            if metric == "tts":
                # Total TTS characters in thousands
                azure = _as_int(costs.get("tts_characters", 0))
                elevenlabs = _as_int(costs.get("elevenlabs_characters", 0))
                value = (azure + elevenlabs) / 1000
            elif metric == "tts_cost":
                # TTS cost only
                azure_cost = _as_float(costs.get("azure_tts_cost_usd", costs.get("tts_cost_usd", 0)))
                elevenlabs_cost = _as_float(costs.get("elevenlabs_tts_cost_usd", 0))
                value = azure_cost + elevenlabs_cost
            else:
                value = _as_float(costs.get("total_cost_usd", 0.0))
            
            pos = len(x_vals)
            x_vals.append(pos)
            y_vals.append(value)
            labels.append(date_obj.strftime("%y-%m-%d"))
        
        return x_vals, y_vals, labels

    def _summarize_costs(self, entries: List[Dict]) -> Dict[str, float]:
        """Summarize cost statistics from entries."""
        if not entries:
            return {"avg": 0.0, "max": 0.0, "tts_total": 0}
        
        def _as_float(val: object) -> float:
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0

        def _as_int(val: object) -> int:
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0

        costs = []
        tts_chars: List[int] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            costs_dict = entry.get("costs") or {}
            if not isinstance(costs_dict, dict):
                costs_dict = {}
            costs.append(_as_float(costs_dict.get("total_cost_usd", 0.0)))
            tts_chars.append(
                _as_int(costs_dict.get("tts_characters", 0)) +
                _as_int(costs_dict.get("elevenlabs_characters", 0))
            )
        tts_total = sum(
            tts_chars
        )
        avg_cost = sum(costs) / len(costs) if costs else 0.0
        
        return {
            "avg": avg_cost,
            "max": max(costs) if costs else 0.0,
            "tts_total": tts_total,
        }

    def _save_limits(self) -> None:
        """Save the updated budget limits."""
        self.settings.monthly_openai_cost_limit = float(self.openai_spin.value())
        self.settings.monthly_tts_character_limit = int(self.tts_spin.value())
        self.settings.monthly_elevenlabs_character_limit = int(self.elevenlabs_spin.value())
        self.settings.reset_day_azure = int(self.reset_day_azure_spin.value())
        self.settings.reset_day_gemini = int(self.reset_day_gemini_spin.value())
        self.settings.reset_day_elevenlabs = int(self.reset_day_elevenlabs_spin.value())
        # Maintain legacy budget_reset_day for backward compatibility
        self.settings.budget_reset_day = self.settings.reset_day_azure

        try:
            self.settings.save_ui_preferences(
                {
                    "budget_reset_day": self.settings.budget_reset_day,
                    "reset_day_azure": self.settings.reset_day_azure,
                    "reset_day_gemini": self.settings.reset_day_gemini,
                    "reset_day_elevenlabs": self.settings.reset_day_elevenlabs,
                    "monthly_openai_cost_limit": self.settings.monthly_openai_cost_limit,
                    "monthly_tts_character_limit": self.settings.monthly_tts_character_limit,
                    "monthly_elevenlabs_character_limit": self.settings.monthly_elevenlabs_character_limit,
                }
            )
            self._settings_store.setValue("cost_center/reset_day_azure", self.settings.reset_day_azure)
            self._settings_store.setValue("cost_center/reset_day_gemini", self.settings.reset_day_gemini)
            self._settings_store.setValue("cost_center/reset_day_elevenlabs", self.settings.reset_day_elevenlabs)
            self._settings_store.sync()
        except Exception:
            # Non-fatal: continue without blocking UI
            pass

        self._refresh_all()
        QMessageBox.information(self, "נשמר", "מגבלות התקציב עודכנו בהצלחה.")

    def _run_calculator(self) -> None:
        """Run the cost calculator and display results."""
        try:
            text, _ = self._calculate_cost_estimate(
                self.words_input.value(),
                self.minutes_input.value(),
                self.provider_combo.currentData(),
                self.tts_provider_combo.currentData(),
                self.visual_gen_combo.currentData(),
                self.visual_count_input.value(),
                self.audio_cb.isChecked(),
                self.video_cb.isChecked(),
                self.ppt_cb.isChecked(),
            )
        except ValueError as exc:
            QMessageBox.information(self, "נתונים חסרים", str(exc))
            return
        except Exception as exc:  # Defensive: avoid calculator crashes
            QMessageBox.warning(self, "חישוב נכשל", f"שגיאה בעת חישוב העלות: {exc}")
            return
        self.calc_result.setText(text)

    def _calculate_cost_estimate(
        self,
        words: int,
        minutes: float,
        ai_provider: str,
        tts_provider: str,
        visual_gen: str,
        visual_count: int,
        include_audio: bool,
        include_video: bool,
        include_ppt: bool,
    ) -> Tuple[str, float]:
        """
        Calculate estimated costs for a pipeline run.
        
        Args:
            words: Number of words in transcript
            minutes: Duration in minutes
            ai_provider: AI provider ('azure' or 'gemini')
            tts_provider: TTS provider ('azure' or 'elevenlabs')
            visual_gen: Visual generator ('manim', 'imagen', 'veo', 'hybrid')
            visual_count: Number of images or seconds of video
            include_audio: Include TTS cost
            include_video: Include video rendering cost
            include_ppt: Include PPTX generation cost
            
        Returns:
            Tuple of (breakdown_text, total_cost)
        """
        words = int(max(0, words))
        minutes = float(max(0.0, minutes))

        if words <= 0 and minutes > 0:
            words = int(minutes * PRICING.words_per_minute)
        if words <= 0:
            raise ValueError("הזינו מספר מילים או דקות.")
        
        tokens = words * PRICING.tokens_per_word
        output_tokens = tokens * PRICING.output_token_ratio
        
        provider = "gemini" if ai_provider == "gemini" else "azure"
        gpt_cost_val = gpt_cost(tokens, output_tokens, provider)  # type: ignore[arg-type]
        model_name = "Gemini Flash" if provider == "gemini" else "Azure GPT-4o"
        
        total = gpt_cost_val
        breakdown = [f"מודל {model_name}: ${gpt_cost_val:.3f}"]
        
        if include_audio:
            tts_chars = words * PRICING.chars_per_word
            tts_cost_val, tts_rate = tts_cost(tts_chars, tts_provider if tts_provider in {"azure", "elevenlabs"} else "azure")  # type: ignore[arg-type]
            tts_name = "ElevenLabs" if tts_provider == "elevenlabs" else "Azure Neural"
            total += tts_cost_val
            breakdown.append(
                f"TTS ({tts_name}): ${tts_cost_val:.3f} "
                f"({int(tts_chars):,} תווים @ ${tts_rate:.3f}/1K)"
            )
        
        # Google AI Visuals cost
        if visual_gen in {"imagen", "veo", "hybrid"} and visual_count > 0:
            visual_cost_val = visual_cost(visual_gen, visual_count)  # type: ignore[arg-type]
            total += visual_cost_val
            if visual_gen == "imagen":
                breakdown.append(f"Imagen 4 ({visual_count} תמונות): ${visual_cost_val:.2f}")
            elif visual_gen == "veo":
                breakdown.append(f"VEO ({visual_count} שניות): ${visual_cost_val:.2f}")
            else:
                imagen_count = visual_count // 2
                veo_secs = visual_count - imagen_count
                breakdown.append(
                    f"היברידי ({imagen_count} תמונות + {veo_secs} שניות): ${visual_cost_val:.2f}"
                )
        
        if include_video:
            total += PRICING.video_overhead
            breakdown.append(f"וידאו / רינדור: ${PRICING.video_overhead:.2f}")
        
        if include_ppt:
            total += PRICING.ppt_extra
            breakdown.append(f"PPTX/Story: ${PRICING.ppt_extra:.2f}")
        
        breakdown.append("")
        breakdown.append(f"סה\"כ משוער: ${total:.3f}")
        
        # Add comparison if using ElevenLabs
        if include_audio and tts_provider == "elevenlabs":
            diff = azure_vs_elevenlabs_diff(words * PRICING.chars_per_word)
            breakdown.append(f"(הפרש מ-Azure TTS: {diff:+.3f}$)")
        
        # Add warning for expensive VEO
        if visual_gen == "veo" and visual_count > 0:
            breakdown.append("⚠️ VEO יקר מאוד! שקול להשתמש ב-Manim או Imagen")
        
        return "\n".join(breakdown), total

    def closeEvent(self, event) -> None:
        """Persist cost center UI state on close."""
        try:
            # Ensure reset days are flushed even if the save button was not used
            self._persist_reset_days()

            geometry_bytes = self.saveGeometry()
            geo_hex = bytes(geometry_bytes.toHex()).decode("ascii")
            scroll_val = 0
            tab_idx = self.tabs.currentIndex() if getattr(self, "tabs", None) else 0
            self.settings.save_ui_preferences(
                {
                    "cost_center_geometry": geo_hex,
                    "cost_center_tab_index": int(tab_idx),
                    "cost_center_scroll_pos": int(scroll_val),
                }
            )

            self._settings_store.setValue("cost_center/geometry", geometry_bytes)
            self._settings_store.setValue("cost_center/tab_index", int(tab_idx))
            self._settings_store.setValue("cost_center/scroll_pos", int(scroll_val))
            self._settings_store.sync()
        except Exception as exc:
            print(f"[CostCenterDialog] Failed to save UI state: {exc}")
        super().closeEvent(event)

    def moveEvent(self, event) -> None:
        """Persist geometry when the window is moved."""
        self._save_geometry_qsettings()
        super().moveEvent(event)

    def resizeEvent(self, event) -> None:
        """Persist geometry when the window is resized."""
        self._save_geometry_qsettings()
        super().resizeEvent(event)
