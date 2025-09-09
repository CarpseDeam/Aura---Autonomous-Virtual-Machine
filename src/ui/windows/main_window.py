import logging
import html
from typing import List, Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QTextEdit, QHBoxLayout,
    QApplication, QPushButton, QFileDialog
)
from PySide6.QtGui import QFont, QTextCursor, QIcon, QColor, QTextCharFormat
from PySide6.QtCore import Qt, QTimer, Signal, QObject

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.config import ASSETS_DIR
from src.ui.widgets.chat_input import ChatInputTextEdit
from src.ui.windows.settings_window import SettingsWindow
from src.ui.widgets.knight_rider_widget import ThinkingIndicator


logger = logging.getLogger(__name__)


class Signaller(QObject):
    chunk_received = Signal(str)
    stream_ended = Signal()
    error_received = Signal(str)


class TypewriterTerminal(QObject):
    """Typewriter engine that renders lines with a blinking block cursor."""

    def __init__(self, text_edit: QTextEdit, parent=None):
        super().__init__(parent)
        self.text_edit = text_edit
        self.queue: List[dict] = []  # {open_html, text, close_html}
        self.current: Optional[dict] = None
        self.char_index = 0

        self.typing_timer = QTimer(self)
        self.typing_timer.setInterval(12)
        self.typing_timer.timeout.connect(self._on_type_tick)

        self.cursor_visible = False
        self.cursor_timer = QTimer(self)
        self.cursor_timer.setInterval(500)
        self.cursor_timer.timeout.connect(self._toggle_cursor)

        self.streaming_open = False
        self.streaming_close_html = ""
        # Category color map for non-HTML typewriter coloring
        self.category_colors = {
            "KERNEL": "#64B5F6",   # futuristic blue
            "SYSTEM": "#66BB6A",   # informative green
            "NEURAL": "#FFB74D",   # amber/orange
            "SUCCESS": "#39FF14",  # bright green
            "ERROR": "#FF4444",    # alert red
            "WORKSPACE": "#64B5F6",
            "USER": "#64B5F6",
            "DEFAULT": "#dcdcdc",
        }

    def start(self):
        if not self.cursor_timer.isActive():
            self.cursor_timer.start()

    def _remove_cursor_if_present(self):
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, 1)
        if cursor.selectedText() == "█":
            cursor.removeSelectedText()
            self.cursor_visible = False

    def _append_html(self, html_str: str):
        self._remove_cursor_if_present()
        self.text_edit.moveCursor(QTextCursor.End)
        self.text_edit.insertHtml(html_str)
        self._ensure_cursor()

    def _append_text_escaped(self, text: str):
        self._remove_cursor_if_present()
        self.text_edit.moveCursor(QTextCursor.End)
        self.text_edit.insertHtml(text)
        self._ensure_cursor()

    def _ensure_cursor(self):
        if not self.cursor_visible:
            self.text_edit.insertPlainText("█")
            self.cursor_visible = True
        self.text_edit.moveCursor(QTextCursor.End)
        self.text_edit.ensureCursorVisible()

    def _toggle_cursor(self):
        self._remove_cursor_if_present()
        if not self.cursor_visible:
            self.text_edit.insertPlainText("█")
            self.cursor_visible = True
        else:
            self.cursor_visible = False
        self.text_edit.moveCursor(QTextCursor.End)

    def queue_line(self, text: str, color: str, level: int = 0):
        indent = 20 * max(level, 0)
        open_html = (
            f"<div style='margin: 2px 0 2px {indent}px; color: {color};"
            f" font-family: \"JetBrains Mono\", monospace; font-size: 13px;'>"
            f"<span style='color: {color};'>●</span> "
        )
        close_html = "</div><br/>"
        self._queue_segment(open_html, text, close_html)

    def open_stream_block(self, color: str, level: int = 0):
        if self.streaming_open:
            return
        indent = 20 * max(level, 0)
        open_html = (
            f"<div style='margin: 2px 0 2px {indent}px; color: {color};"
            f" font-family: \"JetBrains Mono\", monospace; font-size: 13px;'>"
        )
        self._append_html(open_html)
        self.streaming_open = True
        self.streaming_close_html = "</div>"

    def append_stream_text(self, text: str):
        if not text:
            return
        self._queue_segment(None, text, None)

    def close_stream_block(self):
        if self.streaming_open:
            if not self.typing_timer.isActive():
                self._append_html(self.streaming_close_html + "<br/>")
            else:
                self._queue_segment(None, "", self.streaming_close_html + "<br/>")
            self.streaming_open = False
            self.streaming_close_html = ""

    def _queue_segment(self, open_html: Optional[str], text: str, close_html: Optional[str]):
        # HTML mode segment (used for streaming blocks)
        self.queue.append({
            "mode": "html",
            "open_html": open_html,
            "text": text,
            "close_html": close_html,
        })
        if not self.typing_timer.isActive():
            self._advance_queue()
            self.typing_timer.start()
        self.start()

    def _queue_plain_segment(self, text: str, color_hex: str):
        """Queue a plain-text segment (no HTML), colored by category."""
        self.queue.append({
            "mode": "plain",
            "text": text,
            "color": color_hex,
        })
        if not self.typing_timer.isActive():
            self._advance_queue()
            self.typing_timer.start()
        self.start()

    def _advance_queue(self):
        if not self.queue:
            self.current = None
            self.char_index = 0
            self.typing_timer.stop()
            self._ensure_cursor()
            return
        self.current = self.queue.pop(0)
        self.char_index = 0
        if self.current.get("open_html"):
            self._append_html(self.current["open_html"])

    def _on_type_tick(self):
        if not self.current:
            self._advance_queue()
            return

        text = self.current.get("text", "")
        if self.char_index < len(text):
            ch = text[self.char_index]
            mode = self.current.get("mode")
            if mode == "plain":
                # Insert raw text with QTextCharFormat color; no HTML entities
                color_hex = self.current.get("color", "#dcdcdc")
                self._remove_cursor_if_present()
                cursor = self.text_edit.textCursor()
                cursor.movePosition(QTextCursor.End)
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color_hex))
                if ch == "\n":
                    cursor.insertText("\n")
                else:
                    cursor.insertText(ch, fmt)
                self._ensure_cursor()
            else:
                # HTML stream: escape per character
                if ch == "\n":
                    out = "<br>"
                elif ch == " ":
                    out = "&nbsp;"
                elif ch == "<":
                    out = "&lt;"
                elif ch == ">":
                    out = "&gt;"
                elif ch == "&":
                    out = "&amp;"
                else:
                    out = ch
                self._append_text_escaped(out)
            self.char_index += 1
            return

        if self.current.get("close_html"):
            self._append_html(self.current["close_html"])
        self._advance_queue()

    # Backward- and forward-compatible queue_line:
    # - New form: queue_line(message, category)
    # - Legacy form: queue_line(text, color, level=0)
    def queue_line(self, message: str, category: str, level: int = 0):
        color = category
        if not (isinstance(category, str) and category.startswith("#")):
            color = self.category_colors.get((category or "").upper(), self.category_colors["DEFAULT"])
        text = message if message.endswith("\n") else message + "\n"
        self._queue_plain_segment(text, color)


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
        self.dispatch_button = None
        self.tasks_available = False

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
        self.chat_display.setObjectName("chat_display")
        self.chat_display.setReadOnly(True)

        self.thinking_indicator = ThinkingIndicator()

        input_container = self._create_input_area()

        layout.addWidget(self._create_top_bar())
        layout.addWidget(banner_label)
        layout.addWidget(self.chat_display, 1)
        layout.addWidget(self.thinking_indicator)
        layout.addWidget(input_container)

        self.typewriter = TypewriterTerminal(self.chat_display, self)
        self.typewriter.start()

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

        self.dispatch_button = QPushButton("Dispatch All Tasks")
        self.dispatch_button.setObjectName("top_bar_button")
        self.dispatch_button.clicked.connect(self._dispatch_all_tasks)
        self.dispatch_button.setEnabled(False)

        hl.addWidget(self.chat_input, 1)
        hl.addWidget(self.dispatch_button)
        return container

    def _register_event_handlers(self):
        self.signaller.chunk_received.connect(self._handle_model_chunk)
        self.signaller.stream_ended.connect(self._handle_stream_end)
        self.signaller.error_received.connect(self._handle_model_error)

        self.event_bus.subscribe("MODEL_CHUNK_RECEIVED", lambda e: self.signaller.chunk_received.emit(e.payload.get("chunk", "")))
        self.event_bus.subscribe("MODEL_STREAM_ENDED", lambda e: self.signaller.stream_ended.emit())
        self.event_bus.subscribe("MODEL_ERROR", lambda e: self.signaller.error_received.emit(e.payload.get("message", "Unknown error")))

        self.event_bus.subscribe("DISPATCH_TASK", self._handle_task_dispatch)
        self.event_bus.subscribe("CODE_GENERATED", self._handle_code_generated)
        # Legacy UI status updates are deprecated in favor of MainWindow-driven narrative logging
        self.event_bus.subscribe("WORKFLOW_STATUS_UPDATE", self._handle_workflow_status_update)
        self.event_bus.subscribe("TASK_LIST_UPDATED", self._handle_task_list_updated)

        self.event_bus.subscribe("PROJECT_ACTIVATED", self._handle_project_activated)
        self.event_bus.subscribe("PROJECT_IMPORTED", self._handle_project_imported)
        self.event_bus.subscribe("PROJECT_IMPORT_ERROR", self._handle_project_import_error)
        self.event_bus.subscribe("VALIDATED_CODE_SAVED", self._handle_validated_code_saved)
        # New: listen for generated blueprints to display summary
        self.event_bus.subscribe("BLUEPRINT_GENERATED", self._handle_blueprint_generated)

    # Boot
    def _start_boot_sequence(self):
        self.chat_display.clear()
        for item in self.BOOT_SEQUENCE:
            text = item.get("text", "")
            if text:
                self.typewriter.queue_line(text, "NEURAL")
        self.typewriter.start()

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
    def _send_message(self):
        user_text = self.chat_input.toPlainText().strip()
        if not user_text:
            return
        self.chat_input.clear()
        self.chat_input.setEnabled(False)

        # User parent + child
        self.typewriter.queue_line("[USER]", "KERNEL")
        self.typewriter.queue_line(user_text, "DEFAULT")
        self.typewriter.start()

        self.thinking_indicator.start_thinking("Analyzing your request...")
        self.event_bus.dispatch(Event(event_type="SEND_USER_MESSAGE", payload={"text": user_text}))

    def _handle_model_chunk(self, chunk: str):
        if not self.is_streaming_response:
            self.is_streaming_response = True
            self.thinking_indicator.stop_thinking()
            self.typewriter.queue_line("[AURA]", "NEURAL")
            self.typewriter.open_stream_block(color="#dcdcdc", level=1)
        self.typewriter.append_stream_text(chunk)
        self.typewriter.start()

    def _handle_stream_end(self):
        if self.is_streaming_response:
            self.typewriter.close_stream_block()
        self.is_streaming_response = False
        self.chat_input.setEnabled(True)
        self.chat_input.setFocus()
        self.typewriter.start()

    def _handle_model_error(self, error_message: str):
        self.thinking_indicator.stop_thinking()
        self.typewriter.queue_line(f"[ERROR] {error_message}", "ERROR")
        self._handle_stream_end()

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

    def _handle_code_generated(self, event):
        file_path = event.payload.get("file_path", "file")
        if self.thinking_indicator.knight_rider.is_animating:
            self.thinking_indicator.set_thinking_message(f"Completed: {file_path}")
        self._display_system_message("NEURAL", f"Code generation complete: {file_path}")

    def _display_system_message(self, category: str, message: str):
        self.typewriter.queue_line(f"[{category}] {message}", category)
        self.typewriter.start()

    def _handle_workflow_status_update(self, event):
        message = event.payload.get("message", "")
        status = event.payload.get("status", "info")
        details = event.payload.get("details")  # optional list[str]
        code_snippet = event.payload.get("code_snippet")  # optional str
        if not message:
            return
        palette = {
            "success": "#39FF14",
            "in-progress": "#FFB74D",
            "error": "#FF4444",
            "info": "#64B5F6",
        }
        # Map status to categories for any legacy events that still arrive
        status_to_category = {
            "success": "SUCCESS",
            "in-progress": "SYSTEM",
            "error": "ERROR",
            "info": "SYSTEM",
        }
        category = status_to_category.get(status, "SYSTEM")
        # Parent command line
        self.typewriter.queue_line(message, category)
        # Optional code snippet as indented child lines (kept simple for clarity)
        if code_snippet:
            for line in code_snippet.splitlines():
                self.typewriter.queue_line(line, "DEFAULT")
        # Optional detail lines
        if details:
            for d in details:
                self.typewriter.queue_line(d, "DEFAULT")
        self.typewriter.start()

    # Mission Control: dispatch
    def _dispatch_all_tasks(self):
        if not self.tasks_available:
            return
        self.dispatch_button.setEnabled(False)
        self.dispatch_button.setText("Building...")
        self.event_bus.dispatch(Event(event_type="DISPATCH_ALL_TASKS"))
        logger.info("Mission Control: All tasks dispatched for execution")
        self.typewriter.queue_line("[SYSTEM] Build sequence initiated. Executing blueprint...", "SYSTEM")
        self.typewriter.start()

    def _handle_task_list_updated(self, event):
        tasks = event.payload.get("tasks", [])
        pending = [t for t in tasks if t.get("status") == "PENDING"]
        has_pending = len(pending) > 0
        if has_pending != self.tasks_available:
            self.tasks_available = has_pending
            if self.dispatch_button:
                self.dispatch_button.setEnabled(has_pending)
                if has_pending:
                    self.dispatch_button.setText(f"Dispatch {len(pending)} Tasks")
                else:
                    self.dispatch_button.setText("Dispatch All Tasks")

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
        self.typewriter.queue_line(f"[ERROR] Project import failed: {error}", "ERROR")
        self.typewriter.start()

    def _handle_validated_code_saved(self, event):
        file_path = event.payload.get("file_path", "unknown")
        line_count = event.payload.get("line_count")
        if line_count is None:
            # Fallback: do not attempt to read file from disk here; just omit count
            self.typewriter.queue_line(f"[SUCCESS] Saved {file_path}", "SUCCESS")
        else:
            self.typewriter.queue_line(f"[SUCCESS] Wrote {line_count} lines to {file_path}", "SUCCESS")
        self.typewriter.start()

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

    
