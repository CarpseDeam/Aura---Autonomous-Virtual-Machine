import logging
import os
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QTextEdit, QHBoxLayout
from PySide6.QtGui import QFont, QTextCursor, QIcon
from PySide6.QtCore import Qt, QTimer, Signal, QObject
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
    AURA_ASCII_BANNER = """
 █████╗ ██╗   ██╗██████╗  █████╗ 
██╔══██╗██║   ██║██╔══██╗██╔══██╗
███████║██║   ██║██████╔╝███████║
██╔══██║██║   ██║██╔══██╗██╔══██║
██║  ██║╚██████╔╝██║  ██║██║  ██║
╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝
    A U T O N O M O U S   V I R T U A L   M A C H I N E
    """

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
            text-align: center;
        }
        QTextEdit#chat_display {
            background-color: transparent;
            border: none;
            color: #dcdcdc; /* Light Grey */
            font-size: 14px;
        }
        QTextEdit#chat_input {
            background-color: #2c2c2c;
            border: 1px solid #FFB74D; /* Amber */
            color: #dcdcdc;
            font-size: 14px;
            padding: 8px;
            border-radius: 5px;
            max-height: 80px; /* Control the height */
        }
        QTextEdit#chat_input:focus {
            border: 1px solid #00FFFF; /* Cyan */
        }
    """

    BOOT_SEQUENCE = [
        {"delay": 200, "text": "[KERNEL] AURA KERNEL V4.0 ... ONLINE"},
        {"delay": 100, "text": "[SYSTEM] Establishing secure link to command deck..."},
        {"delay": 150, "text": "[NEURAL] Cognitive models synchronized."},
        {"delay": 80, "text": "[SYSTEM] All systems nominal. Ready for user input."},
    ]

    def __init__(self, event_bus: EventBus):
        """Initializes the MainWindow."""
        super().__init__()
        self.event_bus = event_bus
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
        icon_path = ASSETS_DIR / "aura_gear_icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            logger.warning(f"Window icon not found at {icon_path}.")

    def _init_ui(self):
        """Initializes the user interface of the main window."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 0, 10, 10)
        main_layout.setSpacing(10)

        header_widget = self._create_header()
        main_layout.addWidget(header_widget)

        self.chat_display = QTextEdit()
        self.chat_display.setObjectName("chat_display")
        self.chat_display.setReadOnly(True)
        main_layout.addWidget(self.chat_display)

        self.chat_input = ChatInputTextEdit()
        self.chat_input.setObjectName("chat_input")
        self.chat_input.setPlaceholderText("Describe what you want to build... (Shift+Enter for new line)")
        self.chat_input.sendMessage.connect(self._send_message)
        self.chat_input.setEnabled(False)
        main_layout.addWidget(self.chat_input)

    def _create_header(self):
        """Creates the header widget containing the banner and buttons."""
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(5, 5, 5, 5)
        header_layout.setSpacing(10)

        banner_label = QLabel(self.AURA_ASCII_BANNER)
        banner_label.setObjectName("aura_banner")
        banner_label.setFont(QFont("JetBrains Mono", 10))
        banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        button_layout.addStretch()

        header_layout.addWidget(banner_label)
        header_layout.addWidget(button_container)

        return header_widget

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

    def _start_boot_sequence(self):
        """Starts the boot sequence animation."""
        self.current_boot_step = 0
        self.boot_timer = QTimer(self)
        self.boot_timer.timeout.connect(self._update_boot_sequence)
        self.boot_timer.start(500)

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
        self.chat_display.append(
            "<br><span style='color: #00FFFF;'>[AURA]</span> Welcome. I am online and ready to assist.")
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()

    def _send_message(self):
        """Sends the user's message from the input box."""
        user_text = self.chat_input.toPlainText().strip()
        if not user_text:
            return

        self.chat_input.clear()
        self.chat_input.setEnabled(False)

        self.chat_display.append(f"<span style='color: #FFB74D;'>[USER]</span>")
        self.chat_display.append(f"<div style='padding-left: 15px;'>{user_text.replace(os.linesep, '<br>')}</div><br>")
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())

        event = Event(event_type="SEND_USER_MESSAGE", payload={"text": user_text})
        self.event_bus.dispatch(event)

    def _handle_model_chunk(self, chunk: str):
        """Appends a chunk of text from the model to the display."""
        if not self.is_streaming_response:
            self.is_streaming_response = True
            self.chat_display.append(f"<span style='color: #00FFFF;'>[AURA]</span>")
            self.chat_display.insertHtml("<div style='padding-left: 15px;'>")

        safe_chunk = chunk.replace('\n', '<br>')
        self.chat_display.insertHtml(safe_chunk)
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())

    def _handle_stream_end(self):
        """Called when the model is finished sending chunks."""
        if self.is_streaming_response:
            self.chat_display.insertHtml("</div><br>")
        self.is_streaming_response = False
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())

    def _handle_model_error(self, error_message: str):
        """Displays an error message in the chat."""
        self.chat_display.append(f"<span style='color: #FF0000;'>[ERROR] {error_message}</span>")
        self._handle_stream_end()