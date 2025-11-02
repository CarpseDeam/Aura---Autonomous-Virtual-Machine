from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class ProjectSwitchDialog(QDialog):
    """
    Retro-styled modal used to select and activate an Aura project.
    """

    DIALOG_STYLESHEET = """
        QDialog {
            background-color: #000000;
            color: #FFB74D;
            font-family: 'JetBrains Mono', 'Courier New', monospace;
            font-size: 14px;
        }
        QLabel#dialog_title {
            color: #FFB74D;
            font-weight: bold;
            font-size: 18px;
            padding-bottom: 6px;
        }
        QListWidget {
            background-color: #101010;
            border: 1px solid #4a4a4a;
            color: #FFB74D;
            padding: 6px;
        }
        QListWidget::item:selected {
            background-color: #FFB74D;
            color: #000000;
        }
        QPushButton {
            background-color: #101010;
            border: 1px solid #FFB74D;
            color: #FFB74D;
            font-weight: bold;
            padding: 6px 14px;
            border-radius: 4px;
            min-width: 96px;
        }
        QPushButton:hover {
            background-color: #1a1a1a;
        }
        QPushButton#select_button {
            background-color: #FFB74D;
            color: #000000;
        }
        QPushButton#select_button:hover {
            background-color: #FFA726;
        }
    """

    def __init__(self, parent: QWidget | None, project_names: List[str]):
        super().__init__(parent)
        self.selected_project: Optional[str] = None

        self.setWindowTitle("Select Project")
        self.setModal(True)
        self.setStyleSheet(self.DIALOG_STYLESHEET)
        self.setFixedSize(420, 320)

        ascii_font = QFont("JetBrains Mono", 11)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        top_border = QLabel("+--------------------------------------+")
        top_border.setAlignment(Qt.AlignCenter)
        top_border.setFont(ascii_font)
        layout.addWidget(top_border)

        title_label = QLabel("SELECT PROJECT")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setObjectName("dialog_title")
        layout.addWidget(title_label)

        self._project_list = QListWidget()
        self._project_list.setSelectionMode(QListWidget.SingleSelection)
        for name in project_names:
            QListWidgetItem(name, self._project_list)
        if project_names:
            self._project_list.setCurrentRow(0)
        self._project_list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self._project_list)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        select_button = QPushButton("Select")
        select_button.setObjectName("select_button")
        select_button.clicked.connect(self.accept)
        button_row.addWidget(select_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        layout.addLayout(button_row)

        bottom_border = QLabel("+--------------------------------------+")
        bottom_border.setAlignment(Qt.AlignCenter)
        bottom_border.setFont(ascii_font)
        layout.addWidget(bottom_border)

    def accept(self) -> None:  # noqa: D401 - inherited docstring
        current_item = self._project_list.currentItem()
        if current_item is None:
            logger.debug("Project selection attempted with no item highlighted.")
            return
        self.selected_project = current_item.text().strip()
        super().accept()

    def reject(self) -> None:  # noqa: D401 - inherited docstring
        self.selected_project = None
        super().reject()
