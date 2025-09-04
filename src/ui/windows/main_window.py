import logging
import os
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QTextEdit, QHBoxLayout, QApplication, QPushButton
from PySide6.QtGui import QFont, QTextCursor, QIcon
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.config import ASSETS_DIR
from src.ui.widgets.chat_input import ChatInputTextEdit

logger = logging.getLogger(__name__)


# Helper class to run Qt signals from non-GUI threads
class Signaller(QObject):
    chunk_received = Signal(str)
    stream_ended = Signal()
    error_received = Signal(str)


class MainWindow(QMainWindow):
    """
    The main window for the AURA application, serving as the command deck.
    """
    AURA_ASCII_BANNER = """█████╗ ██╗   ██╗██████╗  █████╗ 
██╔══██╗██║   ██║██╔══██╗██╔══██╗
███████║██║   ██║██████╔╝███████║
██╔══██║██║   ██║██╔══██╗██╔══██║
██║  ██║╚██████╔╝██║  ██║██║  ██║
╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝
    A U T O N O M O U S   V I R T U A L   M A C H I N E"""

    AURA_STYLESHEET = """
        QMainWindow, QWidget {
            background-color: #1a1a1a;
            color: #dcdcdc;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
        }
        QLabel#aura_banner {
            color: #FFB74D; /* Amber */
            font-weight: bold;
            font-size: 10px;
            padding: 5px;
        }
        QTextEdit#chat_display {
            background-color: #2c2c2c;
            border-top: 1px solid #4a4a4a;
            border-bottom: 1px solid #4a4a4a;
            color: #dcdcdc; /* Light Grey */
            font-size: 14px;
        }
        QTextEdit#chat_input {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a; /* Subtle Grey */
            color: #dcdcdc;
            font-size: 14px;
            padding: 8px;
            border-radius: 5px;
            max-height: 80px; /* Control the height */
        }
        QTextEdit#chat_input:focus {
            border: 1px solid #FFB74D; /* Amber */
        }
        QPushButton#settings_button {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a;
            color: #dcdcdc;
            border-radius: 5px;
            width: 40px;
            height: 40px;
        }
        QPushButton#settings_button:hover {
            border: 1px solid #FFB74D; /* Amber */
        }
    """

    BOOT_SEQUENCE = [
        {"delay": 200, "text": "[SYSTEM] 09:25:51"},
        {"delay": 150, "text": "AURA Command Deck Initialized"},
        {"delay": 100, "text": ""},
        {"delay": 80, "text": "Status: READY"},
        {"delay": 80, "text": "System: Online"},
        {"delay": 80, "text": "Mode: Interactive"},
        {"delay": 250, "text": ""},
        {"delay": 100, "text": "Enter your commands or describe what you want to build..."},
    ]

    def __init__(self, event_bus: EventBus):
        """Initializes the MainWindow."""
        super().__init__()
        self.event_bus = event_bus
        self.task_log_window = None  # Will be set by AuraApp
        self.setWindowTitle("Aura - Command Deck")
        self.setGeometry(100, 100, 900, 700)

        self._set_window_icon()
        self.setStyleSheet(self.AURA_STYLESHEET)

        self.is_booting = True
        self.is_streaming_response = False
        self.signaller = Signaller()

        self._init_ui()
        self._register_event_handlers()
        self._start_boot_sequence()

    def _set_window_icon(self):
        """Sets the main window icon."""
        icon_path = ASSETS_DIR / "aura_gear_icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            logger.warning(f"Window icon not found at {icon_path}.")

    def _init_ui(self):
        """Initializes the user interface of the main window."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        header_widget = self._create_header()

        self.chat_display = QTextEdit()
        self.chat_display.setObjectName("chat_display")
        self.chat_display.setReadOnly(True)

        input_container = self._create_input_area()

        main_layout.addWidget(header_widget)
        main_layout.addWidget(self.chat_display, 1)
        main_layout.addWidget(input_container)

    def _create_header(self):
        """Creates the dedicated header widget with banner and settings button."""
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)

        banner_label = QLabel(self.AURA_ASCII_BANNER)
        banner_label.setObjectName("aura_banner")
        banner_label.setFont(QFont("JetBrains Mono", 10))
        banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Buttons Container
        button_container = QWidget()
        button_layout = QVBoxLayout(button_container)
        button_layout.setSpacing(8)

        self.btn_settings = QPushButton()
        self.btn_settings.setObjectName("settings_button")
        self.btn_settings.setToolTip("Settings")
        icon_path = ASSETS_DIR / "aura_gear_icon.ico"
        if icon_path.exists():
            self.btn_settings.setIcon(QIcon(str(icon_path)))
            self.btn_settings.setIconSize(QSize(24, 24))
        else:
            logger.warning(f"Settings icon not found at {icon_path}. Using text fallback.")
            self.btn_settings.setText("⚙")

        self.btn_settings.clicked.connect(self._open_settings_dialog)

        button_layout.addWidget(self.btn_settings)
        button_layout.addStretch() # Pushes the button to the top

        header_layout.addStretch(1)
        header_layout.addWidget(banner_label, 2) # Banner takes twice the space
        header_layout.addWidget(button_container, 1) # Buttons take 1 part

        return header_widget

    def _create_input_area(self):
        """Creates the bottom input area."""
        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)

        self.chat_input = ChatInputTextEdit()
        self.chat_input.setObjectName("chat_input")
        self.chat_input.setPlaceholderText("Describe what you want to build...")
        self.chat_input.sendMessage.connect(self._send_message)
        self.chat_input.setEnabled(False)

        input_layout.addWidget(self.chat_input, 1)
        input_layout.addStretch(1)

        return input_container

    def _register_event_handlers(self):
        """Connects UI signals to the event bus."""
        self.signaller.chunk_received.connect(self._handle_model_chunk)
        self.signaller.stream_ended.connect(self._handle_stream_end)
        self.signaller.error_received.connect(self._handle_model_error)
        self.event_bus.subscribe("MODEL_CHUNK_RECEIVED",
                                 lambda event: self.signaller.chunk_received.emit(event.payload.get("chunk", "")))
        self.event_bus.subscribe("MODEL_STREAM_ENDED", lambda event: self.signaller.stream_ended.emit())
        self.event_bus.subscribe("MODEL_ERROR", lambda event: self.signaller.error_received.emit(
            event.payload.get("message", "Unknown error")))

    def _open_settings_dialog(self):
        """Placeholder for opening the settings dialog."""
        logger.info("Settings button clicked. This will open the settings dialog.")

    def _start_boot_sequence(self):
        """Starts the boot sequence animation."""
        self.chat_display.clear()
        self.current_boot_step = 0
        self.boot_timer = QTimer(self)
        self.boot_timer.timeout.connect(self._update_boot_sequence)
        self.boot_timer.start(50) # Start faster

    def _update_boot_sequence(self):
        """Updates the boot sequence display with the next line."""
        if self.current_boot_step < len(self.BOOT_SEQUENCE):
            line_info = self.BOOT_SEQUENCE[self.current_boot_step]
            self.chat_display.append(f"<span style='color: #39FF14;'>{line_info['text']}</span>")
            self.current_boot_step += 1
            if self.current_boot_step < len(self.BOOT_SEQUENCE):
                self.boot_timer.setInterval(self.BOOT_SEQUENCE[self.current_boot_step]["delay"])
            else:
                self._end_boot_sequence()
        else:
            self._end_boot_sequence()

    def _end_boot_sequence(self):
        """Finalizes the boot sequence."""
        self.boot_timer.stop()
        self.is_booting = False
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()

    def _send_message(self):
        """Sends the user's message from the input box."""
        user_text = self.chat_input.toPlainText().strip()
        if not user_text:
            return

        self.chat_input.clear()
        self.chat_input.setEnabled(False)

        self.chat_display.append(f"<br><span style='color: #FFB74D;'>[USER]</span>")
        self.chat_display.append(f"<div style='padding-left: 15px;'>{user_text.replace(os.linesep, '<br>')}</div>")
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())

        event = Event(event_type="SEND_USER_MESSAGE", payload={"text": user_text})
        self.event_bus.dispatch(event)

    def _handle_model_chunk(self, chunk: str):
        """Appends a chunk of text from the model to the display."""
        if not self.is_streaming_response:
            self.is_streaming_response = True
            self.chat_display.append(f"<br><span style='color: #00FFFF;'>[AURA]</span>")
            self.chat_display.insertHtml("<div style='padding-left: 15px;'>")

        safe_chunk = chunk.replace('\n', '<br>')
        self.chat_display.insertHtml(safe_chunk)
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())

    def _handle_stream_end(self):
        """Called when the model is finished sending chunks."""
        if self.is_streaming_response:
            self.chat_display.insertHtml("</div>")
        self.is_streaming_response = False
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())

    def _handle_model_error(self, error_message: str):
        """Displays an error message in the chat."""
        self.chat_display.append(f"<span style='color: #FF0000;'>[ERROR] {error_message}</span>")
        self._handle_stream_end()

    def _update_task_log_position(self):
        """Updates the position of the task log window to be pinned to the right."""
        if not self.task_log_window or not self.isVisible():
            return

        main_window_pos = self.pos()
        main_window_width = self.width()
        gap = 8  # A small gap between windows

        new_x = main_window_pos.x() + main_window_width + gap
        new_y = main_window_pos.y()

        self.task_log_window.move(new_x, new_y)
        self.task_log_window.resize(self.task_log_window.width(), self.height())

    def moveEvent(self, event):
        """Override moveEvent to move the task log window along with the main window."""
        super().moveEvent(event)
        self._update_task_log_position()

    def resizeEvent(self, event):
        """Override resizeEvent to adjust the task log window's position and height."""
        super().resizeEvent(event)
        self._update_task_log_position()

    def showEvent(self, event):
        """Override showEvent to position the task log window when the main window is first shown."""
        super().showEvent(event)
        QTimer.singleShot(0, self._update_task_log_position)

    def closeEvent(self, event):
        """Ensure the entire application quits when the main window is closed."""
        QApplication.quit()
        super().closeEvent(event)