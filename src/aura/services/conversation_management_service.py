import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from src.aura.models.session import Session
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.services.conversation_persistence_service import ConversationPersistenceService

logger = logging.getLogger(__name__)

MAX_CONTEXT_MESSAGES = 30


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
        return session

    def _ensure_active_session(self) -> Session:
        session = self.get_active_session()
        if session:
            return session
        project_name = self.active_project or "default_project"
        logger.debug("No active session found; creating one for project '%s'", project_name)
        return self._load_or_create_session_for_project(project_name)

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
        return "default_project"
