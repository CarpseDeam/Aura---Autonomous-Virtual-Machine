"""
Diagnostic console widget showing agent event activity.

The widget listens to selected EventBus topics and renders a scrollable log so
operators can monitor what the autonomous agent is doing without inspecting
stdout.
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event


class AgentConsoleWidget(QWidget):
    """
    Lightweight console surface for streaming agent lifecycle updates.

    Responsibilities:
    - Subscribe to the EventBus for key agent lifecycle topics.
    - Render readable log entries that summarise each event.
    - Keep UI logic isolated from event orchestration code.
    """

    _DEFAULT_EVENT_TYPES: Iterable[str] = (
        "MODEL_CHUNK_RECEIVED",
        "MODEL_STREAM_ENDED",
        "MODEL_ERROR",
        "AGENT_STARTED",
        "AGENT_COMPLETED",
        "TASK_COMPLETED",
        "WORKFLOW_STATUS_UPDATE",
    )

    def __init__(self, event_bus: EventBus, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._event_bus = event_bus

        self.setObjectName("agent_console")
        self._console = QPlainTextEdit(self)
        self._console.setReadOnly(True)
        self._console.setObjectName("agent_console_text")
        self._console.setMaximumBlockCount(500)
        self._console.setStyleSheet(
            "QPlainTextEdit#agent_console_text {"
            "  background-color: #101820;"
            "  color: #7CC4FF;"
            "  font-family: 'JetBrains Mono', monospace;"
            "  font-size: 11px;"
            "  border: 1px solid rgba(255, 255, 255, 0.08);"
            "  border-radius: 4px;"
            "  padding: 6px;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._console)

        self._register_default_subscriptions()
        self._append_line("Agent console ready. Listening for events...", emphasise=True)

    def _register_default_subscriptions(self) -> None:
        for event_type in self._DEFAULT_EVENT_TYPES:
            self._event_bus.subscribe(event_type, self._handle_event)

    def _handle_event(self, event: Event) -> None:
        payload_str = ""
        payload = event.payload or {}
        if payload:
            try:
                payload_str = json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError):
                payload_str = str(payload)

        message = f"[{event.event_type}] {payload_str}".strip()
        self._append_line(message)

    def _append_line(self, message: str, *, emphasise: bool = False) -> None:
        if not message:
            return
        if emphasise:
            message = f"*** {message}"
        self._console.appendPlainText(message)
        cursor = self._console.textCursor()
        cursor.movePosition(cursor.End)
        self._console.setTextCursor(cursor)
        self._console.ensureCursorVisible()

