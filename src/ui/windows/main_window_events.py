from __future__ import annotations

import logging
from typing import Dict, Optional

from PySide6.QtCore import QUrl, QUrlQuery
from PySide6.QtGui import QDesktopServices

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.services.conversation_management_service import ConversationManagementService
from src.ui.widgets.chat_display_widget import ChatDisplayWidget
from src.ui.widgets.chat_input_widget import ChatInputWidget
from src.ui.widgets.thinking_indicator_widget import ThinkingIndicatorWidget
from src.ui.widgets.toolbar_widget import ToolbarWidget
from src.ui.windows.main_window_signals import MainWindowSignaller

logger = logging.getLogger(__name__)


class MainWindowEventController:
    """
    Coordinates EventBus updates with UI widgets for the main window.
    """

    def __init__(
        self,
        event_bus: EventBus,
        chat_display: ChatDisplayWidget,
        toolbar: ToolbarWidget,
        thinking_indicator: ThinkingIndicatorWidget,
        chat_input: ChatInputWidget,
        *,
        auto_accept_enabled: bool,
        conversations: ConversationManagementService,
    ) -> None:
        self.event_bus = event_bus
        self.chat_display = chat_display
        self.toolbar = toolbar
        self.thinking_indicator = thinking_indicator
        self.chat_input = chat_input
        self.conversations = conversations

        self.pending_change_states: Dict[str, str] = {}
        self.token_usage_limit: int = 200_000
        self.current_token_usage: int = 0
        self._auto_accept_enabled = auto_accept_enabled

        self._is_streaming_response = False
        self._response_buffer = ""

        # Cache per-thread UI state (scroll positions)
        self._thread_scroll_positions: Dict[str, int] = {}
        self._active_session_id: Optional[str] = None

        self._signaller = MainWindowSignaller()
        self._signaller.chunk_received.connect(self._handle_model_chunk)
        self._signaller.stream_ended.connect(self._handle_stream_end)
        self._signaller.error_received.connect(self._handle_model_error)

    def register(self) -> None:
        """
        Subscribe to EventBus topics required by the main window UI.
        """
        self.event_bus.subscribe("MODEL_CHUNK_RECEIVED", self._emit_model_chunk)
        self.event_bus.subscribe("MODEL_STREAM_ENDED", self._emit_stream_end)
        self.event_bus.subscribe("MODEL_ERROR", self._emit_model_error)

        self.event_bus.subscribe("TASK_PLAN_GENERATED", self._handle_task_plan_generated)
        self.event_bus.subscribe("DISPATCH_TASK", self._handle_task_dispatch)
        self.event_bus.subscribe("GENERATION_PROGRESS", self._handle_generation_progress)
        self.event_bus.subscribe("WORKFLOW_STATUS_UPDATE", self._handle_workflow_status_update)

        self.event_bus.subscribe("PROJECT_ACTIVATED", self._handle_project_activated)
        self.event_bus.subscribe("PROJECT_IMPORTED", self._handle_project_imported)
        self.event_bus.subscribe("PROJECT_IMPORT_ERROR", self._handle_project_import_error)
        self.event_bus.subscribe("VALIDATED_CODE_SAVED", self._handle_validated_code_saved)

        self.event_bus.subscribe("FILE_DIFF_READY", self._handle_file_diff_ready)
        self.event_bus.subscribe("FILE_CHANGES_APPLIED", self._handle_file_changes_applied)
        self.event_bus.subscribe("FILE_CHANGES_REJECTED", self._handle_file_changes_rejected)

        self.event_bus.subscribe("USER_PREFERENCES_UPDATED", self._handle_preferences_updated)
        self.event_bus.subscribe("BLUEPRINT_GENERATED", self._handle_blueprint_generated)
        self.event_bus.subscribe("BUILD_COMPLETED", self._handle_build_completed)
        self.event_bus.subscribe("TOKEN_USAGE_UPDATED", self._handle_token_usage_updated)
        self.event_bus.subscribe("TOKEN_THRESHOLD_CROSSED", self._handle_token_threshold_crossed)
        # Track conversation switches for scroll preservation and history loading
        self.event_bus.subscribe("CONVERSATION_SESSION_STARTED", self._handle_session_switched)
        self.event_bus.subscribe("CONVERSATION_THREAD_SWITCHED", self._handle_thread_switched)

        # Optional lifecycle signals
        self.event_bus.subscribe("AGENT_STARTED", self._handle_agent_started)
        self.event_bus.subscribe("AGENT_COMPLETED", self._handle_agent_completed)
        self.event_bus.subscribe("TASK_COMPLETED", self._handle_task_completed)
        self.event_bus.subscribe("FILE_GENERATED", self._handle_file_generated)

    def handle_anchor_clicked(self, url: QUrl) -> None:
        """
        Process anchor clicks from the chat display for diff actions.
        """
        if url.scheme() != "aura":
            QDesktopServices.openUrl(url)
            return

        action = url.host() or url.path().lstrip("/")
        query = QUrlQuery(url)

        change_id = query.queryItemValue("change_id")
        if not change_id:
            return

        current_state = self.pending_change_states.get(change_id, "pending")
        if current_state != "pending":
            logger.debug("Ignoring action %s for change %s in state %s", action, change_id, current_state)
            return

        if action == "accept":
            self.pending_change_states[change_id] = "applying"
            self.chat_display.display_system_message("SYSTEM", f"Applying change {self._short_change_id(change_id)}...")
            self.event_bus.dispatch(Event(event_type="APPLY_FILE_CHANGES", payload={"change_id": change_id}))
        elif action == "reject":
            self.pending_change_states[change_id] = "rejecting"
            self.chat_display.display_system_message("SYSTEM", f"Rejecting change {self._short_change_id(change_id)}...")
            self.event_bus.dispatch(Event(event_type="REJECT_FILE_CHANGES", payload={"change_id": change_id}))

    def _handle_task_plan_generated(self, event: Event) -> None:
        payload = event.payload or {}
        task_description = payload.get("task_description")
        if task_description:
            self.thinking_indicator.stop_thinking()
            self.chat_display.display_task_plan(task_description)

    def _emit_model_chunk(self, event: Event) -> None:
        chunk = (event.payload or {}).get("chunk", "")
        self._signaller.chunk_received.emit(chunk)

    def _emit_stream_end(self, _event: Event) -> None:
        self._signaller.stream_ended.emit()

    def _emit_model_error(self, event: Event) -> None:
        message = (event.payload or {}).get("message", "Unknown error")
        self._signaller.error_received.emit(message)

    def _handle_model_chunk(self, chunk: str) -> None:
        if not self._is_streaming_response:
            self._is_streaming_response = True
            self.thinking_indicator.stop_thinking()
            self._response_buffer = ""
        self._response_buffer += chunk

    def _handle_stream_end(self) -> None:
        if self._is_streaming_response and self._response_buffer.strip():
            self.chat_display.display_aura_response(self._response_buffer.strip())
        self._response_buffer = ""
        self._is_streaming_response = False
        self.chat_input.setEnabled(True)
        self.chat_input.focus_input()

    def _handle_model_error(self, error_message: str) -> None:
        self.thinking_indicator.stop_thinking()
        self.chat_display.display_error(error_message)
        self._response_buffer = ""
        self._is_streaming_response = False
        self.chat_input.setEnabled(True)
        self.chat_input.focus_input()

    def _handle_task_dispatch(self, event: Event) -> None:
        if self.thinking_indicator.is_animating:
            self.thinking_indicator.set_thinking_message("Engineering your solution...")
        desc = (event.payload or {}).get("task_description", "Task")
        self.chat_display.display_system_message("SYSTEM", f"Task dispatched: {desc}")

    def _handle_agent_started(self, event: Event) -> None:
        agent_name = (event.payload or {}).get("agent_name", "Agent")
        self.chat_display.display_system_message("KERNEL", f"{agent_name.upper()} ONLINE")

    def _handle_agent_completed(self, event: Event) -> None:
        payload = event.payload or {}
        agent_name = payload.get("agent_name", "Agent")
        status = payload.get("status", "completed")
        self.chat_display.display_system_message("KERNEL", f"{agent_name.upper()} task {status.upper()}")

    def _handle_task_completed(self, event: Event) -> None:
        desc = (event.payload or {}).get("task_description", "Task")
        self.chat_display.display_system_message("SYSTEM", f"Task completed: {desc}")

    def _handle_file_generated(self, event: Event) -> None:
        payload = event.payload or {}
        file_path = payload.get("file_path", "unknown")
        operation = payload.get("operation", "generated")
        self.chat_display.display_system_message("NEURAL", f"File {operation}: {file_path}")

    def _handle_generation_progress(self, event: Event) -> None:
        payload = event.payload or {}
        message = payload.get("message")
        if not message:
            return
        category = (payload.get("category") or "SYSTEM").upper()
        details = payload.get("details") or []
        self.chat_display.display_system_message(category, message)
        for detail in details:
            self.chat_display.display_system_message("DEFAULT", f"  {detail}")

    def _handle_workflow_status_update(self, event: Event) -> None:
        payload = event.payload or {}
        message = payload.get("message")
        if not message:
            return
        status = payload.get("status", "info")
        code_snippet = payload.get("code_snippet")
        details = payload.get("details") or []
        status_to_category = {
            "success": "SUCCESS",
            "in-progress": "SYSTEM",
            "error": "ERROR",
            "info": "SYSTEM",
        }
        category = status_to_category.get(status, "SYSTEM")
        self.chat_display.display_system_message(category, message)
        if code_snippet:
            for line in code_snippet.splitlines():
                self.chat_display.display_system_message("DEFAULT", f"  {line}")
        for detail in details:
            self.chat_display.display_system_message("DEFAULT", f"  {detail}")

    def _handle_project_activated(self, event: Event) -> None:
        payload = event.payload or {}
        project_name = payload.get("project_name", "Unknown")
        self.chat_display.display_system_message("WORKSPACE", f"Project '{project_name}' activated and indexed")

    def _handle_project_imported(self, event: Event) -> None:
        payload = event.payload or {}
        project_name = payload.get("project_name", "Unknown")
        source_path = payload.get("source_path", "")
        self.chat_display.display_system_message("WORKSPACE", f"Project '{project_name}' imported from {source_path}")

    def _handle_project_import_error(self, event: Event) -> None:
        error = (event.payload or {}).get("error", "Unknown error")
        self.chat_display.display_system_message("ERROR", f"Project import failed: {error}")

    def _handle_validated_code_saved(self, event: Event) -> None:
        payload = event.payload or {}
        file_path = payload.get("file_path", "unknown")
        line_count = payload.get("line_count")
        if line_count is None:
            self.chat_display.display_system_message("SUCCESS", f"Saved {file_path}")
        else:
            self.chat_display.display_system_message("SUCCESS", f"Wrote {line_count} lines to {file_path}")

    def _handle_file_diff_ready(self, event: Event) -> None:
        payload = event.payload or {}
        change_id = payload.get("change_id")
        files = payload.get("files") or []
        if not change_id or not files:
            return

        pending = bool(payload.get("pending"))
        auto_applied = bool(payload.get("auto_applied"))

        state = "pending" if pending else ("applied_auto" if auto_applied else "applied")
        self.pending_change_states[change_id] = state

        self.chat_display.display_diff_message(payload, pending=pending, auto_applied=auto_applied)

        if pending:
            self.chat_display.display_system_message(
                "SYSTEM",
                f"Review pending change {self._short_change_id(change_id)} and choose Accept or Reject.",
            )
        elif auto_applied:
            self.chat_display.display_system_message(
                "SUCCESS",
                f"Changes auto-applied ({self._short_change_id(change_id)}).",
            )

    def _handle_file_changes_applied(self, event: Event) -> None:
        payload = event.payload or {}
        change_id = payload.get("change_id")
        auto_applied = bool(payload.get("auto_applied"))
        if change_id:
            self.pending_change_states[change_id] = "applied"
        status = "Changes auto-applied" if auto_applied else "Changes written to workspace"
        self.chat_display.display_system_message("SUCCESS", f"{status} ({self._short_change_id(change_id)})")

    def _handle_file_changes_rejected(self, event: Event) -> None:
        payload = event.payload or {}
        change_id = payload.get("change_id")
        if change_id:
            self.pending_change_states[change_id] = "rejected"
        self.chat_display.display_system_message("SYSTEM", f"Discarded change {self._short_change_id(change_id)}.")

    def _handle_preferences_updated(self, event: Event) -> None:
        prefs = (event.payload or {}).get("preferences") or {}
        if "auto_accept_changes" not in prefs:
            return
        new_value = bool(prefs.get("auto_accept_changes"))
        if new_value == self._auto_accept_enabled:
            return
        self._auto_accept_enabled = new_value
        self.toolbar.set_auto_accept_enabled(new_value)
        status_text = "Auto-accept enabled" if new_value else "Auto-accept disabled"
        self.chat_display.display_system_message("SYSTEM", status_text)

    def _handle_blueprint_generated(self, event: Event) -> None:
        blueprint_data = event.payload or {}
        files = blueprint_data.get("files") or []
        file_count = len([f for f in files if isinstance(f, dict)])
        total_tasks = (
            sum(len(((f or {}).get("functions") or [])) for f in files)
            + sum(
                len(((c or {}).get("methods") or []))
                for f in files
                for c in ((f or {}).get("classes") or [])
            )
        )
        project_name = blueprint_data.get("project_name", "Project")
        self.chat_display.display_system_message(
            "SYSTEM",
            f"Blueprint for '{project_name}' generated: {file_count} files, {total_tasks} tasks.",
        )

    def _handle_build_completed(self, _event: Event) -> None:
        self.thinking_indicator.stop_thinking()
        self.chat_display.display_system_message("SUCCESS", "Build completed successfully. Aura is ready.")
        self.chat_input.setEnabled(True)
        self.chat_input.focus_input()

    def _handle_token_usage_updated(self, event: Event) -> None:
        payload = event.payload or {}
        limit = int(payload.get("token_limit") or self.token_usage_limit or 1)
        current = int(payload.get("current_tokens") or 0)
        percent = payload.get("percent_used")
        try:
            percent_value = float(percent) if percent is not None else current / max(limit, 1)
        except (TypeError, ValueError):
            percent_value = current / max(limit, 1)

        self.token_usage_limit = max(limit, 1)
        self.current_token_usage = max(current, 0)
        self.toolbar.update_token_usage(self.current_token_usage, self.token_usage_limit, percent_value)

    def _handle_token_threshold_crossed(self, event: Event) -> None:
        payload = event.payload or {}
        threshold = float(payload.get("threshold", 0.0))
        limit = int(payload.get("token_limit") or self.token_usage_limit or 1)
        current = int(payload.get("current_tokens") or 0)
        percent = float(payload.get("percent_used", current / max(limit, 1)))
        message = (
            f"Token usage at {percent * 100:.0f}% "
            f"({self._format_token_count(current)} / {self._format_token_count(limit)}). "
            "Consider starting a new session before we exceed the context window."
        )
        logger.warning(
            "Token usage threshold %.0f%% crossed (tokens=%d, limit=%d)",
            threshold * 100,
            current,
            limit,
        )
        self.chat_display.display_system_message("WARNING", message)

    @staticmethod
    def _format_token_count(tokens: int) -> str:
        absolute = abs(tokens)
        if absolute >= 1_000_000:
            value = tokens / 1_000_000
            return f"{value:.1f}m" if not value.is_integer() else f"{int(value)}m"
        if absolute >= 1_000:
            value = tokens / 1_000
            return f"{value:.1f}k" if not value.is_integer() else f"{int(value)}k"
        return str(tokens)

    @staticmethod
    def _short_change_id(change_id: Optional[str]) -> str:
        if not change_id:
            return "N/A"
        return change_id[:8].upper()

    def _handle_session_switched(self, event: Event) -> None:
        """On session activation, load history, reset input, and manage scroll state."""
        try:
            # Save previous thread scroll position
            if self._active_session_id:
                try:
                    bar = self.chat_display.verticalScrollBar()
                    self._thread_scroll_positions[self._active_session_id] = int(bar.value())
                except Exception:
                    logger.debug("Failed saving scroll position", exc_info=True)

            payload = event.payload or {}
            new_session_id = payload.get("session_id")
            self._active_session_id = new_session_id
            if not new_session_id:
                return

            # Show subtle loading while we fetch from persistence
            try:
                self.thinking_indicator.start_thinking("Loading conversation...")
            except Exception:
                logger.debug("Thinking indicator start failed", exc_info=True)

            # Fetch active session and its history from the service
            messages: list = []
            try:
                session = self.conversations.get_active_session()
                if session and session.id == new_session_id:
                    messages = list(session.history or [])
                # If history not present, attempt direct load from persistence
                if not messages and session:
                    try:
                        messages = self.conversations.persistence.load_messages(session.id)
                    except Exception:
                        logger.debug("Direct load of messages failed", exc_info=True)
            except Exception as exc:
                logger.error("Failed to get active session: %s", exc, exc_info=True)
                messages = []

            try:
                if messages:
                    self.chat_display.load_conversation_history(messages)
                else:
                    self.chat_display.clear_chat()
            finally:
                # Always stop loading indicator
                try:
                    self.thinking_indicator.stop_thinking()
                except Exception:
                    logger.debug("Thinking indicator stop failed", exc_info=True)

            # Ensure chat input is ready
            try:
                self.chat_input.setEnabled(True)
                if hasattr(self.chat_input, "clear_input"):
                    self.chat_input.clear_input()
                self.chat_input.focus_input()
            except Exception:
                logger.debug("Failed to reset chat input", exc_info=True)

            # Restore scroll if we have a stored position
            try:
                value = self._thread_scroll_positions.get(new_session_id)
                if value is not None:
                    self.chat_display.verticalScrollBar().setValue(int(value))
            except Exception:
                logger.debug("Failed restoring scroll position", exc_info=True)
        except Exception as exc:
            logger.error("Failed handling session switch: %s", exc, exc_info=True)
            try:
                self.chat_display.display_error(f"Failed to load conversation: {exc}")
            except Exception:
                logger.debug("Failed to display session switch error", exc_info=True)
            # Attempt recovery by starting a new session so UI isn't stuck
            try:
                self.event_bus.dispatch(Event(event_type="NEW_SESSION_REQUESTED"))
            except Exception:
                logger.debug("Failed to dispatch NEW_SESSION_REQUESTED for recovery", exc_info=True)

    def _handle_thread_switched(self, event: Event) -> None:
        """Load conversation history and manage UI when user switches threads."""
        try:
            payload = event.payload or {}
            session_id = payload.get("session_id")
            previous_session_id = payload.get("previous_session_id")
            message_count = payload.get("message_count", 0)
            messages = payload.get("messages", [])

            if not session_id:
                logger.warning("Thread switched event missing session_id")
                return

            # Persist scroll for previous thread if provided
            try:
                if previous_session_id:
                    bar = self.chat_display.verticalScrollBar()
                    self._thread_scroll_positions[previous_session_id] = int(bar.value())
            except Exception:
                logger.debug("Failed to store previous scroll position", exc_info=True)

            # Set current active id
            self._active_session_id = session_id

            # Show loading indicator while rendering
            try:
                self.thinking_indicator.start_thinking("Loading conversation...")
            except Exception:
                logger.debug("Thinking indicator start failed", exc_info=True)

            logger.info(
                "Loading conversation history for thread %s (%d messages)",
                session_id,
                message_count,
            )

            # Display messages, fallback to service if payload missing
            if not messages:
                try:
                    session = self.conversations.get_active_session()
                    if session and session.id == session_id:
                        messages = list(session.history or [])
                    if not messages:
                        messages = self.conversations.persistence.load_messages(session_id)
                except Exception:
                    logger.debug("Failed to fetch messages from service", exc_info=True)

            if messages:
                self.chat_display.load_conversation_history(messages)
                logger.info("Loaded %d messages into chat display", len(messages))
            else:
                self.chat_display.clear_chat()
                if message_count > 0:
                    self.chat_display.display_system_message(
                        "INFO",
                        f"Thread loaded ({message_count} messages - reload to view)"
                    )
                else:
                    self.chat_display.display_system_message(
                        "INFO",
                        "New conversation - start chatting!"
                    )

            # Reset chat input state
            try:
                self.chat_input.setEnabled(True)
                if hasattr(self.chat_input, "clear_input"):
                    self.chat_input.clear_input()
                self.chat_input.focus_input()
            except Exception:
                logger.debug("Failed to reset chat input after thread switch", exc_info=True)

            # Restore scroll if we have a stored position for this thread
            try:
                value = self._thread_scroll_positions.get(session_id)
                if value is not None:
                    self.chat_display.verticalScrollBar().setValue(int(value))
            except Exception:
                logger.debug("Failed restoring scroll position after thread switch", exc_info=True)

        except Exception as exc:
            logger.error("Failed handling thread switch: %s", exc, exc_info=True)
            try:
                self.chat_display.display_error(f"Failed to load conversation: {exc}")
            except Exception:
                logger.debug("Failed to display thread switch error", exc_info=True)
            try:
                self.event_bus.dispatch(Event(event_type="NEW_SESSION_REQUESTED"))
            except Exception:
                logger.debug("Failed to dispatch NEW_SESSION_REQUESTED after thread error", exc_info=True)
        finally:
            # Always hide indicator
            try:
                self.thinking_indicator.stop_thinking()
            except Exception:
                logger.debug("Thinking indicator stop failed", exc_info=True)