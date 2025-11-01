import logging
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import markdown

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QTextBrowser, QHBoxLayout,
    QApplication, QPushButton, QFileDialog, QInputDialog
)
from PySide6.QtGui import QFont, QTextCursor, QIcon, QTextOption, QDesktopServices
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QUrl, QUrlQuery

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.config import ASSETS_DIR
from src.aura.services.user_settings_manager import get_auto_accept_changes
from src.aura.services.image_storage_service import ImageStorageService
from src.ui.widgets.chat_input import ChatInputTextEdit
from src.ui.windows.settings_window import SettingsWindow
from src.ui.widgets.knight_rider_widget import ThinkingIndicator


logger = logging.getLogger(__name__)

# Retro CSS stylesheet for AURA's markdown-rendered responses
AURA_RESPONSE_CSS = """
<style>
    .aura-response-content {
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        color: #FFB74D;
        background: transparent;
        line-height: 1.4;
        word-wrap: break-word;
    }
    .aura-response-content h1,
    .aura-response-content h2,
    .aura-response-content h3,
    .aura-response-content h4,
    .aura-response-content h5,
    .aura-response-content h6 {
        color: #FFD27F;
        font-weight: bold;
        margin: 8px 0 4px 0;
    }
    .aura-response-content h1 { font-size: 16px; }
    .aura-response-content h2 { font-size: 15px; }
    .aura-response-content h3 { font-size: 14px; }
    .aura-response-content p {
        margin: 4px 0;
        color: #FFB74D;
    }
    .aura-response-content ul,
    .aura-response-content ol {
        margin: 4px 0;
        padding-left: 20px;
    }
    .aura-response-content li {
        margin: 2px 0;
        color: #FFB74D;
    }
    .aura-response-content code {
        background-color: #2a2a2a;
        color: #64B5F6;
        padding: 1px 4px;
        border-radius: 2px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px;
    }
    .aura-response-content pre {
        background-color: #1e1e1e;
        color: #FFE0A3;
        padding: 8px;
        border-radius: 4px;
        border-left: 3px solid #FFB74D;
        margin: 8px 0;
        white-space: pre-wrap;
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px;
        overflow-x: auto;
    }
    .aura-response-content pre code {
        background: transparent;
        padding: 0;
    }
    .aura-response-content blockquote {
        border-left: 3px solid #64B5F6;
        margin: 8px 0;
        padding-left: 12px;
        color: #E0C48A;
    }
    .aura-response-content strong,
    .aura-response-content b {
        color: #FFD27F;
        font-weight: bold;
    }
    .aura-response-content em,
    .aura-response-content i {
        color: #64B5F6;
        font-style: italic;
    }
    .aura-response-content a {
        color: #64B5F6;
        text-decoration: underline;
    }
    .diff-message {
        border: 1px solid #4a4a4a;
        border-radius: 6px;
        padding: 10px;
        margin: 12px 0;
        background: rgba(20, 20, 20, 0.85);
    }
    .diff-header {
        font-weight: bold;
        color: #FFD27F;
        margin-bottom: 6px;
    }
    .diff-file-list {
        list-style: none;
        margin: 0 0 8px 0;
        padding: 0;
        color: #FFB74D;
        font-size: 12px;
    }
    .diff-file-list li {
        margin: 2px 0;
    }
    .diff-block {
        background-color: #0e0e0e;
        border-radius: 4px;
        padding: 8px;
        border: 1px solid #333;
        font-size: 12px;
        overflow-x: auto;
    }
    .diff-line {
        display: block;
        white-space: pre;
        font-family: 'JetBrains Mono', monospace;
    }
    .diff-line.added { color: #66BB6A; }
    .diff-line.removed { color: #EF5350; }
    .diff-line.meta { color: #64B5F6; }
    .diff-line.neutral { color: #dcdcdc; }
    .diff-actions {
        margin-top: 10px;
        text-align: right;
    }
    .diff-actions a {
        display: inline-block;
        margin-left: 8px;
        padding: 4px 10px;
        border-radius: 4px;
        text-decoration: none;
        font-weight: bold;
    }
    .diff-actions a.accept {
        background-color: #2E7D32;
        color: #ffffff;
    }
    .diff-actions a.reject {
        background-color: #C62828;
        color: #ffffff;
    }
    .diff-status {
        margin-top: 8px;
        font-size: 12px;
        color: #64B5F6;
        text-align: right;
    }
</style>
"""


class Signaller(QObject):
    chunk_received = Signal(str)
    stream_ended = Signal()
    error_received = Signal(str)





class MainWindow(QMainWindow):
    """Main window with a Modern Retro Terminal log and markdown-rendered output."""

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
        QLabel#auto_accept_label {
            color: #64B5F6;
            font-weight: bold;
            padding-left: 12px;
        }
        QTextBrowser#chat_display, QTextEdit#chat_display {
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

    def __init__(self, event_bus: EventBus, image_storage: ImageStorageService):
        super().__init__()
        self.event_bus = event_bus
        self.image_storage = image_storage
        self.settings_window = None
        self.auto_accept_enabled = get_auto_accept_changes()
        self.pending_change_states: Dict[str, str] = {}
        self.setWindowTitle("Aura - Command Deck")

        # Set sensible default window size and minimum constraints
        self.setGeometry(100, 100, 1100, 750)
        self.setMinimumSize(800, 600)

        self.setStyleSheet(self.AURA_STYLESHEET)
        self._set_window_icon()

        self.is_streaming_response = False
        self.signaller = Signaller()
        self._styles_injected = False
        

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

        self.chat_display = QTextBrowser()
        # Prevent chat display from receiving keyboard focus to avoid
        # conflicts between user selection and caret/auto-scrolling
        self.chat_display.setFocusPolicy(Qt.NoFocus)
        self.chat_display.setObjectName("chat_display")
        self.chat_display.setOpenLinks(False)
        self.chat_display.anchorClicked.connect(self._handle_anchor_clicked)
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

        btn_new_project = QPushButton("New Project")
        btn_new_project.setObjectName("top_bar_button")
        btn_new_project.clicked.connect(self._create_new_project)

        btn_import_project = QPushButton("Import Project...")
        btn_import_project.setObjectName("top_bar_button")
        btn_import_project.clicked.connect(self._import_project)

        btn_configure_agents = QPushButton("Configure Agents")
        btn_configure_agents.setObjectName("top_bar_button")
        btn_configure_agents.clicked.connect(self._open_settings_dialog)

        self.auto_accept_label = QLabel()
        self.auto_accept_label.setObjectName("auto_accept_label")
        self.auto_accept_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._update_auto_accept_label()

        hl.addWidget(btn_new_session)
        hl.addStretch()
        hl.addWidget(btn_new_project)
        hl.addWidget(btn_import_project)
        hl.addWidget(btn_configure_agents)
        hl.addWidget(self.auto_accept_label)
        return widget

    def _update_auto_accept_label(self):
        state_text = "ON" if self.auto_accept_enabled else "OFF"
        color = "#66BB6A" if self.auto_accept_enabled else "#FF7043"
        if hasattr(self, "auto_accept_label") and self.auto_accept_label:
            self.auto_accept_label.setText(
                f"<span style='color: {color}; font-weight:bold;'>Auto-Accept: {state_text}</span>"
            )

    def _create_input_area(self):
        container = QWidget()
        hl = QHBoxLayout(container)
        hl.setContentsMargins(0, 0, 0, 0)

        self.chat_input = ChatInputTextEdit(image_storage=self.image_storage)
        self.chat_input.setObjectName("chat_input")
        self.chat_input.setPlaceholderText("Type here. Shift+Enter for newline. Enter to send.")
        self.chat_input.sendMessage.connect(self._send_message)

        hl.addWidget(self.chat_input, 1)
        return container

    def _ensure_chat_styles(self):
        if not self._styles_injected:
            self.chat_display.insertHtml(AURA_RESPONSE_CSS)
            self._styles_injected = True

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
        self.event_bus.subscribe("GENERATION_PROGRESS", self._handle_generation_progress)

        self.event_bus.subscribe("PROJECT_ACTIVATED", self._handle_project_activated)
        self.event_bus.subscribe("PROJECT_IMPORTED", self._handle_project_imported)
        self.event_bus.subscribe("PROJECT_IMPORT_ERROR", self._handle_project_import_error)
        self.event_bus.subscribe("VALIDATED_CODE_SAVED", self._handle_validated_code_saved)
        self.event_bus.subscribe("FILE_DIFF_READY", self._handle_file_diff_ready)
        self.event_bus.subscribe("FILE_CHANGES_APPLIED", self._handle_file_changes_applied)
        self.event_bus.subscribe("FILE_CHANGES_REJECTED", self._handle_file_changes_rejected)
        self.event_bus.subscribe("USER_PREFERENCES_UPDATED", self._handle_preferences_updated)
        # New: listen for generated blueprints to display summary
        self.event_bus.subscribe("BLUEPRINT_GENERATED", self._handle_blueprint_generated)
        # Build lifecycle completion signal
        self.event_bus.subscribe("BUILD_COMPLETED", self._handle_build_completed)


    # Boot
    def _start_boot_sequence(self):
        self.chat_display.clear()
        self._styles_injected = False
        self._ensure_chat_styles()
        for item in self.BOOT_SEQUENCE:
            text = item.get("text", "")
            if text:
                boot_html = f'<div style="color: #FFB74D; font-family: JetBrains Mono, monospace; font-size: 13px; margin: 2px 0;">{text}</div><br>'
                self.chat_display.insertHtml(boot_html)
        self.chat_display.ensureCursorVisible()

    def _log_system_message(self, category: str, message: str):
        """Display system messages as centered blocks."""
        self._ensure_chat_styles()
        color_map = {
            "KERNEL": "#64B5F6",
            "SYSTEM": "#66BB6A",
            "NEURAL": "#FFB74D",
            "SUCCESS": "#39FF14",
            "ERROR": "#FF4444",
            "WORKSPACE": "#64B5F6",
            "USER": "#64B5F6",
            "DEFAULT": "#dcdcdc",
        }
        color = color_map.get(category.upper(), color_map["DEFAULT"])
        safe_message = escape(message).replace("\n", "<br>")
        system_html = (
            '<div style="margin: 12px 0; text-align: center;">'
            f'<span style="color: {color}; font-size: 12px;">'
            f"[{category.upper()}] {safe_message}"
            "</span>"
            "</div>"
            "<br>"
        )
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

    def _create_new_project(self):
        """Show dialog to create a new project."""
        project_name, ok = QInputDialog.getText(
            self,
            "New Project",
            "Enter project name:",
            text=""
        )

        if ok and project_name:
            project_name = project_name.strip()
            if not project_name:
                self._log_system_message("ERROR", "Project name cannot be empty.")
                return

            logger.info(f"User requested new project: {project_name}")
            # Use the /project create command to create and switch to the new project
            self.event_bus.dispatch(
                Event(
                    event_type="SEND_USER_MESSAGE",
                    payload={"text": f"/project create {project_name}"}
                )
            )

    # Input/Output
    def _log_user_message(self, user_text: str, image: Optional[Union[str, Dict[str, Any]]] = None):
        """Display a single user bubble using basic block HTML."""
        self._ensure_chat_styles()
        safe_text = escape(user_text).replace("\n", "<br>") if user_text else ""

        content_parts: List[str] = []
        if safe_text:
            content_parts.append(safe_text)

        display_image: Optional[Dict[str, Any]] = None
        has_image_reference = image is not None
        image_reference: Optional[str] = None

        if isinstance(image, dict):
            if isinstance(image.get("path"), str):
                image_reference = image["path"]
            elif isinstance(image.get("relative_path"), str):
                image_reference = image["relative_path"]
            elif image.get("data"):
                display_image = {
                    "base64_data": image.get("data"),
                    "mime_type": image.get("mime_type") or "image/png",
                }
        elif isinstance(image, Path):
            image_reference = image.as_posix()
        elif isinstance(image, str):
            image_reference = image

        if display_image is None and image_reference and self.image_storage:
            loaded = self.image_storage.load_image(image_reference)
            if loaded:
                display_image = loaded
            else:
                logger.warning("Failed to load image for display from %s", image_reference)

        if display_image and display_image.get("base64_data"):
            mime_type = display_image.get("mime_type") or "image/png"
            image_html = (
                '<div style="margin-top: 10px; text-align: center;">'
                f'<img src="data:{mime_type};base64,{display_image["base64_data"]}" '
                'style="max-width: 260px; max-height: 180px; border-radius: 6px; '
                'border: 1px solid rgba(255, 255, 255, 0.2); display: block; margin: 0 auto;" '
                'alt="User attached image" />'
                '</div>'
            )
            content_parts.append(image_html)
        elif has_image_reference:
            content_parts.append('<span style="color: #7CC4FF;">[Image attached]</span>')

        if not content_parts:
            content_parts.append('<span style="color: #7CC4FF;">[Image attached]</span>')

        user_html = (
            '<div style="margin: 15px 0; text-align: right;">'
            '<div style="display: inline-block; max-width: 65%; background-color: #34536d; '
            'color: #f5f8ff; padding: 14px; border-radius: 8px; text-align: left; '
            "font-family: 'JetBrains Mono', monospace; font-size: 14px; line-height: 1.55;\">"
            '<strong style="color: #7CC4FF; font-size: 11px;">YOU</strong><br>'
            f"{''.join(content_parts)}"
            "</div>"
            "</div>"
            "<br>"
        )
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.insertHtml(user_html)
        self.chat_display.ensureCursorVisible()

    def _normalize_image_reference(self, image: Optional[Union[str, Dict[str, Any]]]) -> Optional[Union[str, Dict[str, Any]]]:
        if image is None:
            return None
        if isinstance(image, dict):
            if isinstance(image.get("path"), str):
                return image["path"]
            if isinstance(image.get("relative_path"), str):
                return image["relative_path"]
            data = image.get("data")
            if data and self.image_storage:
                saved_path = self.image_storage.save_image(data, image.get("mime_type") or "image/png")
                if saved_path:
                    return saved_path
            return image
        if isinstance(image, Path):
            return image.as_posix()
        if isinstance(image, str):
            return image
        return image

    def _render_aura_response(self, response_text: str):
        """Render AURA's response as a left-aligned block using minimal HTML."""
        normalized_text = response_text.replace("\r\n", "\n").replace("\r", "\n")
        html_content = markdown.markdown(
            normalized_text,
            extensions=[
                "markdown.extensions.fenced_code",
                "markdown.extensions.nl2br",
                "markdown.extensions.sane_lists",
            ],
            output_format="html5",
        )
        heading_styles = {
            "h1": "color: #FFF3D2; font-size: 18px; margin: 10px 0 6px 0;",
            "h2": "color: #FFEFC5; font-size: 17px; margin: 10px 0 6px 0;",
            "h3": "color: #FFEBC0; font-size: 16px; margin: 8px 0 4px 0;",
            "h4": "color: #FFE7B8; font-size: 15px; margin: 8px 0 4px 0;",
            "h5": "color: #FFE3B0; font-size: 14px; margin: 6px 0 4px 0;",
            "h6": "color: #FFDFA6; font-size: 13px; margin: 6px 0 4px 0;",
        }
        for tag, style in heading_styles.items():
            html_content = html_content.replace(
                f"<{tag}>", f"<{tag} style=\"{style}\">"
            )
        html_content = html_content.replace(
            "<pre>",
            "<pre style=\"background-color: #211207; color: #FFEBD2; padding: 10px; "
            "border-radius: 6px; border-left: 3px solid #FFCF8C; margin: 8px 0; "
            "white-space: pre-wrap; font-family: 'JetBrains Mono', monospace; font-size: 13px;\">",
        )
        html_content = html_content.replace(
            "<code>",
            "<code style=\"background-color: #3a2310; color: #FFEBD2; padding: 2px 6px; "
            "border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: 13px;\">",
        )
        html_content = html_content.replace("<p>", "<p style=\"margin: 6px 0;\">")
        aura_html = (
            '<div style="margin: 15px 0; text-align: left;">'
            '<div style="display: inline-block; max-width: 70%; background-color: #2a170a; '
            'color: #FFEBD2; padding: 14px; border-radius: 8px; '
            "font-family: 'JetBrains Mono', monospace; font-size: 14px; line-height: 1.55;\">"
            '<strong style="color: #FFEFC5; font-size: 11px;">AURA</strong><br>'
            f"{html_content}"
            "</div>"
            "</div>"
            "<br>"
        )
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.insertHtml(aura_html)
        self.chat_display.ensureCursorVisible()

    def _send_message(self):
        raw_text = self.chat_input.toPlainText()
        image_attachment = self.chat_input.take_attached_image()
        user_text = raw_text.strip()
        if not user_text and not image_attachment:
            return

        normalized_image = self._normalize_image_reference(image_attachment)

        self.chat_input.clear()
        self.chat_input.setEnabled(False)

        # Display user message instantly
        self._log_user_message(user_text, normalized_image)

        self.thinking_indicator.start_thinking("Analyzing your request...")
        payload: Dict[str, Any] = {"text": user_text}
        if normalized_image:
            payload["image"] = normalized_image
        self.event_bus.dispatch(Event(event_type="SEND_USER_MESSAGE", payload=payload))

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

    def _short_change_id(self, change_id: Optional[str]) -> str:
        if not change_id:
            return "N/A"
        return change_id[:8].upper()

    def _handle_generation_progress(self, event: Event):
        payload = event.payload or {}
        message = payload.get("message")
        if not message:
            return
        category = (payload.get("category") or "SYSTEM").upper()
        details = payload.get("details") or []
        self._display_system_message(category, message)
        for detail in details:
            self._display_system_message("DEFAULT", f"  {detail}")

    def _display_diff_message(self, payload: Dict[str, Any], *, pending: bool, auto_applied: bool):
        self._ensure_chat_styles()
        diff_html = self._build_diff_html(payload, pending=pending, auto_applied=auto_applied)
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.insertHtml(diff_html)
        self.chat_display.insertHtml("<br>")
        self.chat_display.ensureCursorVisible()

    def _build_diff_html(self, payload: Dict[str, Any], *, pending: bool, auto_applied: bool) -> str:
        change_id = payload.get("change_id")
        summary = payload.get("summary") or {}
        files = payload.get("files") or []

        total_files = summary.get("total_files") or len(files)
        additions = summary.get("total_additions", 0)
        deletions = summary.get("total_deletions", 0)

        header_parts = [
            f"Change {self._short_change_id(change_id)}",
            f"{total_files} file{'s' if total_files != 1 else ''}",
            f"+{additions} / -{deletions}",
        ]
        header_html = " | ".join(header_parts)

        file_items = []
        for file_info in files:
            path = file_info.get("display_path") or file_info.get("relative_path") or "unknown"
            add = file_info.get("additions", 0)
            remove = file_info.get("deletions", 0)
            badge = " (new)" if file_info.get("is_new_file") else ""
            file_items.append(f"<li><strong>{escape(path)}</strong> (+{add} / -{remove}){badge}</li>")

        file_list_html = "".join(file_items) or "<li><strong>Unknown file</strong></li>"

        diff_sections = []
        for file_info in files:
            path = file_info.get("display_path") or file_info.get("relative_path") or "unknown"
            diff_text = file_info.get("diff", "")
            diff_lines_html = self._format_diff_lines(diff_text)
            diff_sections.append(
                "<div>"
                f"<div style='font-size:12px; color:#64B5F6; margin-bottom:4px;'>{escape(path)}</div>"
                f"<pre class='diff-block'>{diff_lines_html}</pre>"
                "</div>"
            )

        actions_html = ""
        status_html = ""
        if pending:
            actions_html = (
                "<div class='diff-actions'>"
                f"<a href='aura://reject?change_id={change_id}' class='reject'>Reject</a>"
                f"<a href='aura://accept?change_id={change_id}' class='accept'>Accept</a>"
                "</div>"
            )
        else:
            status_text = "Changes applied automatically." if auto_applied else "Changes applied."
            status_html = f"<div class='diff-status'>{escape(status_text)}</div>"

        return (
            "<div class='diff-message'>"
            f"<div class='diff-header'>{escape(header_html)}</div>"
            f"<ul class='diff-file-list'>{file_list_html}</ul>"
            f"{''.join(diff_sections)}"
            f"{actions_html}{status_html}"
            "</div>"
        )

    def _format_diff_lines(self, diff_text: str) -> str:
        """
        Format diff lines with syntax highlighting and line number extraction.

        Parses unified diff headers (@@ -X,Y +A,B @@) to extract and display
        line numbers for easier navigation in large files.
        """
        import re

        lines_html: List[str] = []
        for raw_line in (diff_text or "").splitlines():
            line = raw_line or ""

            # Check if this is a hunk header with line numbers
            if line.startswith("@@"):
                # Parse the hunk header to extract line numbers
                # Format: @@ -old_start,old_count +new_start,new_count @@
                match = re.match(r'@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@', line)
                if match:
                    old_start = int(match.group(1))
                    old_count = int(match.group(2)) if match.group(2) else 1
                    new_start = int(match.group(3))
                    new_count = int(match.group(4)) if match.group(4) else 1

                    # Format line number info
                    old_end = old_start + old_count - 1
                    new_end = new_start + new_count - 1

                    # Create a user-friendly line number display
                    if old_count == 1:
                        old_range = f"Line {old_start}"
                    else:
                        old_range = f"Lines {old_start}-{old_end}"

                    if new_count == 1:
                        new_range = f"Line {new_start}"
                    else:
                        new_range = f"Lines {new_start}-{new_end}"

                    line_info = f"@@ {old_range} → {new_range} @@"
                    lines_html.append(f"<span class='diff-line meta'>{escape(line_info)}</span>")
                else:
                    # Fallback if regex doesn't match
                    lines_html.append(f"<span class='diff-line meta'>{escape(line)}</span>")

            elif line.startswith("+") and not line.startswith("+++"):
                css_class = "diff-line added"
                lines_html.append(f"<span class='{css_class}'>{escape(line)}</span>")
            elif line.startswith("-") and not line.startswith("---"):
                css_class = "diff-line removed"
                lines_html.append(f"<span class='{css_class}'>{escape(line)}</span>")
            elif line.startswith("diff ") or line.startswith("---") or line.startswith("+++"):
                css_class = "diff-line meta"
                lines_html.append(f"<span class='{css_class}'>{escape(line)}</span>")
            else:
                css_class = "diff-line neutral"
                lines_html.append(f"<span class='{css_class}'>{escape(line)}</span>")

        if not lines_html:
            lines_html.append("<span class='diff-line neutral'>(no changes)</span>")

        return "\n".join(lines_html)

    def _handle_file_diff_ready(self, event: Event):
        payload = event.payload or {}
        change_id = payload.get("change_id")
        files = payload.get("files") or []
        if not change_id or not files:
            return

        pending = bool(payload.get("pending"))
        auto_applied = bool(payload.get("auto_applied"))

        state = "pending" if pending else ("applied_auto" if auto_applied else "applied")
        self.pending_change_states[change_id] = state

        self._display_diff_message(payload, pending=pending, auto_applied=auto_applied)

        if pending:
            self._log_system_message(
                "SYSTEM",
                f"Review pending change {self._short_change_id(change_id)} and choose Accept or Reject."
            )
        elif auto_applied:
            self._log_system_message(
                "SUCCESS",
                f"Changes auto-applied ({self._short_change_id(change_id)})."
            )

    def _handle_file_changes_applied(self, event: Event):
        payload = event.payload or {}
        change_id = payload.get("change_id")
        auto_applied = bool(payload.get("auto_applied"))
        if change_id:
            self.pending_change_states[change_id] = "applied"

        status = "Changes auto-applied" if auto_applied else "Changes written to workspace"
        self._log_system_message("SUCCESS", f"{status} ({self._short_change_id(change_id)})")

    def _handle_file_changes_rejected(self, event: Event):
        payload = event.payload or {}
        change_id = payload.get("change_id")
        if change_id:
            self.pending_change_states[change_id] = "rejected"
        self._log_system_message("SYSTEM", f"Discarded change {self._short_change_id(change_id)}.")

    def _handle_preferences_updated(self, event: Event):
        prefs = (event.payload or {}).get("preferences") or {}
        if "auto_accept_changes" not in prefs:
            return
        new_value = bool(prefs.get("auto_accept_changes"))
        if new_value != self.auto_accept_enabled:
            self.auto_accept_enabled = new_value
            self._update_auto_accept_label()
            status_text = "Auto-accept enabled" if new_value else "Auto-accept disabled"
            self._log_system_message("SYSTEM", status_text)

    def _handle_anchor_clicked(self, url: QUrl):
        if url.scheme() != "aura":
            QDesktopServices.openUrl(url)
            return

        action = url.host() or url.path().lstrip("/")
        query = QUrlQuery(url)
        change_id = query.queryItemValue("change_id")
        if not change_id:
            return

        current_state = self.pending_change_states.get(change_id, "pending")
        if current_state != "pending":
            return

        if action == "accept":
            self.pending_change_states[change_id] = "applying"
            self._log_system_message("SYSTEM", f"Applying change {self._short_change_id(change_id)}...")
            self.event_bus.dispatch(Event(event_type="APPLY_FILE_CHANGES", payload={"change_id": change_id}))
        elif action == "reject":
            self.pending_change_states[change_id] = "rejecting"
            self._log_system_message("SYSTEM", f"Rejecting change {self._short_change_id(change_id)}...")
            self.event_bus.dispatch(Event(event_type="REJECT_FILE_CHANGES", payload={"change_id": change_id}))

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

    
