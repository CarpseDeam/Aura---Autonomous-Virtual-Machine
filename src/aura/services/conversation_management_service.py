import logging
from typing import Dict, List, Optional

from src.aura.models.session import Session
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event

logger = logging.getLogger(__name__)


class ConversationManagementService:
    """Manages multiple conversation sessions and their histories."""

    def __init__(self, event_bus: EventBus):
        """Initializes the ConversationManagementService."""
        self.event_bus = event_bus
        self.sessions: Dict[str, Session] = {}
        self.active_session_id: Optional[str] = None
        self._register_event_handlers()

    def _register_event_handlers(self):
        """Subscribes the service to relevant events."""
        self.event_bus.subscribe("NEW_SESSION_REQUESTED", self.start_new_session)

    def start_new_session(self, event: Event):
        """
        Creates a new conversation session and sets it as the active one.

        Args:
            event: The event that triggered the new session.
        """
        logger.info("New session requested by user. Clearing conversation history.")
        session = Session()
        self.sessions[session.id] = session
        self.active_session_id = session.id

    def get_active_session(self) -> Optional[Session]:
        """
        Retrieves the currently active session object.

        Returns:
            The active Session object, or None if no session is active.
        """
        if not self.active_session_id:
            return None
        return self.sessions.get(self.active_session_id)

    def add_message(self, role: str, content: str):
        """
        Adds a message to the history of the active session.

        If no session is active, a new one is started automatically.

        Args:
            role: The role of the message sender (e.g., 'user', 'model').
            content: The content of the message.
        """
        if not self.active_session_id:
            # The event parameter is not used, so we can pass None
            self.start_new_session(None)

        session = self.get_active_session()
        if session:
            session.history.append({"role": role, "content": content})

    def get_history(self) -> List[Dict[str, str]]:
        """
        Retrieves the message history of the active session.

        Returns:
            A list of message dictionaries, or an empty list if no
            session is active.
        """
        session = self.get_active_session()
        return session.history if session else []
