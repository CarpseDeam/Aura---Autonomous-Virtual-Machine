import logging
from typing import List, Dict
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit
from PySide6.QtCore import Qt, Signal, QObject
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event

logger = logging.getLogger(__name__)


# Signaller to handle events safely in the UI thread from other threads
class TaskLogSignaller(QObject):
    tasks_updated = Signal(list)


class TaskLogWindow(QWidget):
    """
    The "Mission Control" window to display and manage the current task list.
    """
    TASK_LOG_STYLESHEET = """
        QWidget {
            background-color: #000000;
            color: #dcdcdc;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
            border: 1px solid #FFB74D; /* Amber */
            border-radius: 5px;
        }
        QLabel#mission_control_label {
            color: #FFB74D; /* Amber */
            font-weight: bold;
            font-size: 16px;
            padding: 5px;
            border: none;
            max-height: 25px; /* Give it a fixed height */
        }
        QLabel.task-item {
            font-size: 14px;
            border: none;
        }
        QLineEdit#task_input {
            background-color: #000000;
            border: 1px solid #4a4a4a; /* Subtle Grey */
            color: #dcdcdc;
            font-size: 14px;
            padding: 6px;
            border-radius: 5px;
        }
        QLineEdit#task_input:focus {
            border: 1px solid #FFB74D; /* Amber */
        }
    """

    def __init__(self, event_bus: EventBus, parent=None):
        """Initializes the TaskLogWindow."""
        super().__init__(parent)
        self.event_bus = event_bus
        self.signaller = TaskLogSignaller()
        self.setWindowTitle("Mission Control")
        # Makes it a floating tool window without a taskbar icon
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setGeometry(100, 100, 350, 700)
        self.setStyleSheet(self.TASK_LOG_STYLESHEET)
        self._init_ui()
        self._register_event_handlers()

    def _init_ui(self):
        """Initializes the user interface of the task log window."""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(10)

        title_label = QLabel("MISSION CONTROL")
        title_label.setObjectName("mission_control_label")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # This widget will hold the dynamically generated task labels
        self.task_list_container = QWidget()
        self.task_list_layout = QVBoxLayout(self.task_list_container)
        self.task_list_layout.setContentsMargins(0, 5, 0, 5)
        self.task_list_layout.setSpacing(8)
        self.task_list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.task_input = QLineEdit()
        self.task_input.setObjectName("task_input")
        self.task_input.setPlaceholderText("Add a new task...")
        self.task_input.returnPressed.connect(self._add_task_from_input)

        # New layout structure
        self.main_layout.addWidget(title_label)
        self.main_layout.addWidget(self.task_list_container, 1) # Task list takes all available space
        self.main_layout.addWidget(self.task_input)

    def _register_event_handlers(self):
        """Subscribe to events from the event bus."""
        self.signaller.tasks_updated.connect(self._on_tasks_updated)
        self.event_bus.subscribe(
            "TASK_LIST_UPDATED",
            lambda event: self.signaller.tasks_updated.emit(event.payload.get("tasks", []))
        )

    def _add_task_from_input(self):
        """Dispatches an event to add a new task from the input field."""
        description = self.task_input.text().strip()
        if not description:
            return

        self.task_input.clear()
        logger.info(f"User added new task via input: '{description}'")
        self.event_bus.dispatch(Event(
            event_type="ADD_TASK",
            payload={"description": description}
        ))

    def _on_tasks_updated(self, tasks: List[Dict]):
        """
        Clears and redraws the task list in the UI.
        """
        # Clear existing task widgets
        while self.task_list_layout.count():
            child = self.task_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # Add new task widgets
        if not tasks:
            placeholder = QLabel("No active tasks.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("border: none;") # Ensure placeholder has no border
            self.task_list_layout.addWidget(placeholder)
        else:
            for task_data in tasks:
                # Using a simple circle character for "pending"
                task_label = QLabel(f"○ {task_data.get('description')}")
                task_label.setObjectName("task-item")
                self.task_list_layout.addWidget(task_label)