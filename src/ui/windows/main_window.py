from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QSplitter, QVBoxLayout, QWidget

from src.aura.app.event_bus import EventBus
from src.aura.config import ASSETS_DIR
from src.aura.models.events import Event
from src.aura.services.image_storage_service import ImageStorageService
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.user_settings_manager import get_auto_accept_changes
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
from src.ui.widgets.agent_console_widget import AgentConsoleWidget
from src.ui.widgets.thinking_indicator_widget import ThinkingIndicatorWidget
from src.ui.widgets.toolbar_widget import ToolbarWidget
from src.ui.widgets.conversation_sidebar_widget import ConversationSidebarWidget

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Aura command deck main window orchestrating specialized UI widgets.
    """

    def __init__(
        self,
        event_bus: EventBus,
        image_storage: ImageStorageService,
        terminal_session_manager: Optional[TerminalSessionManager] = None,
        conversations: Optional[ConversationManagementService] = None,
    ) -> None:
        super().__init__()
        self.event_bus = event_bus
        self.settings_window: Optional[SettingsWindow] = None

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
        self.agent_console = AgentConsoleWidget(self.event_bus, parent=self)

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

        banner_label = QLabel(AURA_ASCII_BANNER)
        banner_label.setObjectName("aura_banner")
        banner_label.setFont(QFont("JetBrains Mono", 10))
        banner_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.toolbar)
        layout.addWidget(banner_label)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.conversation_sidebar)
        splitter.addWidget(self.chat_display)
        splitter.addWidget(self.agent_console)
        splitter.setStretchFactor(0, 0)  # Sidebar has fixed width
        splitter.setStretchFactor(1, 3)  # Chat display gets most space
        splitter.setStretchFactor(2, 2)  # Agent console gets less space
        layout.addWidget(splitter, 1)

        layout.addWidget(self.thinking_indicator)
        layout.addWidget(self.chat_input)

    def _connect_signals(self) -> None:
        self.toolbar.new_session_requested.connect(self._start_new_session)
        self.toolbar.new_project_requested.connect(self.project_actions.create_new_project)
        self.toolbar.switch_project_requested.connect(self.project_actions.open_project_switcher)
        self.toolbar.import_project_requested.connect(self.project_actions.import_project)
        self.toolbar.configure_requested.connect(self._open_settings_dialog)
        self.toolbar.console_toggle_requested.connect(self._toggle_agent_console)

        self.chat_input.message_requested.connect(self._handle_message_requested)
        self.chat_display.anchor_requested.connect(self._event_controller.handle_anchor_clicked)

    def _start_new_session(self) -> None:
        self.event_bus.dispatch(Event(event_type="NEW_SESSION_REQUESTED"))
        self.chat_display.display_boot_sequence(BOOT_SEQUENCE)

    def _open_settings_dialog(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.event_bus)
        self.settings_window.show()

    def _toggle_agent_console(self) -> None:
        is_visible = self.agent_console.isVisible()
        self.agent_console.setVisible(not is_visible)

    def _handle_message_requested(self) -> None:
        result = self.chat_input.take_message()
        if result is None:
            return

        user_text, normalized_image = result
        self.chat_input.setEnabled(False)

        self.chat_display.display_user_message(user_text, normalized_image)
        self.thinking_indicator.start_thinking("Analyzing your request...")

        payload: Dict[str, Any] = {"text": user_text}
        if normalized_image:
            payload["image"] = normalized_image
        self.event_bus.dispatch(Event(event_type="SEND_USER_MESSAGE", payload=payload))

    def closeEvent(self, event) -> None:  # noqa: D401 - QWidget signature
        QApplication.quit()
        super().closeEvent(event)
