"""
Terminal Session Panel - UI widget for monitoring terminal agent sessions.

Displays active and completed terminal sessions with status information.
"""

from datetime import datetime
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
)
from PySide6.QtCore import Signal
from PySide6.QtGui import QFont

from src.aura.models.events import Event
from src.aura.models.event_types import (
    TERMINAL_SESSION_STARTED,
    TERMINAL_SESSION_COMPLETED,
    TERMINAL_SESSION_FAILED,
    TERMINAL_SESSION_TIMEOUT,
    TERMINAL_SESSION_ABORTED,
)


class SessionWidget(QFrame):
    """Widget displaying a single terminal session."""

    abort_requested = Signal(str)  # Emits task_id when abort is clicked

    def __init__(self, task_id: str, status: str, started_at: str, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.status = status

        self.setFrameStyle(QFrame.Box | QFrame.Raised)
        self.setLineWidth(1)

        # Set background color based on status
        self._update_style()

        # Create layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # Task ID label
        self.task_label = QLabel(f"Task: {task_id[:12]}...")
        self.task_label.setFont(QFont("JetBrains Mono", 9))
        layout.addWidget(self.task_label)

        # Status label
        self.status_label = QLabel(self._format_status(status))
        self.status_label.setFont(QFont("JetBrains Mono", 9, QFont.Bold))
        layout.addWidget(self.status_label)

        # Time label
        try:
            started_dt = datetime.fromisoformat(started_at)
            time_str = started_dt.strftime("%H:%M:%S")
        except:
            time_str = "??:??:??"
        self.time_label = QLabel(time_str)
        self.time_label.setFont(QFont("JetBrains Mono", 8))
        layout.addWidget(self.time_label)

        layout.addStretch()

        # Abort button (only for running sessions)
        if status == "running":
            self.abort_button = QPushButton("Abort")
            self.abort_button.setMaximumWidth(60)
            self.abort_button.clicked.connect(lambda: self.abort_requested.emit(self.task_id))
            layout.addWidget(self.abort_button)

    def _format_status(self, status: str) -> str:
        """Format status for display."""
        status_map = {
            "running": "⚙ RUNNING",
            "completed": "✓ COMPLETED",
            "failed": "✗ FAILED",
            "timeout": "⏱ TIMEOUT",
            "aborted": "◼ ABORTED",
        }
        return status_map.get(status, status.upper())

    def _update_style(self):
        """Update widget style based on status."""
        color_map = {
            "running": "#1a3a1a",  # Dark green
            "completed": "#1a2a1a",  # Darker green
            "failed": "#3a1a1a",  # Dark red
            "timeout": "#3a2a1a",  # Dark orange
            "aborted": "#2a2a2a",  # Gray
        }
        bg_color = color_map.get(self.status, "#1a1a1a")
        self.setStyleSheet(f"background-color: {bg_color}; color: #00ff00; border: 1px solid #003300;")

    def update_status(self, new_status: str):
        """Update the status of this session widget."""
        self.status = new_status
        self.status_label.setText(self._format_status(new_status))
        self._update_style()

        # Remove abort button if no longer running
        if hasattr(self, "abort_button") and new_status != "running":
            self.abort_button.setVisible(False)


class TerminalSessionPanel(QWidget):
    """
    Panel widget for displaying and managing terminal agent sessions.

    Displays active sessions at the top and completed sessions below.
    Subscribes to session lifecycle events to update the display.
    """

    def __init__(self, event_bus, terminal_session_manager, parent=None):
        super().__init__(parent)
        self.event_bus = event_bus
        self.terminal_session_manager = terminal_session_manager
        self.session_widgets = {}  # task_id -> SessionWidget

        self._setup_ui()
        self._subscribe_to_events()

    def _setup_ui(self):
        """Setup the panel UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # Title
        title = QLabel("Terminal Sessions")
        title.setFont(QFont("JetBrains Mono", 11, QFont.Bold))
        title.setStyleSheet("color: #00ff00; padding: 5px;")
        main_layout.addWidget(title)

        # Active sessions section
        active_label = QLabel("Active:")
        active_label.setFont(QFont("JetBrains Mono", 9))
        active_label.setStyleSheet("color: #00aa00; padding: 2px;")
        main_layout.addWidget(active_label)

        # Scroll area for active sessions
        self.active_scroll = QScrollArea()
        self.active_scroll.setWidgetResizable(True)
        self.active_scroll.setMaximumHeight(200)
        self.active_scroll.setStyleSheet("border: 1px solid #003300; background-color: #0a0a0a;")

        self.active_container = QWidget()
        self.active_layout = QVBoxLayout(self.active_container)
        self.active_layout.setContentsMargins(2, 2, 2, 2)
        self.active_layout.setSpacing(2)
        self.active_layout.addStretch()

        self.active_scroll.setWidget(self.active_container)
        main_layout.addWidget(self.active_scroll)

        # Completed sessions section
        completed_label = QLabel("Completed:")
        completed_label.setFont(QFont("JetBrains Mono", 9))
        completed_label.setStyleSheet("color: #00aa00; padding: 2px;")
        main_layout.addWidget(completed_label)

        # Scroll area for completed sessions
        self.completed_scroll = QScrollArea()
        self.completed_scroll.setWidgetResizable(True)
        self.completed_scroll.setStyleSheet("border: 1px solid #003300; background-color: #0a0a0a;")

        self.completed_container = QWidget()
        self.completed_layout = QVBoxLayout(self.completed_container)
        self.completed_layout.setContentsMargins(2, 2, 2, 2)
        self.completed_layout.setSpacing(2)
        self.completed_layout.addStretch()

        self.completed_scroll.setWidget(self.completed_container)
        main_layout.addWidget(self.completed_scroll)

        # Set panel styling
        self.setStyleSheet("""
            QWidget {
                background-color: #0a0a0a;
                color: #00ff00;
            }
        """)

    def _subscribe_to_events(self):
        """Subscribe to terminal session events."""
        self.event_bus.subscribe(TERMINAL_SESSION_STARTED, self._handle_session_started)
        self.event_bus.subscribe(TERMINAL_SESSION_COMPLETED, self._handle_session_completed)
        self.event_bus.subscribe(TERMINAL_SESSION_FAILED, self._handle_session_failed)
        self.event_bus.subscribe(TERMINAL_SESSION_TIMEOUT, self._handle_session_timeout)
        self.event_bus.subscribe(TERMINAL_SESSION_ABORTED, self._handle_session_aborted)

    def _handle_session_started(self, event: Event):
        """Handle new session started."""
        payload = event.payload
        task_id = payload.get("task_id")
        started_at = payload.get("started_at", "")

        # Create widget for new session
        widget = SessionWidget(task_id, "running", started_at, self)
        widget.abort_requested.connect(self._abort_session)
        self.session_widgets[task_id] = widget

        # Add to active section (insert before stretch)
        self.active_layout.insertWidget(self.active_layout.count() - 1, widget)

    def _handle_session_completed(self, event: Event):
        """Handle session completed."""
        self._move_to_completed(event.payload.get("task_id"), "completed")

    def _handle_session_failed(self, event: Event):
        """Handle session failed."""
        self._move_to_completed(event.payload.get("task_id"), "failed")

    def _handle_session_timeout(self, event: Event):
        """Handle session timeout."""
        self._move_to_completed(event.payload.get("task_id"), "timeout")

    def _handle_session_aborted(self, event: Event):
        """Handle session aborted."""
        self._move_to_completed(event.payload.get("task_id"), "aborted")

    def _move_to_completed(self, task_id: str, status: str):
        """Move a session from active to completed section."""
        widget = self.session_widgets.get(task_id)
        if not widget:
            return

        # Update status
        widget.update_status(status)

        # Remove from active layout
        self.active_layout.removeWidget(widget)

        # Add to completed section (insert before stretch)
        self.completed_layout.insertWidget(0, widget)  # Add at top

        # Limit completed sessions displayed (keep last 20)
        if self.completed_layout.count() > 21:  # 20 + stretch
            old_widget = self.completed_layout.itemAt(20).widget()
            if old_widget:
                self.completed_layout.removeWidget(old_widget)
                task_id_to_remove = old_widget.task_id
                old_widget.deleteLater()
                if task_id_to_remove in self.session_widgets:
                    del self.session_widgets[task_id_to_remove]

    def _abort_session(self, task_id: str):
        """Handle abort request for a session."""
        success = self.terminal_session_manager.abort_session(task_id)
        if not success:
            # Optionally show an error message
            pass

    def refresh_display(self):
        """Refresh the display from the terminal session manager."""
        # Clear existing widgets
        for widget in self.session_widgets.values():
            widget.deleteLater()
        self.session_widgets.clear()

        # Add active sessions
        for status in self.terminal_session_manager.get_active_sessions():
            started_at = status.started_at.isoformat()
            widget = SessionWidget(status.session.task_id, status.status, started_at, self)
            widget.abort_requested.connect(self._abort_session)
            self.session_widgets[status.session.task_id] = widget
            self.active_layout.insertWidget(self.active_layout.count() - 1, widget)

        # Add completed sessions
        for status in self.terminal_session_manager.get_completed_sessions(limit=20):
            started_at = status.started_at.isoformat()
            widget = SessionWidget(status.session.task_id, status.status, started_at, self)
            self.session_widgets[status.session.task_id] = widget
            self.completed_layout.insertWidget(self.completed_layout.count() - 1, widget)
