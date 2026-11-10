"""
M.B.S Studio - ElevenLabs Voice Selector Dialog
================================================

Dialog for browsing and selecting ElevenLabs voices
with preview capability and voice configuration.

Author: M.B.S Studio
Version: 1.1.0 (Enhanced settings persistence)
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.utils import Settings

_logger = logging.getLogger(__name__)


# Pre-defined ElevenLabs voices with explicit Hebrew support flags
ELEVENLABS_PRESET_VOICES = [
    {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "gender": "female", "description": "קול נשי חם ואמין", "hebrew_support": True},
    {"voice_id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi", "gender": "female", "description": "קול נשי צעיר ואנרגטי", "hebrew_support": True},
    {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella", "gender": "female", "description": "קול נשי רך ונעים", "hebrew_support": True},
    {"voice_id": "ErXwobaYiN019PkySvjV", "name": "Antoni", "gender": "male", "description": "קול גברי צעיר וידידותי", "hebrew_support": True},
    {"voice_id": "MF3mGyEYCI7XYWbV9V60", "name": "Elli", "gender": "female", "description": "קול נשי צעיר וחכם", "hebrew_support": False},
    {"voice_id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh", "gender": "male", "description": "קול גברי עמוק וסמכותי", "hebrew_support": False},
    {"voice_id": "VR6AewLTigWG4xSOukaG", "name": "Arnold", "gender": "male", "description": "קול גברי חזק ודרמטי", "hebrew_support": False},
    {"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Adam", "gender": "male", "description": "קול גברי עמוק וטבעי", "hebrew_support": True},
    {"voice_id": "yoZ06aMxZJJ28mfd3POQ", "name": "Sam", "gender": "male", "description": "קול גברי צעיר ונמרץ", "hebrew_support": False},
    {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel", "gender": "male", "description": "קול גברי בריטי מעודן", "hebrew_support": False},
    {"voice_id": "XB0fDUnXU5powFXDhCwa", "name": "Charlotte", "gender": "female", "description": "קול נשי בריטי אלגנטי", "hebrew_support": False},
    {"voice_id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Alice", "gender": "female", "description": "קול נשי ברור ומקצועי", "hebrew_support": False},
    {"voice_id": "iP95p4xoKVk53GoZ742B", "name": "Chris", "gender": "male", "description": "קול גברי אמריקאי סטנדרטי", "hebrew_support": False},
    {"voice_id": "oWAxZDx7w5VEj9dCyTzz", "name": "Grace", "gender": "female", "description": "קול נשי דרומי חם", "hebrew_support": False},
    {"voice_id": "pqHfZKP75CvOlQylNhV4", "name": "Bill", "gender": "male", "description": "קול גברי בוגר ומנוסה", "hebrew_support": False},
    {"voice_id": "nPczCjzI2devNBz1zQrb", "name": "Brian", "gender": "male", "description": "קול גברי אירי קליל", "hebrew_support": False},
    {"voice_id": "N2lVS1w4EtoT3dr4eOWO", "name": "Callum", "gender": "male", "description": "קול גברי סקוטי חזק", "hebrew_support": False},
    {"voice_id": "IKne3meq5aSn9XLyUdCD", "name": "Charlie", "gender": "male", "description": "קול גברי אוסטרלי ידידותי", "hebrew_support": False},
    {"voice_id": "XrExE9yKIg1WjnnlVkGX", "name": "Matilda", "gender": "female", "description": "קול נשי אוסטרלי נעים", "hebrew_support": False},
    {"voice_id": "bIHbv24MWmeRgasZH58o", "name": "Will", "gender": "male", "description": "קול גברי ידידותי ונגיש", "hebrew_support": False},
    {"voice_id": "cgSgspJ2msm6clMCkdW9", "name": "Jessica", "gender": "female", "description": "קול נשי אמריקאי צעיר", "hebrew_support": False},
    {"voice_id": "cjVigY5qzO86Huf0OWal", "name": "Eric", "gender": "male", "description": "קול גברי מקצועי וברור", "hebrew_support": False},
    {"voice_id": "FGY2WhTYpPnrIDTdsKH5", "name": "Laura", "gender": "female", "description": "קול נשי אמריקאי חם", "hebrew_support": False},
    {"voice_id": "IgLzaUC3E3Pa20BxNP7O", "name": "George", "gender": "male", "description": "קול גברי בריטי קלאסי", "hebrew_support": False},
    {"voice_id": "JBFqnCBsd6RMkjVDRZzb", "name": "Emily", "gender": "female", "description": "קול נשי בריטי עדין", "hebrew_support": False},
    {"voice_id": "SOYHLrjzK2X1ezoPC6cr", "name": "Harry", "gender": "male", "description": "קול גברי בריטי צעיר", "hebrew_support": False},
    {"voice_id": "TX3LPaxmHKxFdv7VOQHJ", "name": "Liam", "gender": "male", "description": "קול גברי אירי מודרני", "hebrew_support": False},
    {"voice_id": "ThT5KcBeYPX3keUQqHPh", "name": "Dorothy", "gender": "female", "description": "קול נשי בריטי מבוגר", "hebrew_support": False},
    {"voice_id": "g5CIjZEefAph4nQFvHAz", "name": "Ethan", "gender": "male", "description": "קול גברי אמריקאי צעיר", "hebrew_support": False},
    {"voice_id": "jsCqWAovK2LkecY7zXl4", "name": "Freya", "gender": "female", "description": "קול נשי סקנדינבי רך", "hebrew_support": False},
    {"voice_id": "t0jbNlBVZ17f02VDIeMI", "name": "Gigi", "gender": "female", "description": "קול נשי צעיר וחי", "hebrew_support": False},
    {"voice_id": "piTKgcLEGmPE4e6mEKli", "name": "Nicole", "gender": "female", "description": "קול נשי אמריקאי מקצועי", "hebrew_support": False},
]


class VoiceFetchWorker(QThread):
    """Background worker to fetch voices from ElevenLabs API."""
    
    result = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self.api_key = api_key
    
    def run(self):
        try:
            from elevenlabs.client import ElevenLabs
            client = ElevenLabs(api_key=self.api_key)
            voices = client.voices.get_all()
            voice_list = [
                {
                    "voice_id": v.voice_id,
                    "name": v.name,
                    "category": getattr(v, 'category', 'unknown'),
                    "labels": getattr(v, 'labels', {}),
                }
                for v in voices.voices
            ]
            self.result.emit(voice_list)
        except Exception as exc:
            self.error.emit(str(exc))


class ElevenLabsVoiceSelectorDialog(QDialog):
    """
    Dialog for selecting ElevenLabs voices for Roee and Noa.
    
    Features:
        - Browse preset multilingual voices
        - Fetch custom voices from account
        - Filter by gender
        - Search by name
        - Configure voice for each speaker
    """
    
    def __init__(
        self,
        settings: Settings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.selected_voices: Dict[str, str] = {}
        self.all_voices: List[Dict] = ELEVENLABS_PRESET_VOICES.copy()
        self.fetch_worker: Optional[VoiceFetchWorker] = None
        
        _logger.info("[ElevenLabsVoiceSelectorDialog] Initializing voice selector")
        
        self._setup_window()
        self._setup_ui()
        self._populate_table()
        self._load_saved_selections()  # Load previously saved voice selections
    
    def _setup_window(self) -> None:
        """Configure window properties."""
        self.setWindowTitle("בחירת קולות ElevenLabs")
        
        # Restore saved geometry if available
        try:
            saved_geo = getattr(self.settings, 'voice_selector_geometry', None)
            if saved_geo:
                self.restoreGeometry(bytes.fromhex(saved_geo))
            else:
                self.resize(800, 600)
        except Exception:
            self.resize(800, 600)
            
        self.setMinimumSize(600, 400)
        self.setStyleSheet("""
            QDialog {
                background-color: #0f172a;
                color: #e2e8f0;
            }
            QTableWidget {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                gridline-color: #334155;
            }
            QTableWidget::item {
                padding: 8px;
            }
            QTableWidget::item:selected {
                background-color: #3b82f6;
            }
            QHeaderView::section {
                background-color: #334155;
                color: #94a3b8;
                padding: 10px;
                border: none;
                font-weight: 600;
            }
            QLineEdit {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px;
                color: #e2e8f0;
            }
            QComboBox {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px;
                color: #e2e8f0;
            }
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:disabled {
                background-color: #475569;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #334155;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #22c55e;
            }
        """)
    
    def _setup_ui(self) -> None:
        """Build the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        # Title
        title = QLabel("בחירת קולות ElevenLabs")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #22c55e;")
        layout.addWidget(title)
        
        # Controls row
        controls = QHBoxLayout()
        
        # Search
        controls.addWidget(QLabel("חיפוש:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("הקלד שם קול...")
        self.search_edit.textChanged.connect(self._filter_table)
        controls.addWidget(self.search_edit)
        
        # Gender filter
        controls.addWidget(QLabel("מין:"))
        self.gender_combo = QComboBox()
        self.gender_combo.addItem("הכל", "all")
        self.gender_combo.addItem("גברי", "male")
        self.gender_combo.addItem("נשי", "female")
        self.gender_combo.currentIndexChanged.connect(self._filter_table)
        controls.addWidget(self.gender_combo)
        
        # Fetch from account button
        self.fetch_btn = QPushButton("טען קולות מהחשבון")
        self.fetch_btn.clicked.connect(self._fetch_account_voices)
        controls.addWidget(self.fetch_btn)
        
        controls.addStretch(1)
        layout.addLayout(controls)
        
        # Voice table
        self.voice_table = QTableWidget()
        self.voice_table.setColumnCount(5)  # Added Language column
        self.voice_table.setHorizontalHeaderLabels(["שם", "שפה", "מין", "תיאור", "Voice ID"])
        self.voice_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.voice_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.voice_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.voice_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.voice_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.voice_table.verticalHeader().setVisible(False)
        self.voice_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.voice_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.voice_table.setAlternatingRowColors(True)
        layout.addWidget(self.voice_table, 1)
        
        # Speaker selection
        speaker_group = QGroupBox("הקצאת קולות לדוברים")
        speaker_layout = QHBoxLayout(speaker_group)
        
        # Roee voice
        roee_layout = QVBoxLayout()
        roee_layout.addWidget(QLabel("קול ל-Roee (גברי):"))
        self.roee_combo = QComboBox()
        roee_layout.addWidget(self.roee_combo)
        self.set_roee_btn = QPushButton("הגדר מהטבלה")
        self.set_roee_btn.clicked.connect(lambda: self._set_speaker_voice("Roee"))
        roee_layout.addWidget(self.set_roee_btn)
        speaker_layout.addLayout(roee_layout)
        
        # Noa voice
        noa_layout = QVBoxLayout()
        noa_layout.addWidget(QLabel("קול ל-Noa (נשי):"))
        self.noa_combo = QComboBox()
        noa_layout.addWidget(self.noa_combo)
        self.set_noa_btn = QPushButton("הגדר מהטבלה")
        self.set_noa_btn.clicked.connect(lambda: self._set_speaker_voice("Noa"))
        noa_layout.addWidget(self.set_noa_btn)
        speaker_layout.addLayout(noa_layout)
        
        layout.addWidget(speaker_group)
        
        # Action buttons
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        
        cancel_btn = QPushButton("ביטול")
        cancel_btn.setStyleSheet("background-color: #475569;")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        
        save_btn = QPushButton("שמור הגדרות")
        save_btn.setStyleSheet("background-color: #22c55e;")
        save_btn.clicked.connect(self._save_and_close)
        buttons.addWidget(save_btn)
        
        layout.addLayout(buttons)
    
    def _populate_table(self) -> None:
        """Fill the voice table with available voices."""
        self._fill_speaker_combos()
        self._filter_table()
    
    def _fill_speaker_combos(self) -> None:
        """Fill the speaker combo boxes."""
        self.roee_combo.clear()
        self.noa_combo.clear()
        
        for voice in self.all_voices:
            display = f"{voice['name']} ({voice.get('description', '')})"
            self.roee_combo.addItem(display, voice['voice_id'])
            self.noa_combo.addItem(display, voice['voice_id'])
        
        # Set defaults
        for i in range(self.roee_combo.count()):
            if self.roee_combo.itemData(i) == "pNInz6obpgDQGcFmaJgB":  # Adam
                self.roee_combo.setCurrentIndex(i)
                break
        
        for i in range(self.noa_combo.count()):
            if self.noa_combo.itemData(i) == "21m00Tcm4TlvDq8ikWAM":  # Rachel
                self.noa_combo.setCurrentIndex(i)
                break

    def _load_saved_selections(self) -> None:
        """Load previously saved voice selections from settings."""
        saved_overrides = getattr(self.settings, 'elevenlabs_voice_overrides', {})
        if not saved_overrides:
            _logger.debug("[ElevenLabsVoiceSelectorDialog] No saved voice overrides found")
            return
        
        _logger.info("[ElevenLabsVoiceSelectorDialog] Loading saved voice selections: %s", 
                    list(saved_overrides.keys()))
        
        # Load Roee voice
        if "Roee" in saved_overrides:
            roee_voice_id = saved_overrides["Roee"].get("voice_id")
            if roee_voice_id:
                for i in range(self.roee_combo.count()):
                    if self.roee_combo.itemData(i) == roee_voice_id:
                        self.roee_combo.setCurrentIndex(i)
                        _logger.debug("[ElevenLabsVoiceSelectorDialog] Restored Roee voice: %s", roee_voice_id)
                        break
        
        # Load Noa voice
        if "Noa" in saved_overrides:
            noa_voice_id = saved_overrides["Noa"].get("voice_id")
            if noa_voice_id:
                for i in range(self.noa_combo.count()):
                    if self.noa_combo.itemData(i) == noa_voice_id:
                        self.noa_combo.setCurrentIndex(i)
                        _logger.debug("[ElevenLabsVoiceSelectorDialog] Restored Noa voice: %s", noa_voice_id)
                        break
    
    def _filter_table(self) -> None:
        """Filter the voice table based on search and gender."""
        search_text = self.search_edit.text().lower()
        gender_filter = self.gender_combo.currentData()
        
        filtered = []
        for voice in self.all_voices:
            # Gender filter
            if gender_filter != "all":
                if voice.get("gender", "").lower() != gender_filter:
                    continue
            
            # Search filter
            if search_text:
                name = voice.get("name", "").lower()
                desc = voice.get("description", "").lower()
                if search_text not in name and search_text not in desc:
                    continue
            
            filtered.append(voice)
        
        # Update table
        self.voice_table.setRowCount(len(filtered))
        for row, voice in enumerate(filtered):
            self.voice_table.setItem(row, 0, QTableWidgetItem(voice.get("name", "")))
            
            # Determine language support from explicit flag
            hebrew_support = voice.get("hebrew_support", False)
            lang_display = "🇮🇱 עברית/English" if hebrew_support else "🇺🇸 English only"

            self.voice_table.setItem(row, 1, QTableWidgetItem(lang_display))
            
            gender_display = "גברי" if voice.get("gender") == "male" else "נשי"
            self.voice_table.setItem(row, 2, QTableWidgetItem(gender_display))
            
            self.voice_table.setItem(row, 3, QTableWidgetItem(voice.get("description", "")))
            self.voice_table.setItem(row, 4, QTableWidgetItem(voice.get("voice_id", "")))
    
    def _set_speaker_voice(self, speaker: str) -> None:
        """Set a speaker's voice from the selected table row."""
        selected = self.voice_table.selectedItems()
        if not selected:
            QMessageBox.information(self, "בחירה", "בחר קול מהטבלה תחילה.")
            return
        
        row = selected[0].row()
        voice_id = self.voice_table.item(row, 4).text()  # Updated index for Voice ID
        voice_name = self.voice_table.item(row, 0).text()
        
        # Warn if the selected voice does not support Hebrew
        voice_data = next((v for v in self.all_voices if v.get("voice_id") == voice_id), {})
        if not voice_data.get("hebrew_support", False):
            reply = QMessageBox.warning(
                self,
                "אזהרה - קול לא תומך בעברית",
                (
                    f"הקול '{voice_name}' אינו תומך בהגייה טבעית בעברית.\n"
                    "המשך שימוש עלול לגרום לאיכות קול ירודה או הגייה שגויה.\n\n"
                    "להמשיך בכל זאת?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        combo = self.roee_combo if speaker == "Roee" else self.noa_combo
        for i in range(combo.count()):
            if combo.itemData(i) == voice_id:
                combo.setCurrentIndex(i)
                break
        
        QMessageBox.information(
            self, 
            "הוגדר", 
            f"הקול '{voice_name}' הוגדר ל-{speaker}."
        )
    
    def _fetch_account_voices(self) -> None:
        """Fetch custom voices from the user's ElevenLabs account."""
        if not self.settings.elevenlabs_api_key:
            QMessageBox.warning(
                self,
                "מפתח חסר",
                "לא הוגדר מפתח ElevenLabs.\nהגדר ELEVENLABS_API_KEY בקובץ .env"
            )
            return
        
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("טוען...")
        
        self.fetch_worker = VoiceFetchWorker(self.settings.elevenlabs_api_key)
        self.fetch_worker.result.connect(self._handle_voices_fetched)
        self.fetch_worker.error.connect(self._handle_fetch_error)
        self.fetch_worker.start()
    
    def _handle_voices_fetched(self, voices: List[Dict]) -> None:
        """Handle fetched voices from API."""
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("טען קולות מהחשבון")
        
        # Merge with presets, avoid duplicates
        existing_ids = {v["voice_id"] for v in self.all_voices}
        for voice in voices:
            if voice["voice_id"] not in existing_ids:
                # Add gender from labels if available
                labels = voice.get("labels", {})
                gender = labels.get("gender", "unknown")
                voice["gender"] = gender
                voice["description"] = f"קול מותאם אישית - {voice.get('category', 'custom')}"
                self.all_voices.append(voice)
        
        self._fill_speaker_combos()
        self._filter_table()
        
        QMessageBox.information(
            self,
            "הצלחה",
            f"נטענו {len(voices)} קולות מהחשבון."
        )
    
    def _handle_fetch_error(self, error: str) -> None:
        """Handle fetch error."""
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("טען קולות מהחשבון")
        
        QMessageBox.warning(self, "שגיאה", f"שגיאה בטעינת קולות:\n{error}")
    
    def _save_and_close(self) -> None:
        """Save selected voices and close dialog."""
        _logger.info("[ElevenLabsVoiceSelectorDialog] Saving voice selections...")
        
        # Save geometry as hex string (QByteArray -> bytes -> ascii)
        geo: str = ""
        try:
            geo_bytes = self.saveGeometry()
            if geo_bytes and hasattr(geo_bytes, "toHex"):
                geo = bytes(geo_bytes.toHex()).decode("ascii")
                setattr(self.settings, "voice_selector_geometry", geo)
                _logger.debug("[ElevenLabsVoiceSelectorDialog] Saved geometry")
        except Exception as exc:
            _logger.warning("[ElevenLabsVoiceSelectorDialog] Failed to save geometry: %s", exc)

        self.selected_voices = {
            "Roee": {
                "voice_id": self.roee_combo.currentData(),
                "model": "eleven_v3",
            },
            "Noa": {
                "voice_id": self.noa_combo.currentData(),
                "model": "eleven_v3",
            },
        }
        
        _logger.info("[ElevenLabsVoiceSelectorDialog] Selected voices: Roee=%s, Noa=%s",
                    self.selected_voices["Roee"]["voice_id"][:8] if self.selected_voices["Roee"]["voice_id"] else "None",
                    self.selected_voices["Noa"]["voice_id"][:8] if self.selected_voices["Noa"]["voice_id"] else "None")
        
        try:
            # Save both geometry and voice overrides together
            save_payload = {
                "elevenlabs_voice_overrides": self.selected_voices,
                "voice_selector_geometry": geo,
            }
            self.settings.save_ui_preferences(save_payload)
            _logger.info("[ElevenLabsVoiceSelectorDialog] Successfully saved voice preferences to disk")
            
            # Also update in-memory settings
            self.settings.elevenlabs_voice_overrides = self.selected_voices
            
        except Exception as exc:
            _logger.error("[ElevenLabsVoiceSelectorDialog] Failed to save voice preferences: %s", exc)
            QMessageBox.warning(
                self,
                "שגיאה",
                f"לא ניתן לשמור את בחירת הקולות:\n{exc}",
            )
        self.accept()
    
    def get_selected_voices(self) -> Dict[str, Dict]:
        """Return the selected voice configuration."""
        return self.selected_voices

