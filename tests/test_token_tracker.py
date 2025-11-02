from __future__ import annotations

from typing import Callable, Dict, List

import pytest

from src.aura.models.events import Event
from src.aura.models.event_types import (
    CONVERSATION_MESSAGE_ADDED,
    CONVERSATION_SESSION_STARTED,
    TOKEN_THRESHOLD_CROSSED,
    TOKEN_USAGE_UPDATED,
)
from src.aura.services.token_tracker import TokenTracker


class StubEventBus:
    """Minimal event bus stub providing subscribe/dispatch for tests."""

    def __init__(self) -> None:
        self.subscribers: Dict[str, List[Callable[[Event], None]]] = {}
        self.dispatched: List[Event] = []

    def subscribe(self, event_type: str, callback: Callable[[Event], None]) -> None:
        self.subscribers.setdefault(event_type, []).append(callback)

    def dispatch(self, event: Event) -> None:
        self.dispatched.append(event)
        for callback in self.subscribers.get(event.event_type, []):
            callback(event)


def _latest_event(events: List[Event], event_type: str) -> Event:
    matches = [evt for evt in events if evt.event_type == event_type]
    if not matches:
        raise AssertionError(f"No events of type {event_type} were dispatched.")
    return matches[-1]


def test_token_tracker_resets_on_session_start() -> None:
    bus = StubEventBus()
    TokenTracker(event_bus=bus, token_limit=100)

    bus.dispatch(Event(event_type=CONVERSATION_SESSION_STARTED, payload={"session_id": "session-1"}))
    usage_event = _latest_event(bus.dispatched, TOKEN_USAGE_UPDATED)

    assert usage_event.payload["current_tokens"] == 0
    assert usage_event.payload["token_limit"] == 100
    assert usage_event.payload["percent_used"] == pytest.approx(0.0)


def test_token_tracker_emits_threshold_events() -> None:
    bus = StubEventBus()
    TokenTracker(event_bus=bus, token_limit=200)

    bus.dispatch(Event(event_type=CONVERSATION_SESSION_STARTED, payload={"session_id": "session-xyz"}))
    bus.dispatched.clear()

    # First assistant response: no thresholds crossed.
    bus.dispatch(
        Event(
            event_type=CONVERSATION_MESSAGE_ADDED,
            payload={
                "session_id": "session-xyz",
                "role": "assistant",
                "token_usage": {"total_tokens": 60},
            },
        )
    )
    usage_event = _latest_event(bus.dispatched, TOKEN_USAGE_UPDATED)
    assert usage_event.payload["current_tokens"] == 60
    assert not [evt for evt in bus.dispatched if evt.event_type == TOKEN_THRESHOLD_CROSSED]

    bus.dispatched.clear()

    # Second response pushes us past 70%.
    bus.dispatch(
        Event(
            event_type=CONVERSATION_MESSAGE_ADDED,
            payload={
                "session_id": "session-xyz",
                "role": "assistant",
                "token_usage": {"total_tokens": 90},
            },
        )
    )
    threshold_events = [evt for evt in bus.dispatched if evt.event_type == TOKEN_THRESHOLD_CROSSED]
    assert len(threshold_events) == 1
    seventy_event = threshold_events[0]
    assert seventy_event.payload["threshold"] == pytest.approx(0.70)
    assert seventy_event.payload["current_tokens"] == 150
    assert seventy_event.payload["percent_used"] == pytest.approx(0.75)

    bus.dispatched.clear()

    # Third response pushes us past 85%.
    bus.dispatch(
        Event(
            event_type=CONVERSATION_MESSAGE_ADDED,
            payload={
                "session_id": "session-xyz",
                "role": "assistant",
                "token_usage": {"total_tokens": 50},
            },
        )
    )
    threshold_events = [evt for evt in bus.dispatched if evt.event_type == TOKEN_THRESHOLD_CROSSED]
    assert len(threshold_events) == 1
    eighty_five_event = threshold_events[0]
    assert eighty_five_event.payload["threshold"] == pytest.approx(0.85)
    assert eighty_five_event.payload["current_tokens"] == 200
    assert eighty_five_event.payload["percent_used"] == pytest.approx(1.0)
