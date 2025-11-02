import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.event_types import (
    CONVERSATION_MESSAGE_ADDED,
    CONVERSATION_SESSION_STARTED,
    CONVERSATION_THREAD_SWITCHED,
)
from src.aura.models.session import Session
from src.aura.services.conversation_persistence_service import ConversationPersistenceService

logger = logging.getLogger(__name__)

MAX_CONTEXT_MESSAGES = 30
STANDALONE_PROJECT_NAME = "__standalone__"


class ConversationManagementService:
    """Manages conversation sessions backed by persistent storage."""

    def __init__(self, event_bus: EventBus, persistence: ConversationPersistenceService):
        """Initializes the ConversationManagementService."""
        self.event_bus = event_bus
        self.persistence = persistence
        self.sessions: Dict[str, Session] = {}
        self.active_session_id: Optional[str] = None
        self.active_project: Optional[str] = None
        self._lock = threading.RLock()
        self._save_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="conversation-persist")
        self._register_event_handlers()

    def _register_event_handlers(self):
        """Subscribes the service to relevant events."""
        self.event_bus.subscribe("NEW_SESSION_REQUESTED", self.start_new_session)
        self.event_bus.subscribe("PROJECT_ACTIVATED", self._handle_project_activated)

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #

    def start_new_session(self, event: Optional[Event]):
        """
        Creates a new conversation session and sets it as the active one.

        Args:
            event: The event that triggered the new session.
        """
        project_name = self._resolve_project_name(event)
        logger.info("Starting new conversation session for project '%s'", project_name)
        conversation = self.persistence.create_conversation(project_name, active=True)
        session = Session(
            id=conversation["id"],
            project_name=project_name,
            title=conversation.get("title"),
            created_at=conversation.get("created_at"),
            updated_at=conversation.get("updated_at"),
            is_active=True,
        )
        with self._lock:
            # Mark any previous session for this project as inactive in memory
            for existing in self.sessions.values():
                if existing.project_name == project_name:
                    existing.is_active = False
            self.sessions[session.id] = session
            self.active_session_id = session.id

        self._emit_session_started(session)

    def switch_to_conversation(self, conversation_id: str) -> Optional[Session]:
        """
        Switch to an existing conversation by loading it from the database.

        Args:
            conversation_id: The ID of the conversation to switch to

        Returns:
            The loaded Session object, or None if the conversation doesn't exist
        """
        logger.info("Switching to conversation: %s", conversation_id)

        # Fetch the conversation metadata
        conversation = self.persistence.get_conversation(conversation_id)
        if not conversation:
            logger.error("Conversation %s not found", conversation_id)
            return None

        # Load all messages for this conversation
        messages = self.persistence.load_messages(conversation_id)

        # Mark it as active in the database
        self.persistence.mark_conversation_active(conversation_id)

        # Create or update the Session object
        session = Session(
            id=conversation["id"],
            project_name=conversation.get("project_name", STANDALONE_PROJECT_NAME),
            title=conversation.get("title"),
            created_at=conversation.get("created_at"),
            updated_at=conversation.get("updated_at"),
            is_active=True,
            history=messages,
        )

        # Track the previous session ID for the event
        previous_session_id = self.active_session_id

        # Update in-memory state
        with self._lock:
            # Deactivate other sessions for the same project
            for existing in self.sessions.values():
                if existing.project_name == session.project_name:
                    existing.is_active = False

            self.sessions[session.id] = session
            self.active_session_id = session.id
            self.active_project = session.project_name

        # Emit thread switched event
        self._emit_thread_switched(
            session=session,
            previous_session_id=previous_session_id,
            message_count=len(messages),
        )

        logger.info("Successfully switched to conversation %s with %d messages", conversation_id, len(messages))
        return session

    def get_active_session(self) -> Optional[Session]:
        """
        Retrieves the currently active session object.

        Returns:
            The active Session object, or None if no session is active.
        """
        with self._lock:
            if not self.active_session_id:
                return None
            return self.sessions.get(self.active_session_id)

    def _handle_project_activated(self, event: Event) -> None:
        """Load or create the latest conversation when a project becomes active."""
        project_name = (event.payload or {}).get("project_name")
        if not project_name:
            return
        self.active_project = project_name
        logger.info("Project '%s' activated; loading latest conversation.", project_name)
        self._load_or_create_session_for_project(project_name)

    def _load_or_create_session_for_project(self, project_name: str) -> Session:
        conversation = self.persistence.get_most_recent_conversation(project_name)
        if not conversation:
            conversation = self.persistence.create_conversation(project_name, active=True)
            messages: List[Dict[str, Any]] = []
        else:
            self.persistence.mark_conversation_active(conversation["id"])
            messages = self.persistence.load_messages(conversation["id"])

        session = Session(
            id=conversation["id"],
            project_name=project_name,
            title=conversation.get("title"),
            created_at=conversation.get("created_at"),
            updated_at=conversation.get("updated_at"),
            is_active=bool(conversation.get("is_active", 1)),
            history=messages,
        )
        with self._lock:
            self.sessions[session.id] = session
            self.active_session_id = session.id
        self._emit_session_started(session)
        return session

    def _ensure_active_session(self) -> Session:
        session = self.get_active_session()
        if session:
            return session
        project_name = self.active_project or STANDALONE_PROJECT_NAME
        logger.debug("No active session found; creating one for project '%s'", project_name)
        return self._load_or_create_session_for_project(project_name)

    def _emit_session_started(self, session: Session) -> None:
        """Notify listeners that a conversation session is now active."""
        if not self.event_bus or not isinstance(session, Session):
            return
        try:
            payload = {
                "session_id": session.id,
                "project_name": session.project_name,
                "started_at": session.created_at,
            }
            self.event_bus.dispatch(
                Event(
                    event_type=CONVERSATION_SESSION_STARTED,
                    payload=payload,
                )
            )
        except Exception:
            logger.debug("Failed to dispatch CONVERSATION_SESSION_STARTED event", exc_info=True)

    def _emit_thread_switched(
        self,
        session: Session,
        previous_session_id: Optional[str],
        message_count: int,
    ) -> None:
        """Notify listeners that the user has switched to a different conversation thread."""
        if not self.event_bus or not isinstance(session, Session):
            return
        try:
            payload = {
                "session_id": session.id,
                "project_name": session.project_name,
                "previous_session_id": previous_session_id,
                "message_count": message_count,
                "messages": session.history,  # Include the messages so UI can display them
            }
            self.event_bus.dispatch(
                Event(
                    event_type=CONVERSATION_THREAD_SWITCHED,
                    payload=payload,
                )
            )
        except Exception:
            logger.debug("Failed to dispatch CONVERSATION_THREAD_SWITCHED event", exc_info=True)

    def _dispatch_message_event(
        self,
        *,
        session_id: str,
        project_name: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        """Dispatch an event when a message is added to the active session."""
        if not self.event_bus:
            return

        token_usage = None
        if isinstance(metadata, dict):
            token_usage = metadata.get("token_usage")

        payload: Dict[str, Any] = {
            "session_id": session_id,
            "project_name": project_name,
            "role": role,
            "content": content,
        }
        if token_usage is not None:
            payload["token_usage"] = token_usage

        try:
            self.event_bus.dispatch(
                Event(
                    event_type=CONVERSATION_MESSAGE_ADDED,
                    payload=payload,
                )
            )
        except Exception:
            logger.debug("Failed to dispatch CONVERSATION_MESSAGE_ADDED event", exc_info=True)

    # ------------------------------------------------------------------ #
    # Message management
    # ------------------------------------------------------------------ #

    def add_message(
        self,
        role: str,
        content: str,
        images: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Adds a message to the history of the active session.

        If no session is active, a new one is started automatically.

        Args:
            role: The role of the message sender (e.g., 'user', 'model').
            content: The content of the message.
            images: Optional list of image payloads attached to the message.
            metadata: Optional metadata dictionary to persist with the message.
        """
        session = self._ensure_active_session()
        if not session:
            logger.warning("Unable to add message because no session could be created.")
            return

        timestamp = self._now()
        cleaned_images = self._sanitize_image_metadata(images)
        merged_metadata = self._merge_metadata(metadata, cleaned_images)

        message: Dict[str, Any] = {
            "role": role,
            "content": content or "",
            "created_at": timestamp,
        }
        if cleaned_images:
            message["images"] = cleaned_images
        if merged_metadata:
            message["metadata"] = merged_metadata

        with self._lock:
            session.history.append(message)
            # Keep the in-memory history from growing unbounded
            if len(session.history) > 5000:
                session.history = session.history[-5000:]
            session.updated_at = timestamp

        if role == "user" and not session.title:
            generated_title = self._generate_title(content or "")
            if generated_title:
                session.title = generated_title
                self.persistence.update_conversation_title(session.id, generated_title)

        self._save_executor.submit(
            self._persist_message,
            session.id,
            role,
            content or "",
            merged_metadata,
        )

        self._dispatch_message_event(
            session_id=session.id,
            project_name=session.project_name,
            role=role,
            content=content or "",
            metadata=merged_metadata,
        )

    def add_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Convenience method to add multiple messages in order."""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content", "")
            if not role:
                continue
            images = message.get("images")
            metadata = self._extract_metadata_from_message(message)
            self.add_message(role, content, images=images, metadata=metadata)

    def get_history(self) -> List[Dict[str, Any]]:
        """
        Retrieves the message history of the active session.

        Returns:
            A list of message dictionaries, or an empty list if no
            session is active.
        """
        session = self.get_active_session()
        if not session:
            return []
        history_slice = session.history[-MAX_CONTEXT_MESSAGES:]
        # Return deep copies to avoid callers mutating internal state
        return [dict(message) for message in history_slice]

    def get_full_history(self) -> List[Dict[str, Any]]:
        """Return the entire history of the active session (for UI/search)."""
        session = self.get_active_session()
        if not session:
            return []
        return [dict(message) for message in session.history]

    def get_active_files(self) -> List[str]:
        """Return active files tracked for the active thread (if any)."""
        session = self.get_active_session()
        if not session:
            return []
        try:
            return self.persistence.get_thread_active_files(session.id)
        except Exception:
            logger.debug("Failed to get thread active files", exc_info=True)
            return []

    def set_active_files(self, files: List[str]) -> None:
        """Persist active files for the active thread."""
        session = self.get_active_session()
        if not session:
            return
        try:
            self.persistence.set_thread_active_files(session.id, files)
        except Exception:
            logger.debug("Failed to set thread active files", exc_info=True)

    def search_messages(self, term: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Proxy search requests to the persistence layer."""
        return self.persistence.search_messages(term, limit=limit)

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #
    def _persist_message(self, conversation_id: str, role: str, content: str, metadata: Optional[Dict[str, Any]]):
        try:
            self.persistence.save_message(conversation_id, role, content, metadata)
        except Exception as exc:
            logger.error("Failed to persist conversation message: %s", exc, exc_info=True)

    def _sanitize_image_metadata(self, images: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        sanitized: List[Dict[str, Any]] = []
        if not images:
            return sanitized
        for image in images:
            if not isinstance(image, dict):
                continue
            entry: Dict[str, Any] = {}
            path = image.get("path") or image.get("relative_path")
            if path:
                entry["path"] = str(path)
            mime_type = image.get("mime_type") or image.get("content_type")
            if mime_type:
                entry["mime_type"] = str(mime_type)
            caption = image.get("caption")
            if caption:
                entry["caption"] = str(caption)
            width = image.get("width")
            height = image.get("height")
            if isinstance(width, (int, float)):
                entry["width"] = width
            if isinstance(height, (int, float)):
                entry["height"] = height
            if entry:
                sanitized.append(entry)
        return sanitized

    @staticmethod
    def _merge_metadata(metadata: Optional[Dict[str, Any]], images: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if metadata:
            for key, value in metadata.items():
                if key == "images":
                    continue
                try:
                    payload[key] = value
                except Exception:
                    continue
        if images:
            payload["images"] = images
        return payload or None

    @staticmethod
    def _extract_metadata_from_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metadata: Dict[str, Any] = {}
        raw_meta = message.get("metadata")
        if isinstance(raw_meta, dict):
            metadata.update(raw_meta)

        for simple_key in ("action_type", "tool_name"):
            value = message.get(simple_key)
            if value is not None and simple_key not in metadata:
                try:
                    metadata[simple_key] = value
                except Exception:
                    continue

        if "result" in message and "result_preview" not in metadata:
            try:
                preview = str(message["result"])
                if len(preview) > 500:
                    preview = preview[:497].rstrip() + "..."
                metadata["result_preview"] = preview
            except Exception:
                pass

        return metadata or None

    @staticmethod
    def _generate_title(content: str, max_words: int = 8, max_chars: int = 60) -> Optional[str]:
        if not content:
            return None
        stripped = " ".join(content.strip().split())
        words = stripped.split()
        title = " ".join(words[:max_words])
        if len(title) > max_chars:
            title = title[: max_chars - 1].rstrip() + "…"
        return title

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(tz=timezone.utc).isoformat(timespec="microseconds")

    def _resolve_project_name(self, event: Optional[Event]) -> str:
        if event and event.payload:
            candidate = event.payload.get("project_name")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        if self.active_project:
            return self.active_project
        return STANDALONE_PROJECT_NAME
