from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Signal
from src.aura.models.task import Task, TaskStatus

class TaskWidgetItem(QWidget):
    """
    A custom widget to display a single task with its status and action buttons.
    """
    task_dispatched = Signal(str) # Signal emits the task ID

    STYLE = """
        QWidget {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a;
            border-radius: 5px;
            padding: 5px;
        }
        QPushButton {
            background-color: #3a3a3a;
            font-size: 12px;
            padding: 4px 8px;
            border-radius: 3px;
        }
        QPushButton:hover {
            border: 1px solid #00FFFF; /* Cyan */
        }
    """

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self.task = task
        self.setStyleSheet(self.STYLE)
        self._init_ui()

    def _init_ui(self):
        """Initializes the UI for the task item."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        description_label = QLabel(f"○ {self.task.description}")
        description_label.setStyleSheet("border: none; padding: 0;")

        self.dispatch_button = QPushButton("Dispatch")
        self.dispatch_button.setToolTip("Send this task to the Engineer Agent")
        self.dispatch_button.clicked.connect(self._on_dispatch_clicked)

        layout.addWidget(description_label, 1) # Description takes up available space
        layout.addWidget(self.dispatch_button)

        self._update_status()

    def _on_dispatch_clicked(self):
        """Emits the task_dispatched signal with the task ID."""
        self.task_dispatched.emit(self.task.id)
        # Visually disable the button after dispatch
        self.task.status = TaskStatus.IN_PROGRESS
        self._update_status()

    def _update_status(self):
        """Updates the widget's appearance based on the task status."""
        if self.task.status != TaskStatus.PENDING:
            self.dispatch_button.setText("Dispatched")
            self.dispatch_button.setEnabled(False)
            self.dispatch_button.setStyleSheet("background-color: #1a1a1a; color: #555;")