from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QVBoxLayout, QWidget

from src.aura.app.event_bus import EventBus
from src.ui.widgets.terminal_session_panel import TerminalSessionPanel


class TerminalMonitorWidget(QWidget):
    """
    Container widget that embeds the terminal session panel and standardizes sizing.
    """

    def __init__(
        self,
        event_bus: EventBus,
        terminal_session_manager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._panel = TerminalSessionPanel(event_bus, terminal_session_manager)
        self._panel.setMinimumWidth(250)
        self._panel.setMaximumWidth(400)
        layout.addWidget(self._panel)

    @property
    def panel(self) -> TerminalSessionPanel:
        """
        Expose the underlying terminal session panel for advanced interactions.
        """
        return self._panel
