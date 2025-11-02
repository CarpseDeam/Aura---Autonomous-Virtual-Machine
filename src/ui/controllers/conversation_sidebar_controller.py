"""
ConversationSidebarController - Bridges ConversationSidebarWidget and ConversationManagementService.

Handles:
- Loading conversations from the service
- Updating the sidebar when conversations change
- Handling user actions (switch thread, rename, delete, etc.)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Dict
import threading

from PySide6.QtWidgets import QMessageBox

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.event_types import (
    CONVERSATION_SESSION_STARTED,
    CONVERSATION_MESSAGE_ADDED,
    PROJECT_ACTIVATED,
)
from src.aura.services.conversation_management_service import (
    ConversationManagementService,
    STANDALONE_PROJECT_NAME,
)
from src.ui.widgets.conversation_sidebar_widget import ConversationSidebarWidget

logger = logging.getLogger(__name__)


class ConversationSidebarController:
    """
    Controller that connects the ConversationSidebarWidget to the backend services.

    Responsibilities:
    - Load conversations from ConversationManagementService
    - Keep sidebar UI in sync with conversation state
    - Handle user actions (thread switching, renaming, deletion)
    - Subscribe to conversation events
    """

    def __init__(
        self,
        sidebar: ConversationSidebarWidget,
        conversations: ConversationManagementService,
        event_bus: EventBus,
    ):
        self.sidebar = sidebar
        self.conversations = conversations
        self.event_bus = event_bus

        self._current_project: Optional[str] = None
        self._title_cache: Dict[str, str] = {}

        self._connect_signals()
        self._subscribe_to_events()
        self._load_initial_conversations()

    def _connect_signals(self) -> None:
        """Connect sidebar widget signals to handler methods."""
        self.sidebar.thread_selected.connect(self._handle_thread_selected)
        self.sidebar.new_chat_requested.connect(self._handle_new_chat_requested)
        self.sidebar.new_thread_requested.connect(self._handle_new_thread_requested)
        self.sidebar.thread_renamed.connect(self._handle_thread_renamed)
        self.sidebar.thread_archived.connect(self._handle_thread_archived)
        self.sidebar.thread_deleted.connect(self._handle_thread_deleted)

    def _subscribe_to_events(self) -> None:
        """Subscribe to relevant events from the event bus."""
        self.event_bus.subscribe(CONVERSATION_SESSION_STARTED, self._on_session_started)
        self.event_bus.subscribe(CONVERSATION_MESSAGE_ADDED, self._on_message_added)
        self.event_bus.subscribe(PROJECT_ACTIVATED, self._on_project_activated)
        # Hook widget signal for upgrade action
        self.sidebar.upgrade_to_project_requested.connect(self._handle_upgrade_to_project)

    def _load_initial_conversations(self) -> None:
        """Load conversations in a background thread and then update the UI."""
        def _worker() -> None:
            try:
                conversations = self.conversations.persistence.get_all_conversations()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load initial conversations: %s", exc, exc_info=True)
                conversations = []

            from PySide6.QtCore import QTimer

            def _apply() -> None:
                try:
                    for conv in conversations:
                        self._add_conversation_to_sidebar(conv)

                    active_session = self.conversations.get_active_session()
                    if active_session:
                        self.sidebar.set_active_thread(active_session.id)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Failed to apply conversation list: %s", exc, exc_info=True)

            QTimer.singleShot(0, _apply)

        threading.Thread(target=_worker, name="sidebar-loader", daemon=True).start()

    def _add_conversation_to_sidebar(self, conversation: dict) -> None:
        """Add a single conversation to the appropriate sidebar section."""
        try:
            conv_id = conversation["id"]
            title = conversation.get("title") or "Untitled"
            project_name = conversation.get("project_name")
            updated_str = conversation.get("updated_at")

            # Parse timestamp
            last_updated = None
            if updated_str:
                try:
                    last_updated = datetime.fromisoformat(updated_str)
                except ValueError:
                    pass

            # Determine if this is a project thread or standalone chat
            if project_name and project_name != STANDALONE_PROJECT_NAME:
                self.sidebar.add_project_thread(
                    project_name=project_name,
                    thread_id=conv_id,
                    title=title,
                    last_updated=last_updated,
                )
            else:
                self.sidebar.add_chat_thread(
                    thread_id=conv_id,
                    title=title,
                    last_updated=last_updated,
                )

        except Exception as exc:
            logger.warning("Failed to add conversation to sidebar: %s", exc)

    def _handle_thread_selected(self, thread_id: str) -> None:
        """Handle user selecting a different thread."""
        try:
            logger.info(f"Switching to conversation thread: {thread_id}")

            # Load the conversation
            conversation = self.conversations.persistence.get_conversation(thread_id)
            if not conversation:
                logger.error(f"Conversation {thread_id} not found")
                self._show_error("Thread not found", "The selected conversation could not be found.")
                return

            # Mark it as active
            self.conversations.persistence.mark_conversation_active(thread_id)

            # Load messages
            messages = self.conversations.persistence.load_messages(thread_id)

            # Update the active session in memory
            from src.aura.models.session import Session

            session = Session(
                id=conversation["id"],
                project_name=conversation.get("project_name", "default_project"),
                title=conversation.get("title"),
                created_at=conversation.get("created_at"),
                updated_at=conversation.get("updated_at"),
                is_active=True,
                history=messages,
            )

            with self.conversations._lock:
                # Deactivate other sessions for the same project
                for existing in self.conversations.sessions.values():
                    if existing.project_name == session.project_name:
                        existing.is_active = False

                self.conversations.sessions[session.id] = session
                self.conversations.active_session_id = session.id

            # Dispatch session started event
            self.conversations._emit_session_started(session)

            # Update sidebar highlight
            self.sidebar.set_active_thread(thread_id)

            logger.info(f"Successfully switched to thread: {thread_id}")

        except Exception as exc:
            logger.error(f"Failed to switch thread: {exc}", exc_info=True)
            self._show_error("Switch Failed", f"Could not switch to thread: {exc}")

    def _handle_new_chat_requested(self) -> None:
        """Handle request to create a new standalone chat."""
        try:
            logger.info("Creating new standalone chat")

            # Create a new conversation
            project_name = self._current_project or STANDALONE_PROJECT_NAME
            conversation = self.conversations.persistence.create_conversation(
                project_name, active=True
            )

            # Add to sidebar
            self._add_conversation_to_sidebar(conversation)

            # Switch to it
            self._handle_thread_selected(conversation["id"])

        except Exception as exc:
            logger.error(f"Failed to create new chat: {exc}", exc_info=True)
            self._show_error("Creation Failed", f"Could not create new chat: {exc}")

    def _handle_new_thread_requested(self) -> None:
        """Handle request to create a new thread in the current project."""
        if not self._current_project:
            self._show_error(
                "No Project Active",
                "Please activate a project before creating a new thread.",
            )
            return

        try:
            logger.info(f"Creating new thread for project: {self._current_project}")

            # Create a new conversation for the project
            conversation = self.conversations.persistence.create_conversation(
                self._current_project, active=True
            )

            # Add to sidebar under the project
            self._add_conversation_to_sidebar(conversation)

            # Switch to it
            self._handle_thread_selected(conversation["id"])

        except Exception as exc:
            logger.error(f"Failed to create new thread: {exc}", exc_info=True)
            self._show_error("Creation Failed", f"Could not create new thread: {exc}")

    def _handle_upgrade_to_project(self, thread_id: str) -> None:
        """Convert a standalone chat into a project-scoped thread."""
        try:
            from PySide6.QtWidgets import QInputDialog

            # Prompt for project name
            project_name, ok = QInputDialog.getText(
                self.sidebar,
                "Upgrade to Project",
                "Enter new project name:",
                text="",
            )
            if not ok or not project_name.strip():
                return
            project_name = project_name.strip()

            # Update conversation project in persistence (mark active)
            self.conversations.persistence.reassign_conversation_project(thread_id, project_name, make_active=True)

            # Ask the system to create/switch to the project via chat command pipeline
            try:
                self.event_bus.dispatch(Event(event_type="SEND_USER_MESSAGE", payload={"text": f"/project create {project_name}"}))
            except Exception:
                logger.debug("Failed to dispatch project creation command", exc_info=True)

            # Refresh this thread in the sidebar under the project section
            self.sidebar.remove_thread(thread_id)
            conversation = self.conversations.persistence.get_conversation(thread_id)
            if conversation:
                self._add_conversation_to_sidebar(conversation)
                self.sidebar.set_active_thread(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to upgrade chat to project: %s", exc, exc_info=True)
            self._show_error("Upgrade Failed", f"Could not upgrade chat: {exc}")

    def _handle_thread_renamed(self, thread_id: str, new_title: str) -> None:
        """Handle thread rename request."""
        try:
            logger.info(f"Renaming thread {thread_id} to: {new_title}")

            # Update in persistence
            self.conversations.persistence.update_conversation_title(thread_id, new_title)

            # Update in sidebar (reload the conversation)
            self.sidebar.remove_thread(thread_id)
            conversation = self.conversations.persistence.get_conversation(thread_id)
            if conversation:
                self._add_conversation_to_sidebar(conversation)

                # Re-select if it was active
                if self.conversations.active_session_id == thread_id:
                    self.sidebar.set_active_thread(thread_id)

        except Exception as exc:
            logger.error(f"Failed to rename thread: {exc}", exc_info=True)
            self._show_error("Rename Failed", f"Could not rename thread: {exc}")

    def _handle_thread_archived(self, thread_id: str) -> None:
        """Handle thread archive request."""
        try:
            logger.info(f"Archiving thread: {thread_id}")

            # Mark as inactive
            self.conversations.persistence.mark_conversation_inactive(thread_id)

            # Remove from sidebar
            self.sidebar.remove_thread(thread_id)

            # If this was the active session, create a new one
            if self.conversations.active_session_id == thread_id:
                self._handle_new_chat_requested()

        except Exception as exc:
            logger.error(f"Failed to archive thread: {exc}", exc_info=True)
            self._show_error("Archive Failed", f"Could not archive thread: {exc}")

    def _handle_thread_deleted(self, thread_id: str) -> None:
        """Handle thread deletion request."""
        # Confirm deletion
        reply = QMessageBox.question(
            self.sidebar,
            "Delete Thread",
            "Are you sure you want to delete this conversation? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            logger.info(f"Deleting thread: {thread_id}")

            # Delete from persistence
            self.conversations.persistence.delete_conversation(thread_id)

            # Remove from sidebar
            self.sidebar.remove_thread(thread_id)

            # If this was the active session, create a new one
            if self.conversations.active_session_id == thread_id:
                self._handle_new_chat_requested()

        except Exception as exc:
            logger.error(f"Failed to delete thread: {exc}", exc_info=True)
            self._show_error("Deletion Failed", f"Could not delete thread: {exc}")

    def _on_session_started(self, event: Event) -> None:
        """Handle CONVERSATION_SESSION_STARTED event."""
        try:
            payload = event.payload or {}
            session_id = payload.get("session_id")

            if session_id:
                # Highlight the new session in the sidebar
                self.sidebar.set_active_thread(session_id)

        except Exception as exc:
            logger.debug(f"Failed to handle session started event: {exc}")

    def _on_message_added(self, event: Event) -> None:
        """Handle CONVERSATION_MESSAGE_ADDED event."""
        try:
            payload = event.payload or {}
            session_id = payload.get("session_id")
            role = payload.get("role")

            # If it's the first user message, we might need to update the title
            if role == "user" and session_id:
                conversation = self.conversations.persistence.get_conversation(session_id)
                if conversation and not conversation.get("title"):
                    # Reload the conversation in the sidebar to get the auto-generated title
                    self.sidebar.remove_thread(session_id)
                    self._add_conversation_to_sidebar(conversation)

                    if self.conversations.active_session_id == session_id:
                        self.sidebar.set_active_thread(session_id)

        except Exception as exc:
            logger.debug(f"Failed to handle message added event: {exc}")

    def _on_project_activated(self, event: Event) -> None:
        """Handle PROJECT_ACTIVATED event."""
        try:
            payload = event.payload or {}
            project_name = payload.get("project_name")

            if project_name:
                self._current_project = project_name
                self.sidebar.set_project_active(True)

                # Load conversations for this project
                self._reload_sidebar()

        except Exception as exc:
            logger.debug(f"Failed to handle project activated event: {exc}")

    def _reload_sidebar(self) -> None:
        """Reload all conversations in the sidebar."""
        try:
            self.sidebar.clear_threads()
            self._load_initial_conversations()
        except Exception as exc:
            logger.error(f"Failed to reload sidebar: {exc}", exc_info=True)

    def _show_error(self, title: str, message: str) -> None:
        """Show an error dialog to the user."""
        QMessageBox.critical(self.sidebar, title, message)
