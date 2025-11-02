from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from src.ui.widgets.token_display_widget import TokenDisplayWidget


class ToolbarWidget(QWidget):
    """
    Top toolbar containing session controls, project actions, and token metrics.
    """

    new_session_requested = Signal()
    new_project_requested = Signal()
    switch_project_requested = Signal()
    import_project_requested = Signal()
    configure_requested = Signal()

    def __init__(self, *, auto_accept_enabled: bool, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._auto_accept_enabled = auto_accept_enabled
        self._setup_ui()

    @property
    def auto_accept_enabled(self) -> bool:
        return self._auto_accept_enabled

    def update_token_usage(self, current: int, limit: int, percent: Optional[float]) -> None:
        """
        Forward usage updates to the token display widget.
        """
        self._token_display.update_usage(current, limit, percent)

    def set_auto_accept_enabled(self, enabled: bool) -> None:
        """
        Update the auto-accept label to reflect the latest preference.
        """
        self._auto_accept_enabled = enabled
        state_text = "ON" if enabled else "OFF"
        color = "#66BB6A" if enabled else "#FF7043"
        self._auto_accept_label.setText(
            f"<span style='color: {color}; font-weight:bold;'>Auto-Accept: {state_text}</span>"
        )

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)

        btn_new_session = self._create_button("New Session", self.new_session_requested.emit)
        btn_new_project = self._create_button("New Project", self.new_project_requested.emit)
        btn_switch_project = self._create_button("Switch Project", self.switch_project_requested.emit)
        btn_import_project = self._create_button("Import Project...", self.import_project_requested.emit)
        btn_configure = self._create_button("Configure", self.configure_requested.emit)

        self._token_display = TokenDisplayWidget(self)
        self._auto_accept_label = QLabel(self)
        self._auto_accept_label.setObjectName("auto_accept_label")
        self._auto_accept_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.set_auto_accept_enabled(self._auto_accept_enabled)

        layout.addWidget(btn_new_session)
        layout.addStretch()
        layout.addWidget(btn_new_project)
        layout.addWidget(btn_switch_project)
        layout.addWidget(btn_import_project)
        layout.addWidget(btn_configure)
        layout.addWidget(self._token_display)
        layout.addWidget(self._auto_accept_label)

    def _create_button(self, text: str, handler: Callable[[], None]) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("top_bar_button")
        button.clicked.connect(handler)
        return button
