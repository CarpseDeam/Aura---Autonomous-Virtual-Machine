import logging
import os
from collections import deque
from html import escape
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QTextEdit, QHBoxLayout, QApplication, QPushButton, QFileDialog
from PySide6.QtGui import QFont, QTextCursor, QIcon
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.config import ASSETS_DIR
from src.ui.widgets.chat_input import ChatInputTextEdit
from src.ui.windows.settings_window import SettingsWindow
from src.ui.widgets.knight_rider_widget import ThinkingIndicator

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
            background-color: #000000;
            color: #dcdcdc;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
        }
        QLabel#aura_banner {
            color: #FFB74D; /* Amber */
            font-weight: bold;
            font-size: 10px;
            padding-bottom: 10px;
        }
        QTextEdit#chat_display {
            background-color: #000000;
            border-top: 1px solid #4a4a4a;
            border-bottom: none;
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
            border: 1px solid #4a4a4a; /* Subtle Grey */
        }
        QPushButton#top_bar_button {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a; /* Subtle Grey */
            color: #dcdcdc;
            font-size: 14px;
            font-weight: bold;
            padding: 8px 12px;
            border-radius: 5px;
            min-width: 150px;
        }
        QPushButton#top_bar_button:hover {
            background-color: #3a3a3a;
        }
        /* System message styles */
        .system-message {
            color: #39FF14;
            font-weight: bold;
        }
        .system-category {
            color: #00FFFF;
            font-weight: bold;
        }
        .system-status {
            color: #FFB74D;
        }
        /* Aura Command Deck status styles */
        .status-success {
            color: #39FF14;
            font-family: "JetBrains Mono", monospace;
            font-size: 13px;
            margin: 2px 0;
        }
        .status-in-progress {
            color: #FFB74D;
            font-family: "JetBrains Mono", monospace;
            font-size: 13px;
            margin: 2px 0;
        }
        .status-error {
            color: #FF4444;
            font-family: "JetBrains Mono", monospace;
            font-size: 13px;
            margin: 2px 0;
        }
        .status-info {
            color: #64B5F6;
            font-family: "JetBrains Mono", monospace;
            font-size: 13px;
            margin: 2px 0;
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
        self.code_viewer_window = None # Will be set by AuraApp
        self.settings_window = None  # To hold the settings window instance
        self.setWindowTitle("Aura - Command Deck")
        self.setGeometry(100, 100, 675, 805)

        self._set_window_icon()
        self.setStyleSheet(self.AURA_STYLESHEET)

        self.is_booting = True
        self.is_streaming_response = False
        self.signaller = Signaller()
        
        # Aura Command Deck: Animated Terminal System
        self.status_message_queue = deque()  # Queue for status updates
        self.current_status_message = ""  # Currently being typed
        self.current_status_class = ""  # CSS class for current message
        self.current_char_index = 0  # Character position in current message
        self.status_typing_timer = QTimer()
        self.STATUS_TYPING_SPEED = 15  # milliseconds between characters
        
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

        top_bar = self._create_top_bar()
        banner_widget = self._create_banner()

        self.chat_display = QTextEdit()
        self.chat_display.setObjectName("chat_display")
        self.chat_display.setReadOnly(True)

        # Add thinking indicator
        self.thinking_indicator = ThinkingIndicator()
        
        input_container = self._create_input_area()

        main_layout.addWidget(top_bar)
        main_layout.addWidget(banner_widget)
        main_layout.addWidget(self.chat_display, 1)
        main_layout.addWidget(self.thinking_indicator)
        main_layout.addWidget(input_container)

    def _create_top_bar(self):
        """Creates the dedicated top bar for controls."""
        top_bar_widget = QWidget()
        layout = QHBoxLayout(top_bar_widget)
        layout.setContentsMargins(0, 0, 0, 10) # Add some padding below

        btn_new_session = QPushButton("New Session")
        btn_new_session.setObjectName("top_bar_button")
        btn_new_session.clicked.connect(self._start_new_session)

        btn_code_workspace = QPushButton("Code Workspace")
        btn_code_workspace.setObjectName("top_bar_button")
        btn_code_workspace.clicked.connect(self._open_code_workspace)

        btn_import_project = QPushButton("Import Project...")
        btn_import_project.setObjectName("top_bar_button")
        btn_import_project.clicked.connect(self._import_project)

        btn_configure_agents = QPushButton("Configure Agents")
        btn_configure_agents.setObjectName("top_bar_button")
        btn_configure_agents.clicked.connect(self._open_settings_dialog)

        layout.addWidget(btn_new_session)
        layout.addStretch()
        layout.addWidget(btn_code_workspace)
        layout.addWidget(btn_import_project)
        layout.addWidget(btn_configure_agents)

        return top_bar_widget

    def _create_banner(self):
        """Creates the widget for the AURA ASCII banner."""
        banner_widget = QWidget()
        layout = QHBoxLayout(banner_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        banner_label = QLabel(self.AURA_ASCII_BANNER)
        banner_label.setObjectName("aura_banner")
        banner_label.setFont(QFont("JetBrains Mono", 10))
        banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(banner_label)
        return banner_widget

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
        
        # Subscribe to specific thinking states
        self.event_bus.subscribe("DISPATCH_TASK", self._handle_task_dispatch)
        self.event_bus.subscribe("CODE_GENERATED", self._handle_code_generated)
        
        # Subscribe to workflow status events
        self.event_bus.subscribe("AGENT_STARTED", self._handle_agent_started)
        self.event_bus.subscribe("AGENT_COMPLETED", self._handle_agent_completed)
        self.event_bus.subscribe("TASK_COMPLETED", self._handle_task_completed)
        self.event_bus.subscribe("FILE_GENERATED", self._handle_file_generated)
        
        # Subscribe to workspace events
        self.event_bus.subscribe("PROJECT_ACTIVATED", self._handle_project_activated)
        self.event_bus.subscribe("PROJECT_IMPORTED", self._handle_project_imported)
        self.event_bus.subscribe("PROJECT_IMPORT_ERROR", self._handle_project_import_error)
        
        # Aura Command Deck: Subscribe to workflow status updates
        self.event_bus.subscribe("WORKFLOW_STATUS_UPDATE", self._handle_workflow_status_update)
        
        # Setup typing animation timer
        self.status_typing_timer.timeout.connect(self._type_next_status_char)
        self.status_typing_timer.setSingleShot(False)

    def _start_new_session(self):
        """Dispatches an event to start a new session and resets the UI."""
        self.event_bus.dispatch(Event(event_type="NEW_SESSION_REQUESTED"))
        self._start_boot_sequence()

    def _open_settings_dialog(self):
        """Opens the settings dialog, creating it if it doesn't exist."""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.event_bus)
        self.settings_window.show()

    def _open_code_workspace(self):
        """Opens the code viewer window."""
        if self.code_viewer_window:
            self.code_viewer_window.show()
            QTimer.singleShot(0, self._update_code_viewer_position)

    def _import_project(self):
        """Opens a file dialog to import an external project."""
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setWindowTitle("Import Project - Select Directory")
        
        if dialog.exec():
            selected_dirs = dialog.selectedFiles()
            if selected_dirs:
                project_path = selected_dirs[0]
                logger.info(f"User selected project for import: {project_path}")
                
                # Dispatch import request event
                self.event_bus.dispatch(Event(
                    event_type="IMPORT_PROJECT_REQUESTED",
                    payload={"path": project_path}
                ))
                
                # Show system message
                self._display_system_message("WORKSPACE", f"Importing project from: {project_path}")
        else:
            logger.debug("User cancelled project import dialog")

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
            self.chat_display.moveCursor(QTextCursor.End)
            self.chat_display.ensureCursorVisible()
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
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.ensureCursorVisible()

        # Start thinking animation
        self.thinking_indicator.start_thinking("AURA is analyzing your request...")

        event = Event(event_type="SEND_USER_MESSAGE", payload={"text": user_text})
        self.event_bus.dispatch(event)

    def _handle_model_chunk(self, chunk: str):
        """Appends a chunk of text from the model to the display."""
        if not self.is_streaming_response:
            self.is_streaming_response = True
            # Stop thinking animation when first chunk arrives
            self.thinking_indicator.stop_thinking()
            self.chat_display.append(f"<br><span style='color: #00FFFF;'>[AURA]</span>")
            self.chat_display.insertHtml("<div style='padding-left: 15px;'>")

        safe_chunk = chunk.replace('\n', '<br>')
        self.chat_display.insertHtml(safe_chunk)
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.ensureCursorVisible()

    def _handle_stream_end(self):
        """Called when the model is finished sending chunks."""
        if self.is_streaming_response:
            self.chat_display.insertHtml("</div>")
        self.is_streaming_response = False
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.ensureCursorVisible()

    def _handle_model_error(self, error_message: str):
        """Displays an error message in the chat."""
        # Stop thinking animation on error
        self.thinking_indicator.stop_thinking()
        self.chat_display.append(f"<span style='color: #FF0000;'>[ERROR] {error_message}</span>")
        self._handle_stream_end()

    def _handle_task_dispatch(self, event):
        """Handle task dispatch events to show engineering thinking state."""
        if self.thinking_indicator.knight_rider.is_animating:
            self.thinking_indicator.set_thinking_message("AURA is engineering your solution...")
        
        # Display system message for task dispatch
        task_description = event.payload.get("task_description", "Task")
        self._display_system_message("SYSTEM", f"Task dispatched: {task_description}")

    def _handle_agent_started(self, event):
        """Handle agent startup events."""
        agent_name = event.payload.get("agent_name", "Unknown Agent")
        self._display_system_message("KERNEL", f"{agent_name.upper()} ONLINE")

    def _handle_agent_completed(self, event):
        """Handle agent completion events."""
        agent_name = event.payload.get("agent_name", "Unknown Agent")
        status = event.payload.get("status", "completed")
        self._display_system_message("KERNEL", f"{agent_name.upper()} task {status.upper()}")

    def _handle_task_completed(self, event):
        """Handle task completion events."""
        task_description = event.payload.get("task_description", "Task")
        self._display_system_message("SYSTEM", f"Task completed: {task_description}")

    def _handle_file_generated(self, event):
        """Handle file generation events."""
        file_path = event.payload.get("file_path", "unknown")
        operation = event.payload.get("operation", "generated")
        self._display_system_message("NEURAL", f"File {operation}: {file_path}")

    def _handle_code_generated(self, event):
        """Handle code generation completion."""
        file_path = event.payload.get("file_path", "file")
        if self.thinking_indicator.knight_rider.is_animating:
            self.thinking_indicator.set_thinking_message(f"AURA completed: {file_path}")
        
        # Display system message
        self._display_system_message("NEURAL", f"Code generation complete: {file_path}")

    def _display_system_message(self, category: str, message: str):
        """Display a system message in the terminal style."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        system_html = f"""
        <div style="margin: 8px 0; font-family: 'JetBrains Mono', monospace;">
            <span style="color: #39FF14; font-weight: bold;">[{category}]</span> 
            <span style="color: #FFB74D;">{message}</span>
        </div>
        """
        
        self.chat_display.append(system_html)
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.ensureCursorVisible()

    def _update_child_window_positions(self):
        """Updates the position of all attached child windows."""
        self._update_task_log_position()
        self._update_code_viewer_position()

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

    def _update_code_viewer_position(self):
        """Updates the position of the code viewer window to be pinned to the right of the task log."""
        if not self.isVisible() or not self.code_viewer_window or not self.code_viewer_window.isVisible() or not self.task_log_window or not self.task_log_window.isVisible():
            return

        task_log_pos = self.task_log_window.pos()
        task_log_width = self.task_log_window.width()
        gap = 8  # A small gap between windows

        new_x = task_log_pos.x() + task_log_width + gap
        new_y = task_log_pos.y()

        self.code_viewer_window.move(new_x, new_y)
        self.code_viewer_window.resize(self.code_viewer_window.width(), self.height())

    def moveEvent(self, event):
        """Override moveEvent to move child windows along with the main window."""
        super().moveEvent(event)
        self._update_child_window_positions()

    def resizeEvent(self, event):
        """Override resizeEvent to adjust child windows' position and height."""
        super().resizeEvent(event)
        self._update_child_window_positions()

    def showEvent(self, event):
        """Override showEvent to position child windows when the main window is first shown."""
        super().showEvent(event)
        QTimer.singleShot(0, self._update_child_window_positions)

    def closeEvent(self, event):
        """Ensure the entire application quits when the main window is closed."""
        QApplication.quit()
        super().closeEvent(event)

    def _handle_project_activated(self, event):
        """Handle project activation events."""
        project_name = event.payload.get("project_name", "Unknown")
        project_path = event.payload.get("project_path", "")
        self._display_system_message("WORKSPACE", f"Project '{project_name}' activated and indexed")

    def _handle_project_imported(self, event):
        """Handle successful project import events."""
        project_name = event.payload.get("project_name", "Unknown")
        source_path = event.payload.get("source_path", "")
        self._display_system_message("WORKSPACE", f"Project '{project_name}' imported successfully from {source_path}")

    def _handle_project_import_error(self, event):
        """Handle project import error events."""
        error = event.payload.get("error", "Unknown error")
        self.chat_display.append(f"<span style='color: #FF0000;'>[ERROR] Project import failed: {error}</span>")

    # ═══════════════════════════════════════════════════════════════════════════════════════
    # AURA COMMAND DECK: ANIMATED TERMINAL SYSTEM
    # ═══════════════════════════════════════════════════════════════════════════════════════
    
    def _handle_workflow_status_update(self, event):
        """
        Aura Command Deck: Handle incoming workflow status updates by queuing them for animation.
        """
        message = event.payload.get("message", "")
        status = event.payload.get("status", "info")
        
        if message:
            # Add to queue for animated typing
            self.status_message_queue.append((message, status))
            
            # Start typing animation if not already running
            if not self.status_typing_timer.isActive():
                self._start_next_status_message()
    
    def _start_next_status_message(self):
        """
        Aura Command Deck: Start typing the next message in the queue.
        """
        if not self.status_message_queue:
            return
            
        # Get next message from queue
        message, status = self.status_message_queue.popleft()
        self.current_status_message = message
        self.current_status_class = f"status-{status}"
        self.current_char_index = 0
        
        # Map status to icon
        status_icons = {
            "success": "🟢",
            "in-progress": "🟡", 
            "error": "🔴",
            "info": "🔵"
        }
        icon = status_icons.get(status, "⚪")
        
        # Insert the initial HTML container with icon
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(f'<div class="{self.current_status_class}">{escape(icon)} ')
        self.chat_display.setTextCursor(cursor)
        
        # Start the character-by-character typing
        self.status_typing_timer.start(self.STATUS_TYPING_SPEED)
    
    def _type_next_status_char(self):
        """
        Aura Command Deck: Type the next character of the current status message.
        This is the core animation method that creates the hacker terminal effect.
        """
        if self.current_char_index >= len(self.current_status_message):
            # Current message is complete
            cursor = self.chat_display.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertHtml('</div>')
            self.chat_display.setTextCursor(cursor)
            
            # Auto-scroll to bottom to keep status updates visible
            scrollbar = self.chat_display.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            
            self.status_typing_timer.stop()
            
            # Start next message if queue has more
            if self.status_message_queue:
                self._start_next_status_message()
            return
        
        # Type the next character
        char = self.current_status_message[self.current_char_index]
        escaped_char = escape(char)  # Handle HTML special characters safely
        
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(escaped_char)
        self.chat_display.setTextCursor(cursor)
        
        # Auto-scroll to keep typing visible
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        self.current_char_index += 1