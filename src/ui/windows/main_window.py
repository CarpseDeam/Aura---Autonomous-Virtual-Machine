import logging
import textwrap
from typing import List, Optional
import markdown

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QTextEdit, QHBoxLayout,
    QApplication, QPushButton, QFileDialog
)
from PySide6.QtGui import QFont, QTextCursor, QIcon, QColor, QTextCharFormat, QTextOption
from PySide6.QtCore import Qt, QTimer, Signal, QObject

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.config import ASSETS_DIR
from src.ui.widgets.chat_input import ChatInputTextEdit
from src.ui.windows.settings_window import SettingsWindow
from src.ui.widgets.knight_rider_widget import ThinkingIndicator


logger = logging.getLogger(__name__)

# Retro CSS stylesheet for AURA's markdown-rendered responses
AURA_RESPONSE_CSS = """
<style>
    body { 
        font-family: 'JetBrains Mono', monospace; 
        font-size: 13px; 
        color: #dcdcdc; 
        background: transparent; 
        margin: 2px 0; 
        line-height: 1.4;
    }
    h1, h2, h3, h4, h5, h6 { 
        color: #FFB74D; 
        font-weight: bold; 
        margin: 8px 0 4px 0; 
    }
    h1 { font-size: 16px; }
    h2 { font-size: 15px; }
    h3 { font-size: 14px; }
    p { 
        margin: 4px 0; 
        color: #dcdcdc; 
    }
    ul, ol { 
        margin: 4px 0; 
        padding-left: 20px; 
    }
    li { 
        margin: 2px 0; 
        color: #dcdcdc; 
    }
    code { 
        background-color: #2a2a2a; 
        color: #64B5F6; 
        padding: 1px 4px; 
        border-radius: 2px; 
        font-family: 'JetBrains Mono', monospace; 
        font-size: 12px; 
    }
    pre { 
        background-color: #1e1e1e; 
        color: #dcdcdc; 
        padding: 8px; 
        border-radius: 4px; 
        border-left: 3px solid #FFB74D; 
        margin: 8px 0; 
        white-space: pre-wrap; 
        font-family: 'JetBrains Mono', monospace; 
        font-size: 12px; 
        overflow-x: auto; 
    }
    pre code { 
        background: transparent; 
        padding: 0; 
    }
    blockquote { 
        border-left: 3px solid #64B5F6; 
        margin: 8px 0; 
        padding-left: 12px; 
        color: #b0b0b0; 
    }
    strong, b { 
        color: #FFB74D; 
        font-weight: bold; 
    }
    em, i { 
        color: #64B5F6; 
        font-style: italic; 
    }
    a { 
        color: #64B5F6; 
        text-decoration: underline; 
    }
</style>
"""


class Signaller(QObject):
    chunk_received = Signal(str)
    stream_ended = Signal()
    error_received = Signal(str)





class MainWindow(QMainWindow):
    """Main window with a Modern Retro Terminal log and typewriter output."""

    AURA_ASCII_BANNER = """
        █████╗ ██╗   ██╗██████╗  █████╗
       ██╔══██╗██║   ██║██╔══██╗██╔══██╗
       ███████║██║   ██║██████╔╝███████║
       ██╔══██║██║   ██║██╔══██╗██╔══██║
       ██║  ██║╚██████╔╝██║  ██║██║  ██║
       ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝
      A U T O N O M O U S  V I R T U A L  M A C H I N E
    """

    AURA_STYLESHEET = """
        QMainWindow, QWidget {
            background-color: #000000;
            color: #dcdcdc;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
        }
        QLabel#aura_banner {
            color: #FFB74D;
            font-weight: bold;
            font-size: 10px;
            padding-bottom: 10px;
        }
        QTextEdit#chat_display {
            background-color: #000000;
            border-top: 1px solid #4a4a4a;
            border-bottom: none;
            color: #dcdcdc;
            font-size: 14px;
        }
        QTextEdit#chat_input {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a;
            color: #dcdcdc;
            font-size: 14px;
            padding: 8px;
            border-radius: 5px;
            max-height: 80px;
        }
        QTextEdit#chat_input:focus { border: 1px solid #4a4a4a; }
        QPushButton#top_bar_button {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a;
            color: #dcdcdc;
            font-size: 14px;
            font-weight: bold;
            padding: 8px 12px;
            border-radius: 5px;
            min-width: 150px;
        }
        QPushButton#top_bar_button:hover { background-color: #3a3a3a; }
    """

    BOOT_SEQUENCE = [
        {"text": "[SYSTEM] AURA Command Deck Initialized"},
        {"text": "Status: READY"},
        {"text": "System: Online"},
        {"text": "Mode: Interactive"},
        {"text": "Enter your commands..."},
    ]

    def __init__(self, event_bus: EventBus):
        super().__init__()
        self.event_bus = event_bus
        self.code_viewer_window = None
        self.settings_window = None
        self.setWindowTitle("Aura - Command Deck")
        self.setGeometry(100, 100, 700, 820)

        self.setStyleSheet(self.AURA_STYLESHEET)
        self._set_window_icon()

        self.is_streaming_response = False
        self.signaller = Signaller()
        
        # New animation system variables
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(20)  # 20ms for smooth animation
        self.animation_timer.timeout.connect(self._on_animation_tick)
        self.full_response_buffer = ""
        self.displayed_text_buffer = ""
        self.animation_index = 0

        self._init_ui()
        self._register_event_handlers()
        self._start_boot_sequence()

    def _set_window_icon(self):
        icon_path = ASSETS_DIR / "aura_gear_icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        banner_label = QLabel(self.AURA_ASCII_BANNER)
        banner_label.setObjectName("aura_banner")
        banner_label.setFont(QFont("JetBrains Mono", 10))
        banner_label.setAlignment(Qt.AlignCenter)

        self.chat_display = QTextEdit()
        # Prevent chat display from receiving keyboard focus to avoid
        # conflicts between user selection and the typewriter cursor
        self.chat_display.setFocusPolicy(Qt.NoFocus)
        self.chat_display.setObjectName("chat_display")
        # Ensure word wrapping occurs at word boundaries for readability
        self.chat_display.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.chat_display.setReadOnly(True)

        self.thinking_indicator = ThinkingIndicator()

        input_container = self._create_input_area()

        layout.addWidget(self._create_top_bar())
        layout.addWidget(banner_label)
        layout.addWidget(self.chat_display, 1)
        layout.addWidget(self.thinking_indicator)
        layout.addWidget(input_container)

    def _create_top_bar(self):
        widget = QWidget()
        hl = QHBoxLayout(widget)
        hl.setContentsMargins(0, 0, 0, 10)

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

        hl.addWidget(btn_new_session)
        hl.addStretch()
        hl.addWidget(btn_code_workspace)
        hl.addWidget(btn_import_project)
        hl.addWidget(btn_configure_agents)
        return widget

    def _create_input_area(self):
        container = QWidget()
        hl = QHBoxLayout(container)
        hl.setContentsMargins(0, 0, 0, 0)

        self.chat_input = ChatInputTextEdit()
        self.chat_input.setObjectName("chat_input")
        self.chat_input.setPlaceholderText("Type here. Shift+Enter for newline. Enter to send.")
        self.chat_input.sendMessage.connect(self._send_message)

        hl.addWidget(self.chat_input, 1)
        return container

    def _register_event_handlers(self):
        self.signaller.chunk_received.connect(self._handle_model_chunk)
        self.signaller.stream_ended.connect(self._handle_stream_end)
        self.signaller.error_received.connect(self._handle_model_error)

        self.event_bus.subscribe("MODEL_CHUNK_RECEIVED", lambda e: self.signaller.chunk_received.emit(e.payload.get("chunk", "")))
        self.event_bus.subscribe("MODEL_STREAM_ENDED", lambda e: self.signaller.stream_ended.emit())
        self.event_bus.subscribe("MODEL_ERROR", lambda e: self.signaller.error_received.emit(e.payload.get("message", "Unknown error")))

        self.event_bus.subscribe("DISPATCH_TASK", self._handle_task_dispatch)
        # Legacy UI status updates are deprecated in favor of MainWindow-driven narrative logging
        self.event_bus.subscribe("WORKFLOW_STATUS_UPDATE", self._handle_workflow_status_update)

        self.event_bus.subscribe("PROJECT_ACTIVATED", self._handle_project_activated)
        self.event_bus.subscribe("PROJECT_IMPORTED", self._handle_project_imported)
        self.event_bus.subscribe("PROJECT_IMPORT_ERROR", self._handle_project_import_error)
        self.event_bus.subscribe("VALIDATED_CODE_SAVED", self._handle_validated_code_saved)
        # New: listen for generated blueprints to display summary
        self.event_bus.subscribe("BLUEPRINT_GENERATED", self._handle_blueprint_generated)
        # Build lifecycle completion signal
        self.event_bus.subscribe("BUILD_COMPLETED", self._handle_build_completed)


    # Boot
    def _start_boot_sequence(self):
        self.chat_display.clear()
        for item in self.BOOT_SEQUENCE:
            text = item.get("text", "")
            if text:
                boot_html = f'<div style="color: #FFB74D; font-family: JetBrains Mono, monospace; font-size: 13px; margin: 2px 0;">{text}</div><br>'
                self.chat_display.insertHtml(boot_html)
        self.chat_display.ensureCursorVisible()

    def _log_system_message(self, category: str, message: str):
        """Display system messages instantly with appropriate colors."""
        # Color map for different categories
        color_map = {
            "KERNEL": "#64B5F6",   # futuristic blue
            "SYSTEM": "#66BB6A",   # informative green
            "NEURAL": "#FFB74D",   # amber/orange
            "SUCCESS": "#39FF14",  # bright green
            "ERROR": "#FF4444",    # alert red
            "WORKSPACE": "#64B5F6",
            "USER": "#64B5F6",
            "DEFAULT": "#dcdcdc",
        }
        color = color_map.get(category.upper(), color_map["DEFAULT"])
        
        processed_message = message.replace('\n', '<br>').replace(' ', '&nbsp;')
        system_html = f"""
        <div style="color: {color}; font-family: JetBrains Mono, monospace; font-size: 13px; margin: 2px 0;">
            [{category}] {processed_message}
        </div>
        <br>
        """
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.insertHtml(system_html)
        self.chat_display.ensureCursorVisible()

    def _start_new_session(self):
        self.event_bus.dispatch(Event(event_type="NEW_SESSION_REQUESTED"))
        self._start_boot_sequence()

    # Actions
    def _open_settings_dialog(self):
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.event_bus)
        self.settings_window.show()

    def _open_code_workspace(self):
        if self.code_viewer_window:
            self.code_viewer_window.show()
            QTimer.singleShot(0, self._update_code_viewer_position)

    def _import_project(self):
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setWindowTitle("Import Project - Select Directory")
        if dialog.exec():
            selected_dirs = dialog.selectedFiles()
            if selected_dirs:
                project_path = selected_dirs[0]
                logger.info(f"User selected project for import: {project_path}")
                self.event_bus.dispatch(Event(event_type="IMPORT_PROJECT_REQUESTED", payload={"path": project_path}))
                self._display_system_message("WORKSPACE", f"Importing project from: {project_path}")

    # Input/Output
    def _log_user_message(self, user_text: str):
        """Display user message instantly using HTML."""
        # Create styled HTML for user message - single paragraph with spans to prevent line breaks
        processed_user_text = user_text.replace('\n', '<br>').replace(' ', '&nbsp;')
        user_html = f"""
        <p style="color: #64B5F6; font-family: JetBrains Mono, monospace; font-size: 13px; margin: 2px 0;">
            <span style="font-weight: bold;">[USER]</span> <span>{processed_user_text}</span>
        </p>
        <br>
        """
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.insertHtml(user_html)
        self.chat_display.ensureCursorVisible()

    def _render_aura_response(self, response_text: str):
        """Render AURA's response using Markdown-to-HTML conversion with retro styling."""
        # Convert markdown to HTML
        html_content = markdown.markdown(response_text, extensions=['fenced_code', 'codehilite'])
        
        # Create complete HTML document with CSS styling
        styled_html = f"""
        <div>
            {AURA_RESPONSE_CSS}
            <div style="margin-bottom: 8px;">
                {html_content}
            </div>
        </div>
        """
        
        # Append the styled HTML to chat display
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.insertHtml(styled_html)
        self.chat_display.ensureCursorVisible()

    def _send_message(self):
        user_text = self.chat_input.toPlainText().strip()
        if not user_text:
            return
        self.chat_input.clear()
        self.chat_input.setEnabled(False)

        # Display user message instantly
        self._log_user_message(user_text)

        self.thinking_indicator.start_thinking("Analyzing your request...")
        self.event_bus.dispatch(Event(event_type="SEND_USER_MESSAGE", payload={"text": user_text}))

    def _handle_model_chunk(self, chunk: str):
        if not self.is_streaming_response:
            self.is_streaming_response = True
            self.thinking_indicator.stop_thinking()
            # Clear the response buffer for new response
            self.full_response_buffer = ""
        
        # Only buffer the text, don't touch the UI
        self.full_response_buffer += chunk

    def _handle_stream_end(self):
        if self.is_streaming_response and self.full_response_buffer.strip():
            # Add the colored [AURA] tag to show who is speaking
            aura_label_html = f'<div style="color: #FFB74D; font-family: JetBrains Mono, monospace; font-size: 13px; margin: 2px 0; font-weight: bold;">[AURA]</div>'
            self.chat_display.moveCursor(QTextCursor.End)
            self.chat_display.insertHtml(aura_label_html)
            
            # Render the complete response using markdown-to-HTML conversion
            self._render_aura_response(self.full_response_buffer.strip())
            
            # Clear the buffer and finish up
            self.full_response_buffer = ""
            
        # Finish streaming state
        self.is_streaming_response = False
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()

    def _handle_model_error(self, error_message: str):
        self.thinking_indicator.stop_thinking()
        # Display error message instantly with red color
        processed_error_message = error_message.replace('\n', '<br>').replace(' ', '&nbsp;')
        error_html = f"""
        <div style="color: #FF4444; font-family: JetBrains Mono, monospace; font-size: 13px; margin: 2px 0;">
            [ERROR] {processed_error_message}
        </div>
        <br>
        """
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.insertHtml(error_html)
        self.chat_display.ensureCursorVisible()
        
        # Reset state
        self.is_streaming_response = False
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()

    # Workflow/system events
    def _handle_task_dispatch(self, event):
        if self.thinking_indicator.knight_rider.is_animating:
            self.thinking_indicator.set_thinking_message("Engineering your solution...")
        desc = event.payload.get("task_description", "Task")
        self._display_system_message("SYSTEM", f"Task dispatched: {desc}")

    def _handle_agent_started(self, event):
        agent_name = event.payload.get("agent_name", "Agent")
        self._display_system_message("KERNEL", f"{agent_name.upper()} ONLINE")

    def _handle_agent_completed(self, event):
        agent_name = event.payload.get("agent_name", "Agent")
        status = event.payload.get("status", "completed")
        self._display_system_message("KERNEL", f"{agent_name.upper()} task {status.upper()}")

    def _handle_task_completed(self, event):
        desc = event.payload.get("task_description", "Task")
        self._display_system_message("SYSTEM", f"Task completed: {desc}")

    def _handle_file_generated(self, event):
        file_path = event.payload.get("file_path", "unknown")
        operation = event.payload.get("operation", "generated")
        self._display_system_message("NEURAL", f"File {operation}: {file_path}")

    

    def _display_system_message(self, category: str, message: str):
        self._log_system_message(category, message)

    def _handle_workflow_status_update(self, event):
        message = event.payload.get("message", "")
        status = event.payload.get("status", "info")
        details = event.payload.get("details")  # optional list[str]
        code_snippet = event.payload.get("code_snippet")  # optional str
        if not message:
            return
            
        # Map status to categories for any legacy events that still arrive
        status_to_category = {
            "success": "SUCCESS",
            "in-progress": "SYSTEM",
            "error": "ERROR",
            "info": "SYSTEM",
        }
        category = status_to_category.get(status, "SYSTEM")
        
        # Display parent message
        self._log_system_message(category, message)
        
        # Optional code snippet as indented child lines
        if code_snippet:
            for line in code_snippet.splitlines():
                self._log_system_message("DEFAULT", f"  {line}")
                
        # Optional detail lines
        if details:
            for d in details:
                self._log_system_message("DEFAULT", f"  {d}")

    # Mission Control manual dispatch has been removed; build starts automatically.

    # Workspace events
    def _handle_project_activated(self, event):
        project_name = event.payload.get("project_name", "Unknown")
        self._display_system_message("WORKSPACE", f"Project '{project_name}' activated and indexed")

    def _handle_project_imported(self, event):
        project_name = event.payload.get("project_name", "Unknown")
        source_path = event.payload.get("source_path", "")
        self._display_system_message("WORKSPACE", f"Project '{project_name}' imported from {source_path}")

    def _handle_project_import_error(self, event):
        error = event.payload.get("error", "Unknown error")
        self._log_system_message("ERROR", f"Project import failed: {error}")

    def _handle_validated_code_saved(self, event):
        file_path = event.payload.get("file_path", "unknown")
        line_count = event.payload.get("line_count")
        if line_count is None:
            # Fallback: do not attempt to read file from disk here; just omit count
            self._log_system_message("SUCCESS", f"Saved {file_path}")
        else:
            self._log_system_message("SUCCESS", f"Wrote {line_count} lines to {file_path}")

    def _handle_build_completed(self, event):
        # Stop any thinking animation, show final success, and re-enable input
        self.thinking_indicator.stop_thinking()
        self._log_system_message("SUCCESS", "Build completed successfully. Aura is ready.")
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()

    # Child window positioning
    def _update_child_window_positions(self):
        self._update_code_viewer_position()

    def _update_code_viewer_position(self):
        if not self.isVisible() or not self.code_viewer_window or not self.code_viewer_window.isVisible():
            return
        main_pos = self.pos()
        new_x = main_pos.x() + self.width() + 8
        new_y = main_pos.y()
        self.code_viewer_window.move(new_x, new_y)
        self.code_viewer_window.resize(self.code_viewer_window.width(), self.height())

    def moveEvent(self, event):
        super().moveEvent(event)
        self._update_child_window_positions()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_child_window_positions()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._update_child_window_positions)

    def closeEvent(self, event):
        QApplication.quit()
        super().closeEvent(event)

    # ------------------- Blueprint UI -------------------
    def _handle_blueprint_generated(self, event: Event):
        """Log a concise, plain-text blueprint summary to the terminal UI."""
        blueprint_data = event.payload or {}
        files = blueprint_data.get("files") or []
        file_count = len([f for f in files if isinstance(f, dict)])
        total_tasks = (
            sum(len(((f or {}).get("functions") or [])) for f in files)
            + sum(len(((c or {}).get("methods") or [])) for f in files for c in ((f or {}).get("classes") or []))
        )
        project_name = blueprint_data.get("project_name", "Project")
        self._display_system_message(
            "SYSTEM",
            f"Blueprint for '{project_name}' generated: {file_count} files, {total_tasks} tasks."
        )

    
