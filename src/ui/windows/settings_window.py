import logging
import json
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QFormLayout, QComboBox,
                               QDoubleSpinBox, QPushButton, QGroupBox, QHBoxLayout,
                               QScrollArea)
from PySide6.QtCore import Qt, Signal, QObject, Slot
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.config import AGENT_CONFIG, SETTINGS_FILE

logger = logging.getLogger(__name__)


class SettingsSignaller(QObject):
    """A signaller to safely update the UI from other threads."""
    models_received = Signal(dict)


class SettingsWindow(QWidget):
    """
    The settings dialog for configuring AI agents and other application settings.
    """
    SETTINGS_STYLESHEET = """
        QWidget {
            background-color: #1e1e1e;
            color: #dcdcdc;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
        }
        QScrollArea {
            border: none;
        }
        #scroll_widget {
             background-color: #1e1e1e;
        }
        QGroupBox {
            border: 1px solid #4a4a4a;
            border-radius: 5px;
            margin-top: 1ex;
            font-size: 14px;
            font-weight: bold;
            color: #FFB74D; /* Amber */
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top center;
            padding: 0 3px;
        }
        QLabel {
            font-size: 14px;
            border: none;
            padding-top: 5px; /* Align with input fields */
        }
        QLabel#title {
            color: #FFB74D;
            font-weight: bold;
            font-size: 18px;
            padding-bottom: 10px;
        }
        QComboBox, QDoubleSpinBox {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a;
            color: #dcdcdc;
            font-size: 14px;
            padding: 6px;
            border-radius: 5px;
        }
        QComboBox:focus, QDoubleSpinBox:focus {
            border: 1px solid #FFB74D; /* Amber */
        }
        QComboBox::drop-down {
            border: none;
        }
        QComboBox QAbstractItemView {
            background-color: #2c2c2c;
            selection-background-color: #FFB74D;
            selection-color: #000000;
        }
        QPushButton {
            background-color: #2c2c2c;
            border: 1px solid #FFB74D;
            color: #FFB74D;
            font-size: 14px;
            font-weight: bold;
            padding: 8px 12px;
            border-radius: 5px;
            min-width: 100px;
        }
        QPushButton:hover {
            background-color: #3a3a3a;
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
        """Initializes the SettingsWindow."""
        super().__init__(parent)
        self.event_bus = event_bus
        self.signaller = SettingsSignaller()

        self.setWindowTitle("Agent Configuration")
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setGeometry(200, 200, 550, 600)
        self.setStyleSheet(self.SETTINGS_STYLESHEET)
        self.setWindowModality(Qt.ApplicationModal)

        self.agent_widgets = {}
        self._init_ui()
        self._register_event_handlers()
        self.event_bus.dispatch(Event(event_type="REQUEST_AVAILABLE_MODELS"))

    def _init_ui(self):
        """Initializes the user interface of the settings window."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        title_label = QLabel("AGENT CONFIGURATION")
        title_label.setObjectName("title")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_widget.setObjectName("scroll_widget")
        self.form_container_layout = QVBoxLayout(scroll_widget)
        scroll_area.setWidget(scroll_widget)
        main_layout.addWidget(scroll_area)

        for agent_name, config in AGENT_CONFIG.items():
            group_box = QGroupBox(agent_name.replace("_", " ").title())
            form_layout = QFormLayout()
            form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            form_layout.setContentsMargins(10, 15, 10, 10)
            form_layout.setSpacing(10)

            model_input = QComboBox()
            temperature_input = QDoubleSpinBox()
            temperature_input.setRange(0.0, 2.0)
            temperature_input.setSingleStep(0.1)
            temperature_input.setValue(config.get("temperature", 0.7))

            top_p_input = QDoubleSpinBox()
            top_p_input.setRange(0.0, 1.0)
            top_p_input.setSingleStep(0.1)
            top_p_input.setValue(config.get("top_p", 1.0))

            form_layout.addRow(QLabel("Model:"), model_input)
            form_layout.addRow(QLabel("Temperature:"), temperature_input)
            form_layout.addRow(QLabel("Top P:"), top_p_input)

            group_box.setLayout(form_layout)
            self.form_container_layout.addWidget(group_box)

            self.agent_widgets[agent_name] = {
                "model": model_input,
                "temperature": temperature_input,
                "top_p": top_p_input
            }
        self.form_container_layout.addStretch()

        button_layout = QHBoxLayout()
        save_button = QPushButton("Save & Close")
        save_button.setObjectName("save_button")
        save_button.clicked.connect(self._save_settings)
        close_button = QPushButton("Cancel")
        close_button.clicked.connect(self.close)

        button_layout.addStretch()
        button_layout.addWidget(close_button)
        button_layout.addWidget(save_button)
        main_layout.addLayout(button_layout)

    def _register_event_handlers(self):
        """Connects UI signals and subscribes to events."""
        self.signaller.models_received.connect(self._on_models_received)
        self.event_bus.subscribe(
            "AVAILABLE_MODELS_RECEIVED",
            lambda event: self.signaller.models_received.emit(event.payload["models"])
        )

    @Slot(dict)
    def _on_models_received(self, models: dict):
        """Populates the model dropdowns when models are received."""
        for agent_name, widgets in self.agent_widgets.items():
            combo_box = widgets["model"]
            combo_box.clear()
            for provider, model_list in models.items():
                combo_box.addItem(f"--- {provider} ---").setFlags(Qt.ItemFlag.NoItemFlags)
                for model_name in model_list:
                    combo_box.addItem(model_name)
        self._load_settings()

    def _load_settings(self):
        """Loads settings from the JSON file or uses defaults."""
        try:
            config_to_load = AGENT_CONFIG
            if SETTINGS_FILE.exists():
                logger.info(f"Loading user settings from {SETTINGS_FILE}")
                with open(SETTINGS_FILE, 'r') as f:
                    config_to_load = json.load(f)

            for agent_name, widgets in self.agent_widgets.items():
                if agent_name in config_to_load:
                    config = config_to_load[agent_name]
                    model_name = config.get("model")
                    if model_name:
                        index = widgets["model"].findText(model_name)
                        if index != -1:
                            widgets["model"].setCurrentIndex(index)
                    widgets["temperature"].setValue(config.get("temperature", 0.7))
                    widgets["top_p"].setValue(config.get("top_p", 1.0))
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load settings from {SETTINGS_FILE}: {e}")

    def _save_settings(self):
        """Saves the current settings from the UI to a JSON file."""
        new_config = {}
        for agent_name, widgets in self.agent_widgets.items():
            model_text = widgets["model"].currentText()
            if "---" in model_text:
                model_text = ""

            new_config[agent_name] = {
                "model": model_text,
                "temperature": widgets["temperature"].value(),
                "top_p": widgets["top_p"].value()
            }
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(new_config, f, indent=4)
            logger.info(f"Successfully saved user settings to {SETTINGS_FILE}")
            # Dispatch an event to tell the LLMService to reload its config
            self.event_bus.dispatch(Event(event_type="RELOAD_LLM_CONFIG"))
        except IOError as e:
            logger.error(f"Failed to save settings to {SETTINGS_FILE}: {e}")
        self.close()

    def showEvent(self, event):
        """Overrides the show event to request models when the window is shown."""
        super().showEvent(event)
        logger.info("Settings window opened, requesting available models...")
        self.event_bus.dispatch(Event(event_type="REQUEST_AVAILABLE_MODELS"))