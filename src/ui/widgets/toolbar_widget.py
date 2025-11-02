from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QStyle,
)

from src.ui.widgets.token_display_widget import TokenDisplayWidget
from src.aura.config import ASSETS_DIR


class ToolbarWidget(QWidget):
    """
    Top toolbar containing session controls, project actions, and token metrics.
    """

    new_session_requested = Signal()
    new_project_requested = Signal()
    switch_project_requested = Signal()
    import_project_requested = Signal()
    configure_requested = Signal()
    console_toggle_requested = Signal()

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
        layout.setSpacing(8)

        style = self.style()
        btn_new_session = self._create_icon_button(
            tooltip="New Session",
            handler=self.new_session_requested.emit,
            icon=self._resolve_icon(style.standardIcon(QStyle.StandardPixmap.SP_ArrowForward)),
        )
        btn_new_project = self._create_icon_button(
            tooltip="New Project",
            handler=self.new_project_requested.emit,
            icon=self._resolve_icon(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder)),
        )
        btn_switch_project = self._create_icon_button(
            tooltip="Switch Project",
            handler=self.switch_project_requested.emit,
            icon=self._resolve_icon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload)),
        )
        btn_import_project = self._create_icon_button(
            tooltip="Import Project",
            handler=self.import_project_requested.emit,
            icon=self._resolve_icon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)),
        )
        gear_path = ASSETS_DIR / "aura_gear_icon.ico"
        configure_icon = QIcon(str(gear_path)) if gear_path.exists() else style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        btn_configure = self._create_icon_button(
            tooltip="Configure",
            handler=self.configure_requested.emit,
            icon=self._resolve_icon(configure_icon),
        )

        btn_console_toggle = self._create_icon_button(
            tooltip="Toggle Agent Console",
            handler=self.console_toggle_requested.emit,
            icon=self._resolve_icon(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)),
        )

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
        layout.addWidget(btn_console_toggle)
        layout.addWidget(btn_configure)
        layout.addWidget(self._token_display)
        layout.addWidget(self._auto_accept_label)

    def _create_button(self, text: str, handler: Callable[[], None]) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("top_bar_button")
        button.clicked.connect(handler)
        return button

    def _create_icon_button(self, *, tooltip: str, handler: Callable[[], None], icon: QIcon) -> QPushButton:
        """Create a compact icon-only toolbar button matching Aura theme."""
        button = QPushButton("", self)
        button.setObjectName("icon_button")
        button.setToolTip(tooltip)
        button.setIcon(icon)
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(QSize(36, 28))
        button.clicked.connect(handler)
        button.setStyleSheet(
            "QPushButton#icon_button {"
            "  background-color: #2c2c2c;"
            "  border: 1px solid #4a4a4a;"
            "  color: #dcdcdc;"
            "  border-radius: 5px;"
            "}"
            "QPushButton#icon_button:hover {"
            "  background-color: #3a3a3a;"
            "  border-color: #FFB74D;"
            "}"
        )
        return button

    def _resolve_icon(self, icon: QIcon) -> QIcon:
        return icon
