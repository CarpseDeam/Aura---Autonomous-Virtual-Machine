from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QTextEdit, QHBoxLayout
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QTimer
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event


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
            font-family: "Courier New", Courier, monospace;
        }
        QLabel#aura_banner {
            color: #FFB74D;
            font-weight: bold;
            font-size: 10px;
            padding: 5px;
            text-align: center;
        }
        QTextEdit#boot_sequence_display {
            background-color: transparent;
            border: none;
            color: #39FF14; /* Neon Green */
            font-size: 14px;
        }
    """

    BOOT_SEQUENCE = [
        {"delay": 500, "text": "[KERNEL] AURA KERNEL V4.0 ... ONLINE"},
        {"delay": 200, "text": "[SYSTEM] Establishing secure link to command deck..."},
        {"delay": 300, "text": "[NEURAL] Cognitive models synchronized."},
        {"delay": 150, "text": "[SYSTEM] All systems nominal. Ready for user input."},
        {"delay": 0, "text": "\n[AURA] Welcome. How can I assist you today?"}
    ]

    def __init__(self, event_bus: EventBus):
        """
        Initializes the MainWindow.

        Args:
            event_bus: The application's central event bus.
        """
        super().__init__()
        self.event_bus = event_bus
        self.setWindowTitle("Aura - Command Deck")
        self.setGeometry(100, 100, 800, 600)
        self.setStyleSheet(self.AURA_STYLESHEET)

        self.current_boot_step = 0
        self._init_ui()
        self._start_boot_sequence()

    def _init_ui(self):
        """Initializes the user interface of the main window."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header (Banner and Buttons)
        header_widget = self._create_header()
        main_layout.addWidget(header_widget)

        # Main content area for boot sequence/chat
        self.boot_sequence_display = QTextEdit()
        self.boot_sequence_display.setObjectName("boot_sequence_display")
        self.boot_sequence_display.setReadOnly(True)
        main_layout.addWidget(self.boot_sequence_display)

        # We will add the input text box here later

    def _create_header(self):
        """Creates the header widget containing the banner and buttons."""
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(5, 5, 5, 5)
        header_layout.setSpacing(10)

        # AURA Banner
        banner_label = QLabel(self.AURA_ASCII_BANNER)
        banner_label.setObjectName("aura_banner")
        banner_label.setFont(QFont("Courier New", 10))
        banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Placeholder for buttons
        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        button_layout.addStretch()  # For now, we will add buttons later

        header_layout.addWidget(banner_label)
        header_layout.addWidget(button_container)

        return header_widget

    def _start_boot_sequence(self):
        """Starts the boot sequence animation."""
        self.event_bus.dispatch(Event(event_type="APP_START"))
        self.boot_timer = QTimer(self)
        self.boot_timer.timeout.connect(self._update_boot_sequence)
        self.boot_timer.start(500)  # Initial delay for the first line

    def _update_boot_sequence(self):
        """Updates the boot sequence display with the next line."""
        if self.current_boot_step < len(self.BOOT_SEQUENCE):
            line_info = self.BOOT_SEQUENCE[self.current_boot_step]
            self.boot_sequence_display.append(line_info["text"])
            self.current_boot_step += 1

            # Set timer for the next line's delay
            if self.current_boot_step < len(self.BOOT_SEQUENCE):
                next_delay = self.BOOT_SEQUENCE[self.current_boot_step]["delay"]
                self.boot_timer.setInterval(next_delay)
            else:
                self.boot_timer.stop()
        else:
            self.boot_timer.stop()