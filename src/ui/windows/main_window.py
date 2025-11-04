from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QSplitter, QVBoxLayout, QWidget

from src.aura.app.event_bus import EventBus
from src.aura.config import ASSETS_DIR
from src.aura.models.event_types import (
    AGENT_OUTPUT,
    TERMINAL_SESSION_COMPLETED,
    TERMINAL_SESSION_FAILED,
)
from src.aura.models.events import Event
from src.aura.services.agent_supervisor import AgentSupervisor
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.image_storage_service import ImageStorageService
from src.aura.services.llm_service import LLMService
from src.aura.services.terminal_agent_service import TerminalAgentService
from src.aura.services.terminal_session_manager import TerminalSessionManager
from src.aura.services.user_settings_manager import get_auto_accept_changes
from src.aura.services.workspace_service import WorkspaceService
from src.ui.windows.main_window_constants import (
    AURA_ASCII_BANNER,
    AURA_STYLESHEET,
    BOOT_SEQUENCE,
)
from src.ui.windows.main_window_events import MainWindowEventController
from src.ui.windows.project_actions import ProjectActions
from src.ui.windows.settings_window import SettingsWindow
from src.ui.widgets.chat_display_widget import ChatDisplayWidget
from src.ui.widgets.chat_input_widget import ChatInputWidget
from src.ui.widgets.terminal_monitor_widget import TerminalMonitorWidget
from src.ui.widgets.thinking_indicator_widget import ThinkingIndicatorWidget
from src.ui.widgets.toolbar_widget import ToolbarWidget
from src.ui.qt_worker import Worker
from src.ui.widgets.conversation_sidebar_widget import ConversationSidebarWidget

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Aura command deck main window orchestrating specialized UI widgets.
    """

    restore_input_signal = Signal()

    def __init__(
        self,
        event_bus: EventBus,
        image_storage: ImageStorageService,
        *,
        llm_service: LLMService,
        terminal_service: TerminalAgentService,
        workspace_service: WorkspaceService,
        conversations: ConversationManagementService,
        terminal_session_manager: Optional[TerminalSessionManager] = None,
    ) -> None:
        super().__init__()
        self.event_bus = event_bus
        self.settings_window: Optional[SettingsWindow] = None
        self.workspace_service = workspace_service
        self.supervisor = AgentSupervisor(
            llm_service,
            terminal_service,
            workspace_service,
            self.event_bus,
        )

        self._auto_accept_enabled = get_auto_accept_changes()

        if conversations is None:
            raise ValueError("conversations dependency is required for MainWindow")

        self.setWindowTitle("Aura - Command Deck")
        self.setGeometry(100, 100, 1100, 750)
        self.setMinimumSize(800, 600)
        self.setStyleSheet(AURA_STYLESHEET)
        self._set_window_icon()

        self.toolbar = ToolbarWidget(auto_accept_enabled=self._auto_accept_enabled, parent=self)
        self.conversation_sidebar = ConversationSidebarWidget(parent=self)
        self.chat_display = ChatDisplayWidget(image_storage=image_storage, parent=self)
        self.chat_input = ChatInputWidget(image_storage=image_storage, parent=self)
        self.thinking_indicator = ThinkingIndicatorWidget(parent=self)
        self.project_actions = ProjectActions(self.event_bus, self.chat_display, self)
        self.terminal_monitor: Optional[TerminalMonitorWidget] = None
        if terminal_session_manager:
            self.terminal_monitor = TerminalMonitorWidget(
                self.event_bus,
                terminal_session_manager,
                parent=self,
            )
        self._event_controller = MainWindowEventController(
            event_bus=self.event_bus,
            chat_display=self.chat_display,
            toolbar=self.toolbar,
            thinking_indicator=self.thinking_indicator,
            chat_input=self.chat_input,
            auto_accept_enabled=self._auto_accept_enabled,
            conversations=conversations,
        )

        self._build_layout()
        self._connect_signals()
        self._event_controller.register()
        self._subscribe_supervisor_events()

        self.chat_display.display_boot_sequence(BOOT_SEQUENCE)

    def _set_window_icon(self) -> None:
        icon_path = ASSETS_DIR / "aura_gear_icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _build_layout(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Use full banner artwork
        banner_text = AURA_ASCII_BANNER

        banner_label = QLabel(banner_text)
        banner_label.setObjectName("aura_banner")
        banner_label.setFont(QFont("JetBrains Mono", 9))
        banner_label.setAlignment(Qt.AlignCenter)
        banner_label.setContentsMargins(0, 0, 0, 0)
        # Allow enough height for full banner; no decorative borders
        banner_label.setMaximumHeight(120)

        layout.addWidget(self.toolbar)
        layout.addWidget(banner_label)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.conversation_sidebar)
        self.splitter.addWidget(self.chat_display)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setCollapsible(0, True)
        self.splitter.setStretchFactor(0, 0)  # Sidebar has fixed width
        self.splitter.setStretchFactor(1, 1)  # Chat display takes remaining space
        layout.addWidget(self.splitter, 1)

        layout.addWidget(self.thinking_indicator)
        layout.addWidget(self.chat_input)

    def _connect_signals(self) -> None:
        self.toolbar.new_session_requested.connect(self._start_new_session)
        self.toolbar.new_project_requested.connect(self.project_actions.create_new_project)
        self.toolbar.switch_project_requested.connect(self.project_actions.open_project_switcher)
        self.toolbar.import_project_requested.connect(self.project_actions.import_project)
        self.toolbar.configure_requested.connect(self._open_settings_dialog)

        self.chat_input.message_requested.connect(self._handle_message_requested)
        self.chat_display.anchor_requested.connect(self._event_controller.handle_anchor_clicked)
        self.restore_input_signal.connect(self._restore_chat_input)
        # Resize splitter when sidebar collapses/expands
        try:
            self.conversation_sidebar.collapsed_changed.connect(self._on_sidebar_collapsed_changed)
        except Exception:
            pass

    def _subscribe_supervisor_events(self) -> None:
        """Subscribe to supervisor-emitted events for agent output and lifecycle."""
        self.event_bus.subscribe(AGENT_OUTPUT, self._handle_agent_output)
        self.event_bus.subscribe(TERMINAL_SESSION_COMPLETED, self._handle_session_completed)
        self.event_bus.subscribe(TERMINAL_SESSION_FAILED, self._handle_session_failed)

    def _start_new_session(self) -> None:
        self.event_bus.dispatch(Event(event_type="NEW_SESSION_REQUESTED"))
        self.chat_display.display_boot_sequence(BOOT_SEQUENCE)

    def _handle_agent_output(self, event: Event) -> None:
        payload = event.payload or {}
        text = (payload.get("text") or "").strip()
        if not text:
            return
        if self.thinking_indicator.is_animating:
            self.thinking_indicator.stop_thinking()
        self.chat_display.display_system_message("AGENT", text)

    def _handle_session_completed(self, event: Event) -> None:
        payload = event.payload or {}
        task_id = payload.get("task_id", "?")
        summary_data = payload.get("summary_data")

        self._restore_chat_input()

        if summary_data:
            self.chat_display.display_task_summary(summary_data)
        else:
            reason = payload.get("completion_reason") or "completed"
            self.chat_display.display_system_message("AGENT", f"Task {task_id} completed: {reason}")

    def _handle_session_failed(self, event: Event) -> None:
        payload = event.payload or {}
        reason = payload.get("failure_reason") or payload.get("completion_reason") or "failed"
        task_id = payload.get("task_id", "?")
        error_message = payload.get("error_message")
        self._restore_chat_input()
        summary = f"Task {task_id} failed: {reason}"
        if error_message:
            summary = f"{summary} ({error_message})"
        self.chat_display.display_system_message("ERROR", summary)

    def _restore_chat_input(self) -> None:
        self.thinking_indicator.stop_thinking()
        self.chat_input.setEnabled(True)
        self.chat_input.focus_input()

    def _resolve_active_project(self) -> str:
        active = getattr(self.workspace_service, "active_project", None)
        if active:
            return active
        return "default_project"

    def _open_settings_dialog(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.event_bus)
        self.settings_window.show()

    def _handle_message_requested(self) -> None:
        result = self.chat_input.take_message()
        if result is None:
            return

        user_text, normalized_image = result
        self.chat_input.setEnabled(False)

        self.chat_display.display_user_message(user_text, normalized_image)
        self.thinking_indicator.start_thinking("Analyzing your request...")

        worker = Worker(self._handle_message_background, user_text, normalized_image)
        QThreadPool.globalInstance().start(worker)

    def _handle_message_background(self, user_text: str, normalized_image: Optional[str]) -> None:
        """Runs in background thread - safe to block."""
        logger.debug("Processing message in background")
        if normalized_image:
            logger.info(f"Image attachment received but not yet handled: {normalized_image}")

        project_name = self._resolve_active_project()
        try:
            self.supervisor.process_message(user_text, project_name)
        except Exception as exc:
            logger.error("Failed to process message with supervisor: %s", exc, exc_info=True)
            # Since this is in a background thread, we need to explicitly restore the UI
            self.restore_input_signal.emit()

    def closeEvent(self, event) -> None:  # noqa: D401 - QWidget signature
        QApplication.quit()
        super().closeEvent(event)

    def _on_sidebar_collapsed_changed(self, collapsed: bool) -> None:
        """Adjust splitter sizes when the conversation sidebar is toggled."""
        if not hasattr(self, "splitter"):
            return
        try:
            total = max(self.width() - 20, 400)
            if collapsed:
                # Allocate almost all space to the chat display when collapsed
                sidebar = 40
                rest = max(total - sidebar, 400)
                chat = rest
                self.splitter.setSizes([sidebar, chat])
            else:
                # Restore reasonable widths with visible sidebar
                sidebar = 250
                rest = max(total - sidebar, 400)
                chat = rest
                self.splitter.setSizes([sidebar, chat])
        except Exception:
            # Non-fatal UI concerns should never break the app
            pass