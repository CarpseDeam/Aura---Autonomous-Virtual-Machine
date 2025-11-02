from __future__ import annotations

import logging
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
    TERMINAL_SESSION_COMPLETED,
    TERMINAL_SESSION_FAILED,
    TERMINAL_SESSION_TIMEOUT,
    TERMINAL_SESSION_ABORTED,
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
        self._logger = logging.getLogger(__name__)

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

        # Status badge
        self.status_badge = QLabel("Idle")
        self.status_badge.setObjectName("status_badge")
        self.status_badge.setAlignment(Qt.AlignCenter)
        self.status_badge.setMinimumWidth(70)
        self.status_badge.setFont(QFont("JetBrains Mono", 9, QFont.Bold))
        self._set_badge_style("idle")
        header.addWidget(self.status_badge)
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
        self.event_bus.subscribe(TERMINAL_SESSION_COMPLETED, self._on_session_status)
        self.event_bus.subscribe(TERMINAL_SESSION_FAILED, self._on_session_status)
        self.event_bus.subscribe(TERMINAL_SESSION_TIMEOUT, self._on_session_status)
        self.event_bus.subscribe(TERMINAL_SESSION_ABORTED, self._on_session_status)
        self._logger.debug("AgentConsoleWidget subscribed to terminal events")

    def _on_session_started(self, event: Event) -> None:
        task_id = event.payload.get("task_id")
        if isinstance(task_id, str) and task_id:
            self._active_task_id = task_id
            self._update_active_label()
            self._update_status_badge("running")
            self._logger.info("AgentConsoleWidget detected session start (task_id=%s)", task_id)

    def _on_session_status(self, event: Event) -> None:
        task_id = str(event.payload.get("task_id", ""))
        if not self._active_task_id or task_id != self._active_task_id:
            return
        mapping = {
            TERMINAL_SESSION_COMPLETED: "completed",
            TERMINAL_SESSION_FAILED: "failed",
            TERMINAL_SESSION_TIMEOUT: "timeout",
            TERMINAL_SESSION_ABORTED: "aborted",
        }
        status_key = mapping.get(event.event_type, "idle")
        self._update_status_badge(status_key)

    def _on_output(self, event: Event) -> None:
        payload = event.payload
        task_id = str(payload.get("task_id", ""))
        text = str(payload.get("text", ""))
        stream = str(payload.get("stream_type", "stdout"))
        timestamp = str(payload.get("timestamp", ""))
        self._logger.debug(
            "AgentConsoleWidget received output event (task_id=%s, stream=%s, text_length=%d)",
            task_id,
            stream,
            len(text),
        )

        # Normalize timestamp to [HH:MM:SS]
        time_str = timestamp[-8:] if len(timestamp) >= 8 else timestamp
        line = f"[{time_str}] {text}"

        # Queue and start timer for batching
        self._pending.append((task_id, stream, line))
        if not self._flush_timer.isActive():
            self._flush_timer.start()
            self._logger.debug("Starting console flush timer")

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
            self._logger.debug("Updating console display with %d new lines", len(lines))

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
        if not self._active_task_id:
            self._update_status_badge("idle")

    @staticmethod
    def _escape_html(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("'", "&#39;")
        )

    def _update_status_badge(self, status: str) -> None:
        self.status_badge.setText(status.upper())
        self._set_badge_style(status)

    def _set_badge_style(self, status: str) -> None:
        styles = {
            "running": ("#1a3a1a", "#00ff00", "#003300"),
            "completed": ("#1a2a1a", "#00ff00", "#003300"),
            "failed": ("#3a1a1a", "#ff5555", "#550000"),
            "timeout": ("#3a2a1a", "#ffcc66", "#664400"),
            "aborted": ("#2a2a2a", "#cccccc", "#444444"),
            "idle": ("#1f1f1f", "#aaaaaa", "#333333"),
        }
        bg, fg, border = styles.get(status, styles["idle"])
        self.status_badge.setStyleSheet(
            f"QLabel#status_badge {{ background-color: {bg}; color: {fg}; border: 1px solid {border};"
            " padding: 2px 6px; border-radius: 6px; }}"
        )
