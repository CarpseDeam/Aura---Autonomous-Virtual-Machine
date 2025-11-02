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
        # Tighter margins and spacing to reduce clutter
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._panel = TerminalSessionPanel(event_bus, terminal_session_manager)
        self._panel.setMinimumWidth(250)
        self._panel.setMaximumWidth(400)
        layout.addWidget(self._panel)

        # Normalize internal panel spacing between session items
        try:
            # Reduce header and section spacing where possible
            if self._panel.layout():
                self._panel.layout().setSpacing(4)

            # Active sessions container spacing and margins
            if hasattr(self._panel, "active_layout"):
                self._panel.active_layout.setSpacing(4)
                self._panel.active_layout.setContentsMargins(4, 4, 4, 4)

            # Completed sessions container spacing and margins
            if hasattr(self._panel, "completed_layout"):
                self._panel.completed_layout.setSpacing(4)
                self._panel.completed_layout.setContentsMargins(4, 4, 4, 4)

            # Show scrollbar only when more than ~5 sessions are present
            if hasattr(self._panel, "active_scroll"):
                # Approximate 5 session rows height with margins
                self._panel.active_scroll.setMaximumHeight(180)
        except Exception:
            # Be defensive: UI tweaks should never break the panel
            pass

    @property
    def panel(self) -> TerminalSessionPanel:
        """
        Expose the underlying terminal session panel for advanced interactions.
        """
        return self._panel
