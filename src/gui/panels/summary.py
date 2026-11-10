"""
M.B.S Studio - Summary Panel Builder
======================================

Builds the right sidebar panel containing input controls,
metadata editor, story preview, gallery, and run status.

Author: M.B.S Studio
Version: 1.0.0
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..app import PodcastGeneratorWindow


def build_summary_panel(
    parent: "PodcastGeneratorWindow",
    section_label: Callable[[str], QLabel],
    helper_label: Callable[[str], QLabel],
    card_widget: Callable[[], QFrame],
    inline_row: Callable[[str, QWidget, QPushButton], QHBoxLayout],
) -> QWidget:
    """
    Build the summary panel (right sidebar).
    
    Contains:
        - Input file/directory selection
        - Output mode selection
        - Voice profile selection
        - Run preferences toggles
        - Metadata editor with save/revert
        - Story preview
        - Output gallery
        - Run status with progress
        - Action buttons
    
    Args:
        parent: Main window reference
        section_label: Factory for section labels
        helper_label: Factory for helper labels
        card_widget: Factory for card widgets
        inline_row: Factory for inline row layouts
        
    Returns:
        QWidget containing the summary panel
    """
    panel = QWidget()
    panel.setMinimumWidth(300)
    panel.setMaximumWidth(360)
    layout = QVBoxLayout(panel)
    
    # Inputs card
    inputs_card = _build_inputs_card(
        parent, section_label, helper_label, card_widget, inline_row
    )
    layout.addWidget(inputs_card)
    
    # Metadata card
    meta_card = _build_metadata_card(
        parent, section_label, helper_label, card_widget
    )
    layout.addWidget(meta_card, 3)
    
    # Story card
    story_card = _build_story_card(
        parent, section_label, helper_label, card_widget
    )
    layout.addWidget(story_card, 2)
    
    # Gallery card
    gallery_card = _build_gallery_card(
        parent, section_label, helper_label, card_widget
    )
    layout.addWidget(gallery_card, 2)
    
    # Status card
    status_card = _build_status_card(parent, section_label, card_widget)
    layout.addWidget(status_card)
    
    # Cost calculator hint
    layout.addWidget(helper_label("מחשבון העלויות הועבר לדיאלוג 'מרכז עלויות'."))
    
    # Action buttons
    layout.addLayout(_build_action_buttons(parent))
    
    # Connect signals
    parent.transcript_edit.textChanged.connect(parent._handle_transcript_changed)
    parent.output_dir_edit.textChanged.connect(parent._update_onboarding_tip)
    parent._update_gallery_buttons()
    
    return panel


def _build_inputs_card(
    parent: "PodcastGeneratorWindow",
    section_label: Callable[[str], QLabel],
    helper_label: Callable[[str], QLabel],
    card_widget: Callable[[], QFrame],
    inline_row: Callable[[str, QWidget, QPushButton], QHBoxLayout],
) -> QFrame:
    """Build the inputs selection card."""
    inputs_card = card_widget()
    inputs_layout = QVBoxLayout(inputs_card)
    
    # Transcript selection
    parent.transcript_edit = QLineEdit()
    parent.transcript_edit.setPlaceholderText("בחר קובץ .txt או .srt")
    parent.transcript_edit.setToolTip(
        "קובץ התמלול העיקרי של ההרצאה – מכאן נבנה הדיאלוג."
    )
    
    transcript_btn = QPushButton("בחר תמלול")
    transcript_btn.setToolTip("פתח בוחר קבצים ותאתר תמלול קיים במחשב.")
    transcript_btn.clicked.connect(
        lambda: parent._pick_file(parent.transcript_edit)
    )
    
    inputs_layout.addLayout(
        inline_row("תמלול", parent.transcript_edit, transcript_btn)
    )
    inputs_layout.addWidget(
        helper_label("קובץ טקסט/SRT בעברית מתוך ההרצאה.")
    )
    
    # Output directory selection
    parent.output_dir_edit = QLineEdit(str(parent.settings.output_base_dir))
    parent.output_dir_edit.setToolTip(
        "תיקיית יעדי פלט – ניתן לבחור תיקייה אחרת לכל פרויקט."
    )
    
    output_btn = QPushButton("בחר תיקייה")
    output_btn.setToolTip("שמור את כל תוצרי ההרצה בתיקייה שתבחר.")
    output_btn.clicked.connect(
        lambda: parent._pick_directory(parent.output_dir_edit)
    )
    
    inputs_layout.addLayout(
        inline_row("פלט", parent.output_dir_edit, output_btn)
    )
    inputs_layout.addWidget(
        helper_label("תיקיית יעד ליצירת הפרויקט (outputs/<תאריך>_<נושא>).")
    )
    
    # Custom project name (optional)
    parent.custom_project_name = QLineEdit()
    parent.custom_project_name.setPlaceholderText("(אופציונלי) שם פרויקט מותאם אישית")
    parent.custom_project_name.setToolTip(
        "הזן שם פרויקט מותאם אישית. השאר ריק לשם אוטומטי מהנושא."
    )
    inputs_layout.addWidget(QLabel("📁 שם פרויקט:"))
    inputs_layout.addWidget(parent.custom_project_name)
    inputs_layout.addWidget(
        helper_label("שם מותאם לתיקיית הפרויקט. ברירת מחדל: <תאריך>_<נושא>")
    )
    
    # Output mode selection
    inputs_layout.addWidget(section_label("בחירת תוצר"))
    
    mode_row = QHBoxLayout()
    parent.output_mode_combo = QComboBox()
    parent.output_mode_combo.addItem("וידאו + אודיו (Mixed media)", "mixed")
    parent.output_mode_combo.addItem("אודיו בלבד", "audio")
    parent.output_mode_combo.addItem("מצגת / שקפים", "presentation")
    parent.output_mode_combo.currentIndexChanged.connect(
        parent._handle_output_mode_change
    )
    parent.output_mode_combo.setToolTip(
        "בחרו אם הפipeline יבנה וידאו מלא, רק אודיו, או מצגת PPTX."
    )
    mode_row.addWidget(parent.output_mode_combo, 1)
    inputs_layout.addLayout(mode_row)
    inputs_layout.addWidget(
        helper_label("בחרו את התוצר העיקרי (וידאו, אודיו או מצגת) לפני ההרצה.")
    )
    
    # Voice profile selection
    parent.voice_combo = QComboBox()
    for name in (
        parent.voice_manager.profile_names or
        [parent.voice_manager.default_voice_profile]
    ):
        parent.voice_combo.addItem(name)
    parent.voice_combo.setToolTip(
        "פרופיל קובע אילו קולות משויכים לדוברים בעברית/אנגלית.\n"
        "בחירת פרופיל ElevenLabs תחליף אוטומטית את ספק הדיבור."
    )
    parent.voice_combo.currentIndexChanged.connect(parent._handle_voice_profile_change)
    
    voice_btn = QPushButton("קולות")
    voice_btn.setToolTip(
        "פתח את קובץ ההגדרות לעריכת קולות מותאמים אישית."
    )
    voice_btn.clicked.connect(parent._open_voice_profiles)
    
    inputs_layout.addLayout(
        inline_row("פרופיל קולות", parent.voice_combo, voice_btn)
    )
    inputs_layout.addWidget(
        helper_label("הפרופיל קובע אילו קולות ייבחרו לעברית/אנגלית.")
    )
    
    # TTS Provider selection
    inputs_layout.addWidget(section_label("ספק דיבור (TTS)"))
    
    tts_row = QHBoxLayout()
    parent.tts_provider_combo = QComboBox()
    parent.tts_provider_combo.addItem("Azure Neural TTS", "azure")
    parent.tts_provider_combo.addItem("ElevenLabs (טבעי יותר)", "elevenlabs")
    
    # Set current selection based on settings
    current_provider = parent.settings.default_tts_provider
    provider_index = parent.tts_provider_combo.findData(current_provider)
    if provider_index >= 0:
        parent.tts_provider_combo.setCurrentIndex(provider_index)
    
    parent.tts_provider_combo.setToolTip(
        "🎤 Azure Neural TTS:\n"
        "  • קולות עברית: he-IL-AvriNeural (גבר), he-IL-HilaNeural (אישה)\n"
        "  • מהיר ומשתלם ($0.016/1K תווים)\n"
        "  • איכות טובה לרוב השימושים\n\n"
        "🎙️ ElevenLabs (מומלץ לעברית):\n"
        "  • תומך בעברית עם המודלים eleven_v3 / eleven_v4\n"
        "  • קולות טבעיים יותר, ביטוי רגשי טוב יותר\n"
        "  • 30+ קולות לבחירה\n"
        "  • יקר יותר (~$0.30/1K תווים)"
    )
    parent.tts_provider_combo.currentIndexChanged.connect(
        parent._handle_tts_provider_change
    )
    tts_row.addWidget(parent.tts_provider_combo, 1)
    
    # ElevenLabs voice selector button
    parent.elevenlabs_voices_btn = QPushButton("בחר קולות")
    parent.elevenlabs_voices_btn.setToolTip(
        "פתח את בורר הקולות של ElevenLabs לבחירת קולות מותאמים אישית.\n"
        "30+ קולות זמינים, קוראים עברית עם המודלים eleven_v3 / eleven_v4."
    )
    parent.elevenlabs_voices_btn.clicked.connect(parent._open_elevenlabs_voice_selector)
    parent.elevenlabs_voices_btn.setVisible(current_provider == "elevenlabs")
    tts_row.addWidget(parent.elevenlabs_voices_btn)
    
    inputs_layout.addLayout(tts_row)
    
    # Create a more informative TTS helper with Hebrew info
    tts_helper = helper_label(
        "🇮🇱 שני הספקים תומכים בעברית:\n"
        "• Azure: קולות סינתטיים מהירים (AvriNeural/HilaNeural)\n"
        "• ElevenLabs: קולות טבעיים יותר (מודל turbo_v2_5) - מומלץ!"
    )
    inputs_layout.addWidget(tts_helper)
    
    # Run preferences toggles
    toggles = QGroupBox("העדפות הרצה")
    toggles_layout = QVBoxLayout(toggles)
    
    parent.include_visuals_cb = QCheckBox("צור וידאו (ויזואליזציות)")
    parent.include_visuals_cb.setChecked(True)
    parent.include_visuals_cb.setToolTip(
        "מפעיל את מנוע Manim/Video ומפיק וידאו גם אם כבר קיים אודיו."
    )
    
    parent.skip_cache_cb = QCheckBox("דלג על מטמון דיאלוג")
    parent.skip_cache_cb.setToolTip(
        "תמיד יפיק דיאלוג מחדש במקום להשתמש בקובץ cache קיים."
    )
    
    parent.force_cb = QCheckBox("דרוס קבצים קיימים (Force)")
    parent.force_cb.setToolTip(
        "יכתוב מחדש דיאלוג/אודיו/וידאו גם אם קבצים קיימים בתיקיית הפלט."
    )
    
    parent.dry_run_cb = QCheckBox("הרצה יבשה (ללא הפקה)")
    parent.dry_run_cb.setToolTip(
        "מבצע סימולציה ומדווח ללוג בלבד – ללא יצירת אודיו/וידאו."
    )
    
    parent.export_ppt_cb = QCheckBox("צור מצגת PPTX")
    parent.export_ppt_cb.setToolTip(
        "יוצר מצגת סיכום PPTX מתוך המטא-דאטה והדיאלוג."
    )
    
    for chk in (
        parent.include_visuals_cb,
        parent.skip_cache_cb,
        parent.force_cb,
        parent.dry_run_cb,
        parent.export_ppt_cb,
    ):
        toggles_layout.addWidget(chk)
    
    inputs_layout.addWidget(toggles)
    
    return inputs_card


def _build_metadata_card(
    parent: "PodcastGeneratorWindow",
    section_label: Callable[[str], QLabel],
    helper_label: Callable[[str], QLabel],
    card_widget: Callable[[], QFrame],
) -> QFrame:
    """Build the metadata editor card."""
    meta_card = card_widget()
    meta_layout = QVBoxLayout(meta_card)
    
    meta_layout.addWidget(section_label("מטא-דאטה שנבנה בזמן אמת"))
    meta_layout.addWidget(
        helper_label("תוכן זה משתנה בחיוך כאשר מדברים עם הבינה או מעלים קבצים.")
    )
    
    parent.metadata_preview = QPlainTextEdit()
    parent.metadata_preview.setObjectName("MetadataPreview")
    parent.metadata_preview.setPlaceholderText(
        '{\n  "topic": "...",\n  "date": "2025-11-25"\n}'
    )
    parent.metadata_preview.textChanged.connect(
        parent._handle_metadata_editor_change
    )
    meta_layout.addWidget(parent.metadata_preview)
    
    # Metadata action buttons
    meta_actions = QHBoxLayout()
    
    parent.meta_save_btn = QPushButton("שמור שינויים")
    parent.meta_save_btn.setEnabled(False)
    parent.meta_save_btn.clicked.connect(parent._save_metadata_from_editor)
    
    parent.meta_revert_btn = QPushButton("שחזר")
    parent.meta_revert_btn.setEnabled(False)
    parent.meta_revert_btn.clicked.connect(parent._revert_metadata_editor)
    
    network_btn = QPushButton("מפת רשת")
    network_btn.clicked.connect(parent._export_network_map)
    
    export_btn = QPushButton("ייצוא JSON...")
    export_btn.clicked.connect(parent._export_metadata_to_file)
    
    meta_actions.addWidget(parent.meta_save_btn)
    meta_actions.addWidget(parent.meta_revert_btn)
    meta_actions.addWidget(network_btn)
    meta_actions.addWidget(export_btn)
    
    meta_layout.addLayout(meta_actions)
    
    # Visual metadata generation row
    visual_meta_layout = QHBoxLayout()
    
    parent.visual_meta_btn = QPushButton("🎨 צור מטא-דאטה ויזואלית")
    parent.visual_meta_btn.setToolTip(
        "יצירת מטא-דאטה מפורטת ל-10 תמונות + וידאו\n"
        "כולל prompts, סגנונות, מצבי רוח ופלטות צבעים\n"
        "לשימוש עם Google Imagen ו-VEO"
    )
    parent.visual_meta_btn.clicked.connect(parent._generate_visual_metadata)
    
    parent.visual_meta_status = QLabel("")
    parent.visual_meta_status.setProperty("class", "helper")
    
    visual_meta_layout.addWidget(parent.visual_meta_btn)
    visual_meta_layout.addWidget(parent.visual_meta_status, 1)
    
    meta_layout.addLayout(visual_meta_layout)
    
    return meta_card


def _build_story_card(
    parent: "PodcastGeneratorWindow",
    section_label: Callable[[str], QLabel],
    helper_label: Callable[[str], QLabel],
    card_widget: Callable[[], QFrame],
) -> QFrame:
    """Build the story preview card."""
    story_card = card_widget()
    story_layout = QVBoxLayout(story_card)
    
    header_row = QHBoxLayout()
    header_row.setContentsMargins(0, 0, 0, 0)
    header_row.setSpacing(8)
    header_row.addWidget(section_label("Notebook Story (טיוטה)"))
    badge = QLabel("טיוטה פעילה")
    badge.setStyleSheet(
        "background-color:#f97316; color:#0b1224; padding:4px 10px; border-radius:12px; font-weight:700;"
    )
    header_row.addWidget(badge)
    header_row.addStretch(1)
    story_layout.addLayout(header_row)
    story_layout.addWidget(
        helper_label("טיוטה אינטראקטיבית שנועדה להציג את עיקרי הסיפור.")
    )
    
    parent.story_preview = QPlainTextEdit()
    parent.story_preview.setReadOnly(True)
    parent.story_preview.setPlaceholderText(
        "תיאור קצר של הסיפור יופיע כאן לאחר יצירת מטא-דאטה."
    )
    parent.story_preview.setStyleSheet(
        "background:#0b1224; border:1px solid #94a3b8; color:#e2e8f0; border-radius:10px; padding:8px;"
    )
    story_layout.addWidget(parent.story_preview)
    
    return story_card


def _build_gallery_card(
    parent: "PodcastGeneratorWindow",
    section_label: Callable[[str], QLabel],
    helper_label: Callable[[str], QLabel],
    card_widget: Callable[[], QFrame],
) -> QFrame:
    """Build the output gallery card."""
    gallery_card = card_widget()
    gallery_layout = QVBoxLayout(gallery_card)
    
    gallery_layout.addWidget(
        section_label("גלריית תוצרים (מצגת / PDF / אודיו)")
    )
    gallery_layout.addWidget(
        helper_label(
            "תצוגה מקדימה של הקבצים שייוצרו בתום הריצה – "
            "כולל מצב הקובץ והצצה ראשונית."
        )
    )
    
    parent.gallery_list = QListWidget()
    parent.gallery_list.itemSelectionChanged.connect(
        parent._update_gallery_buttons
    )
    gallery_layout.addWidget(parent.gallery_list, 1)
    
    # Gallery action buttons
    gallery_btns = QHBoxLayout()
    
    parent.gallery_open_btn = QPushButton("פתח פריט")
    parent.gallery_open_btn.clicked.connect(parent._open_selected_gallery_item)
    
    parent.gallery_export_btn = QPushButton("ייצא PDF")
    parent.gallery_export_btn.clicked.connect(parent._export_gallery_pdf)
    
    gallery_btns.addWidget(parent.gallery_open_btn)
    gallery_btns.addWidget(parent.gallery_export_btn)
    gallery_layout.addLayout(gallery_btns)
    
    parent.gallery_preview = QPlainTextEdit()
    parent.gallery_preview.setReadOnly(True)
    parent.gallery_preview.setPlaceholderText(
        "תיאור שקופיות, Labs וקישורים יוצג כאן לאחר יצירת מטא-דאטה."
    )
    gallery_layout.addWidget(parent.gallery_preview, 2)
    
    return gallery_card


def _build_status_card(
    parent: "PodcastGeneratorWindow",
    section_label: Callable[[str], QLabel],
    card_widget: Callable[[], QFrame],
) -> QFrame:
    """Build the run status card."""
    status_card = card_widget()
    status_layout = QVBoxLayout(status_card)
    
    status_layout.addWidget(section_label("סטטוס ריצה"))
    
    parent.run_stage_label = QLabel("אין ריצה פעילה")
    parent.run_stage_label.setStyleSheet("font-size:16px;font-weight:600;")
    status_layout.addWidget(parent.run_stage_label)
    
    parent.run_progress_bar = QProgressBar()
    parent.run_progress_bar.setRange(0, 100)
    parent.run_progress_bar.setValue(0)
    status_layout.addWidget(parent.run_progress_bar)
    
    parent.run_timer_label = QLabel(
        "כשתתחיל הרצה נראה כאן את ההתקדמות והזמן שחלף."
    )
    parent.run_timer_label.setProperty("class", "helper")
    status_layout.addWidget(parent.run_timer_label)
    
    parent.run_status_card = status_card
    parent.run_status_card.setVisible(False)
    
    return status_card


def _build_action_buttons(parent: "PodcastGeneratorWindow") -> QHBoxLayout:
    """Build the main action buttons row."""
    actions = QHBoxLayout()
    
    # Quality check button
    parent.quality_check_btn = QPushButton("🔍 בדיקת מערכת")
    parent.quality_check_btn.setToolTip("הרצת בדיקות איכות לפני ואחרי Pipeline")
    parent.quality_check_btn.clicked.connect(parent._run_quality_check)
    
    parent.run_btn = QPushButton("הפעל Pipeline")
    parent.run_btn.setObjectName("PrimaryButton")
    parent.run_btn.clicked.connect(parent._run_pipeline)
    
    parent.open_output_btn = QPushButton("פתח תיקיית פלט")
    parent.open_output_btn.clicked.connect(parent._open_outputs)
    
    actions.addWidget(parent.quality_check_btn)
    actions.addWidget(parent.run_btn)
    actions.addWidget(parent.open_output_btn)
    
    return actions

