from __future__ import annotations

import logging
from typing import Dict, Iterable, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.event_types import (
    CONVERSATION_MESSAGE_ADDED,
    CONVERSATION_SESSION_STARTED,
    TOKEN_THRESHOLD_CROSSED,
    TOKEN_USAGE_UPDATED,
)


logger = logging.getLogger(__name__)


class TokenTracker:
    """
    Track running token consumption for the active conversation session.

    Responsibilities:
    - Reset counters when a new session starts.
    - Accumulate token usage reported with assistant messages.
    - Estimate token usage heuristically when providers omit usage metadata.
    - Emit usage updates and threshold warnings over the event bus for UI consumption.
    """

    _DEFAULT_LIMIT = 200_000
    _THRESHOLDS: Iterable[float] = (0.70, 0.85)

    def __init__(self, event_bus: EventBus, token_limit: int = _DEFAULT_LIMIT) -> None:
        self.event_bus = event_bus
        self.token_limit = max(token_limit, 1)
        self.current_tokens = 0
        self.current_session_id: Optional[str] = None
        self._thresholds_crossed: Dict[float, bool] = {threshold: False for threshold in self._THRESHOLDS}

        self._register_event_handlers()
        logger.info(
            "TokenTracker initialized (limit=%d, thresholds=%s)",
            self.token_limit,
            ", ".join(f"{int(t * 100)}%" for t in self._THRESHOLDS),
        )

    # ------------------------------------------------------------------ #
    # Event wiring
    # ------------------------------------------------------------------ #

    def _register_event_handlers(self) -> None:
        self.event_bus.subscribe(CONVERSATION_SESSION_STARTED, self._handle_session_started)
        self.event_bus.subscribe(CONVERSATION_MESSAGE_ADDED, self._handle_message_added)

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    def _handle_session_started(self, event: Event) -> None:
        payload = event.payload or {}
        session_id = payload.get("session_id")
        if not session_id:
            logger.debug("Session started event missing session_id; ignoring payload=%s", payload)
            return

        self.current_session_id = session_id
        self.current_tokens = 0
        for threshold in self._thresholds_crossed:
            self._thresholds_crossed[threshold] = False

        logger.debug("TokenTracker reset for session %s", session_id)
        self._emit_usage_update()

    def _handle_message_added(self, event: Event) -> None:
        payload = event.payload or {}
        session_id = payload.get("session_id")
        if not session_id or session_id != self.current_session_id:
            return

        role = str(payload.get("role") or "").lower()
        if role != "assistant":
            # Only count assistant messages; token usage payloads typically
            # include both prompt and completion tokens when provided.
            return

        tokens = self._extract_token_usage(payload)
        if tokens <= 0:
            logger.debug(
                "TokenTracker skipping message with non-positive token estimate (session=%s, role=%s)",
                session_id,
                role,
            )
            return

        self.current_tokens += tokens
        logger.debug(
            "TokenTracker recorded %d tokens (session=%s, cumulative=%d)",
            tokens,
            session_id,
            self.current_tokens,
        )
        self._emit_usage_update()
        self._emit_thresholds_if_needed()

    # ------------------------------------------------------------------ #
    # Token accounting helpers
    # ------------------------------------------------------------------ #

    def _extract_token_usage(self, payload: Dict[str, object]) -> int:
        usage = payload.get("token_usage")
        if usage is None:
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                usage = metadata.get("token_usage")

        if isinstance(usage, dict):
            total_keys = ("total_tokens", "total", "token_total")
            for key in total_keys:
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    return max(int(value), 0)

            prompt_tokens = self._collect_numeric_values(
                usage,
                ("prompt_tokens", "input_tokens", "promptTokenCount"),
            )
            completion_tokens = self._collect_numeric_values(
                usage,
                ("completion_tokens", "output_tokens", "completionTokenCount"),
            )
            if prompt_tokens or completion_tokens:
                return sum(prompt_tokens + completion_tokens)

        if isinstance(usage, (int, float)):
            return max(int(usage), 0)

        estimated_tokens = payload.get("estimated_tokens")
        if isinstance(estimated_tokens, (int, float)):
            return max(int(estimated_tokens), 0)

        content = payload.get("content")
        if isinstance(content, str):
            return self._estimate_tokens(content)

        return 0

    @staticmethod
    def _collect_numeric_values(mapping: Dict[str, object], keys: Iterable[str]) -> list[int]:
        values: list[int] = []
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, (int, float)):
                values.append(int(value))
        return values

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        Rough heuristic: assume 4 characters per token with a minimum floor of 1.
        """
        normalized = text.strip()
        if not normalized:
            return 0
        approx = max(1, int((len(normalized) + 3) / 4))
        return approx

    # ------------------------------------------------------------------ #
    # Event emission helpers
    # ------------------------------------------------------------------ #

    def _emit_usage_update(self) -> None:
        percent_used = self.current_tokens / self.token_limit
        try:
            self.event_bus.dispatch(
                Event(
                    event_type=TOKEN_USAGE_UPDATED,
                    payload={
                        "session_id": self.current_session_id,
                        "current_tokens": self.current_tokens,
                        "token_limit": self.token_limit,
                        "percent_used": percent_used,
                    },
                )
            )
        except Exception:
            logger.debug("Failed to dispatch TOKEN_USAGE_UPDATED event", exc_info=True)

    def _emit_thresholds_if_needed(self) -> None:
        if self.token_limit <= 0:
            return
        percent_used = self.current_tokens / self.token_limit
        for threshold in sorted(self._thresholds_crossed.keys()):
            if percent_used >= threshold and not self._thresholds_crossed[threshold]:
                self._thresholds_crossed[threshold] = True
                try:
                    self.event_bus.dispatch(
                        Event(
                            event_type=TOKEN_THRESHOLD_CROSSED,
                            payload={
                                "session_id": self.current_session_id,
                                "threshold": threshold,
                                "current_tokens": self.current_tokens,
                                "token_limit": self.token_limit,
                                "percent_used": percent_used,
                            },
                        )
                    )
                except Exception:
                    logger.debug("Failed to dispatch TOKEN_THRESHOLD_CROSSED event", exc_info=True)
