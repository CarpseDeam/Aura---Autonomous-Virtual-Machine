import logging
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)


class SettingsWindow(QWidget):
    """
    The settings dialog for configuring AI agents and other application settings.
    """
    SETTINGS_STYLESHEET = """
        QWidget {
            background-color: #000000;
            color: #dcdcdc;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
            border: 1px solid #FFB74D; /* Amber */
            border-radius: 5px;
        }
        QLabel {
            font-size: 14px;
            border: none;
        }
        QLabel#title {
            color: #FFB74D;
            font-weight: bold;
            font-size: 18px;
        }
    """

    def __init__(self, parent=None):
        """Initializes the SettingsWindow."""
        super().__init__(parent)
        self.setWindowTitle("Agent Configuration")
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setGeometry(200, 200, 500, 400)
        self.setStyleSheet(self.SETTINGS_STYLESHEET)

        # Make the window modal so it blocks interaction with other windows
        self.setWindowModality(Qt.ApplicationModal)

        self._init_ui()

    def _init_ui(self):
        """Initializes the user interface of the settings window."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        title_label = QLabel("AGENT CONFIGURATION")
        title_label.setObjectName("title")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        placeholder_label = QLabel("Agent configuration and settings will be available here soon.")
        placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(title_label)
        layout.addWidget(placeholder_label)
        layout.addStretch()