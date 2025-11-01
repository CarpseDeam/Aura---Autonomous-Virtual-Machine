import logging
from typing import Dict

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QHBoxLayout,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.services.user_settings_manager import (
    AURA_BRAIN_MODEL_CHOICES,
    TERMINAL_AGENT_PRESETS,
    load_user_settings,
    save_user_settings,
)


logger = logging.getLogger(__name__)


class SettingsWindow(QWidget):
    """
    Simplified settings dialog that keeps Aura's retro styling while focusing on
    the handful of controls users actually need.
    """

    SETTINGS_STYLESHEET = """
        QWidget {
            background-color: #000000;
            color: #00ff00;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
            font-size: 14px;
        }
        QLabel#title {
            color: #FFB74D;
            font-size: 20px;
            font-weight: bold;
            border: none;
            padding: 4px 0 12px 0;
        }
        QLabel#ascii_border, QLabel#section_label {
            color: #FFB74D;
        }
        QLabel#field_label {
            color: #FFB74D;
            min-width: 220px;
        }
        QLabel#hint_label {
            color: #00ff00;
            font-size: 12px;
        }
        QComboBox, QLineEdit {
            background-color: #101010;
            border: 1px solid #4a4a4a;
            color: #FFB74D;
            padding: 6px;
            border-radius: 4px;
        }
        QComboBox::drop-down {
            border: none;
        }
        QComboBox QAbstractItemView {
            background-color: #101010;
            selection-background-color: #FFB74D;
            selection-color: #000000;
        }
        QLineEdit[echoMode="2"] {
            letter-spacing: 2px;
        }
        QPushButton {
            background-color: #101010;
            border: 1px solid #FFB74D;
            color: #FFB74D;
            font-weight: bold;
            padding: 8px 16px;
            border-radius: 4px;
            min-width: 140px;
        }
        QPushButton:hover {
            background-color: #1a1a1a;
        }
        QPushButton#save_button {
            background-color: #FFB74D;
            color: #000000;
        }
        QPushButton#save_button:hover {
            background-color: #FFA726;
        }
    """

    def __init__(self, event_bus: EventBus, parent=None):
        super().__init__(parent)
        self.event_bus = event_bus
        self.setWindowTitle("Aura Configuration")
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setGeometry(200, 200, 540, 520)
        self.setStyleSheet(self.SETTINGS_STYLESHEET)
        self.setWindowModality(Qt.ApplicationModal)

        self.brain_combo: QComboBox
        self.terminal_combo: QComboBox
        self.custom_command_input: QLineEdit
        self.api_key_inputs: Dict[str, QLineEdit] = {}
        self.auto_accept_checkbox: QCheckBox

        self._init_ui()
        self._load_settings()

    # ---- UI Construction -------------------------------------------------
    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(12)

        title = QLabel("AURA CONFIGURATION")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)

        ascii_font = QFont("JetBrains Mono", 11)

        top_border = QLabel("+----------------------------------------------+")
        top_border.setFont(ascii_font)
        top_border.setAlignment(Qt.AlignCenter)
        top_border.setObjectName("ascii_border")
        main_layout.addWidget(top_border)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 12, 18, 12)
        panel_layout.setSpacing(14)

        # Aura Brain selector
        panel_layout.addLayout(self._create_header_row("Aura Brain (Thinking):"))
        self.brain_combo = QComboBox()
        for value, display in AURA_BRAIN_MODEL_CHOICES:
            self.brain_combo.addItem(display, userData=value)
        panel_layout.addWidget(self.brain_combo)

        # Terminal agent selector
        panel_layout.addLayout(self._create_header_row("Terminal Agent (Building):"))
        self.terminal_combo = QComboBox()
        for key, preset in TERMINAL_AGENT_PRESETS.items():
            self.terminal_combo.addItem(preset["label"], userData=key)
        self.terminal_combo.addItem("Custom command...", userData="custom")
        self.terminal_combo.currentIndexChanged.connect(self._on_terminal_changed)
        panel_layout.addWidget(self.terminal_combo)

        self.custom_command_input = QLineEdit()
        self.custom_command_input.setPlaceholderText("Enter custom command template (use {spec_path})")
        panel_layout.addWidget(self.custom_command_input)

        panel_layout.addWidget(self._create_section_label("------ API Keys ------", ascii_font))
        for provider_key, label in [
            ("anthropic", "Anthropic"),
            ("openai", "OpenAI"),
            ("google", "Google"),
        ]:
            input_field = QLineEdit()
            input_field.setObjectName(f"api_{provider_key}")
            input_field.setEchoMode(QLineEdit.Password)
            panel_layout.addLayout(self._create_field_row(f"{label}:", input_field))
            self.api_key_inputs[provider_key] = input_field

        panel_layout.addWidget(self._create_section_label("------ Preferences ------", ascii_font))
        self.auto_accept_checkbox = QCheckBox("Auto-accept code changes")
        panel_layout.addWidget(self.auto_accept_checkbox)

        footer_layout = QHBoxLayout()
        footer_layout.addStretch(1)
        save_button = QPushButton("Save & Close")
        save_button.setObjectName("save_button")
        save_button.clicked.connect(self._handle_save)
        footer_layout.addWidget(save_button)
        panel_layout.addLayout(footer_layout)

        bottom_border = QLabel("+----------------------------------------------+")
        bottom_border.setFont(ascii_font)
        bottom_border.setAlignment(Qt.AlignCenter)
        bottom_border.setObjectName("ascii_border")

        main_layout.addWidget(panel)
        main_layout.addWidget(bottom_border)

    def _create_header_row(self, text: str) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(text)
        label.setObjectName("field_label")
        layout.addWidget(label)
        layout.addStretch(1)
        return layout

    def _create_field_row(self, label_text: str, widget: QLineEdit) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel(label_text)
        label.setObjectName("field_label")
        layout.addWidget(label)
        layout.addWidget(widget)
        return layout

    def _create_section_label(self, text: str, font: QFont) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setFont(font)
        label.setObjectName("section_label")
        return label

    # ---- Data Binding ----------------------------------------------------
    def _load_settings(self) -> None:
        try:
            settings = load_user_settings()
        except Exception as exc:
            logger.error("Failed to load user settings: %s", exc)
            settings = {}

        brain_value = settings.get("aura_brain_model")
        self._select_combo_value(self.brain_combo, brain_value)

        terminal_value = settings.get("terminal_agent")
        self._select_combo_value(self.terminal_combo, terminal_value)
        custom_command = settings.get("terminal_agent_custom_command") or ""
        self.custom_command_input.setText(custom_command)
        self._on_terminal_changed()

        api_keys = settings.get("api_keys") or {}
        for provider, input_field in self.api_key_inputs.items():
            input_field.setText(api_keys.get(provider, ""))

        self.auto_accept_checkbox.setChecked(bool(settings.get("auto_accept_changes", True)))

    def _select_combo_value(self, combo: QComboBox, value: str) -> None:
        if not isinstance(value, str):
            return
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _collect_api_keys(self) -> Dict[str, str]:
        return {provider: field.text().strip() for provider, field in self.api_key_inputs.items()}

    # ---- Event Handlers --------------------------------------------------
    def _on_terminal_changed(self) -> None:
        is_custom = self.terminal_combo.currentData() == "custom"
        self.custom_command_input.setVisible(is_custom)

    def _handle_save(self) -> None:
        settings_payload = {
            "aura_brain_model": self.brain_combo.currentData() or "",
            "terminal_agent": self.terminal_combo.currentData() or "codex",
            "terminal_agent_custom_command": self.custom_command_input.text().strip(),
            "api_keys": self._collect_api_keys(),
            "auto_accept_changes": self.auto_accept_checkbox.isChecked(),
        }

        try:
            save_user_settings(settings_payload)
            logger.info("User settings saved successfully.")
        except Exception as exc:
            logger.error("Failed to save user settings: %s", exc)
            return

        self.event_bus.dispatch(Event(event_type="RELOAD_LLM_CONFIG"))
        self.event_bus.dispatch(
            Event(
                event_type="USER_PREFERENCES_UPDATED",
                payload={"preferences": {"auto_accept_changes": settings_payload["auto_accept_changes"]}},
            )
        )

        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self.event_bus.dispatch(Event(event_type="REQUEST_AVAILABLE_MODELS"))
        except Exception as exc:
            logger.debug("Unable to request available models: %s", exc)
