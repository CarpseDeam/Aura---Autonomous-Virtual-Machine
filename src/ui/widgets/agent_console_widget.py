from __future__ import annotations

from typing import Deque, List, Optional, Tuple
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
)

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.event_types import (
    TERMINAL_OUTPUT_RECEIVED,
    TERMINAL_SESSION_STARTED,
)


class AgentConsoleWidget(QWidget):
    """
    Real-time console widget showing terminal agent output.

    - Monospace, dark theme with green text; stderr lines in red
    - Read-only, auto-scrolls on new output
    - Timestamps rendered as [HH:MM:SS]
    - Shows active task_id in header; supports multiple concurrent tasks by prefixing lines
    - Clear button to reset the console
    """

    def __init__(self, event_bus: EventBus, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.event_bus = event_bus

        self._active_task_id: Optional[str] = None
        self._pending: Deque[Tuple[str, str, str]] = deque(maxlen=500)  # (task_id, stream, line)
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(100)  # 100ms batching
        self._flush_timer.timeout.connect(self._flush_pending)

        self._build_ui()
        self._subscribe_events()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header: active task + clear
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self.title_label = QLabel("Agent Console")
        self.title_label.setFont(QFont("JetBrains Mono", 10, QFont.Bold))
        self.title_label.setStyleSheet("color: #00ff00;")
        header.addWidget(self.title_label)

        self.active_label = QLabel("No active agent")
        self.active_label.setFont(QFont("JetBrains Mono", 9))
        self.active_label.setStyleSheet("color: #00aa00;")
        header.addWidget(self.active_label)
        header.addStretch()

        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear)
        header.addWidget(clear_button)

        layout.addLayout(header)

        # Console display area
        self.console = QTextEdit(self)
        self.console.setReadOnly(True)
        self.console.setFont(QFont("JetBrains Mono", 10))
        self.console.setStyleSheet(
            """
            QTextEdit {
                background-color: #1a1a1a;
                color: #00ff00;
                border: 1px solid #003300;
            }
            """
        )
        self.console.setPlaceholderText("Agent output will appear here...\n")
        layout.addWidget(self.console, 1)

    def _subscribe_events(self) -> None:
        self.event_bus.subscribe(TERMINAL_OUTPUT_RECEIVED, self._on_output)
        self.event_bus.subscribe(TERMINAL_SESSION_STARTED, self._on_session_started)

    def _on_session_started(self, event: Event) -> None:
        task_id = event.payload.get("task_id")
        if isinstance(task_id, str) and task_id:
            self._active_task_id = task_id
            self._update_active_label()

    def _on_output(self, event: Event) -> None:
        payload = event.payload
        task_id = str(payload.get("task_id", ""))
        text = str(payload.get("text", ""))
        stream = str(payload.get("stream_type", "stdout"))
        timestamp = str(payload.get("timestamp", ""))

        # Normalize timestamp to [HH:MM:SS]
        time_str = timestamp[-8:] if len(timestamp) >= 8 else timestamp
        line = f"[{time_str}] {text}"

        # Queue and start timer for batching
        self._pending.append((task_id, stream, line))
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending(self) -> None:
        if not self._pending:
            self._flush_timer.stop()
            return

        lines: List[str] = []
        while self._pending:
            task_id, stream, line = self._pending.popleft()
            # Prefix task ID if not the active one (or always when multiple agents)
            prefix = f"{task_id[:8]} | " if self._active_task_id and task_id != self._active_task_id else ""
            color = "#ff5555" if stream == "stderr" else "#00ff00"
            # Render as HTML line for color control
            html_line = f"<span style='color:{color}'>" + self._escape_html(prefix + line) + "</span>"
            lines.append(html_line)

        # Append to console
        if lines:
            self.console.moveCursor(self.console.textCursor().End)
            self.console.insertHtml("<br/>".join(lines) + "<br/>")
            self.console.moveCursor(self.console.textCursor().End)

        # Stop timer if queue drained
        if not self._pending:
            self._flush_timer.stop()

    def _update_active_label(self) -> None:
        if self._active_task_id:
            short = self._active_task_id[:12]
            self.active_label.setText(f"Active: {short}…")
        else:
            self.active_label.setText("No active agent")

    def clear(self) -> None:
        self.console.clear()

    @staticmethod
    def _escape_html(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("'", "&#39;")
        )

