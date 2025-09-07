from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from src.aura.models.task import Task, TaskStatus

class TaskWidgetItem(QWidget):
    """
    A custom widget to display a single task's description.
    """
    STYLE = """
        QWidget {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a;
            border-radius: 5px;
            padding: 8px;
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

        if self.task.status == TaskStatus.COMPLETED:
            description_label = QLabel(f"✓ {self.task.description}")
            description_label.setStyleSheet(
                "border: none; padding: 0; text-decoration: line-through; color: grey;"
            )
        else:
            description_label = QLabel(f"○ {self.task.description}")
            description_label.setStyleSheet("border: none; padding: 0;")
        
        description_label.setWordWrap(True)

        layout.addWidget(description_label)