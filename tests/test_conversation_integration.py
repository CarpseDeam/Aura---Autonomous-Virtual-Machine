"""
Test suite for verifying the integration between AuraInterface and ConversationManagementService.

This test verifies that:
1. Conversation history is pulled from ConversationManagementService in _build_context()
2. Messages are properly added to conversation service instead of ProjectManager
3. ProjectManager only stores metadata (not conversation history)
4. Session events are properly dispatched
"""
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

from src.aura.interface import AuraInterface
from src.aura.models.project_context import ProjectContext
from src.aura.models.events import Event


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus."""
    return MagicMock()


@pytest.fixture
def mock_brain():
    """Create a mock brain."""
    return MagicMock()


@pytest.fixture
def mock_executor():
    """Create a mock executor."""
    return MagicMock()


@pytest.fixture
def mock_conversations():
    """Create a mock conversation management service."""
    service = MagicMock()
    service.get_history.return_value = [
        {"role": "user", "content": "test message 1"},
        {"role": "assistant", "content": "test response 1"},
    ]
    service.get_active_session.return_value = MagicMock(
        id="test-session-123",
        project_name="test-project",
        history=[
            {"role": "user", "content": "test message 1"},
            {"role": "assistant", "content": "test response 1"},
        ]
    )
    return service


@pytest.fixture
def mock_workspace():
    """Create a mock workspace service."""
    workspace = MagicMock()
    workspace.active_project = "test-project"
    workspace.get_project_files.return_value = ["file1.py", "file2.py"]
    return workspace


@pytest.fixture
def mock_thread_pool():
    """Create a mock thread pool."""
    return MagicMock()


@pytest.fixture
def mock_project_manager():
    """Create a mock project manager."""
    pm = MagicMock()
    project = MagicMock()
    project.name = "test-project"
    project.root_path = "/test/path"
    project.active_files = ["file1.py"]
    project.conversation_history = []  # Should not be used for context
    project.metadata = {"current_language": "python"}
    project.last_active = datetime.now(timezone.utc)
    pm.current_project = project
    return pm


@pytest.fixture
def interface(mock_event_bus, mock_brain, mock_executor, mock_conversations,
              mock_workspace, mock_thread_pool, mock_project_manager):
    """Create an AuraInterface instance with mocks."""
    return AuraInterface(
        event_bus=mock_event_bus,
        brain=mock_brain,
        executor=mock_executor,
        conversations=mock_conversations,
        workspace=mock_workspace,
        thread_pool=mock_thread_pool,
        project_manager=mock_project_manager,
    )


class TestConversationIntegration:
    """Test suite for conversation integration."""

    def test_build_context_uses_conversation_service(self, interface, mock_conversations):
        """Verify that _build_context() pulls history from ConversationManagementService."""
        context = interface._build_context()

        # Should call get_history() from conversations service
        mock_conversations.get_history.assert_called_once()

        # Context should contain the conversation history from the service
        assert len(context.conversation_history) == 2
        assert context.conversation_history[0]["role"] == "user"
        assert context.conversation_history[1]["role"] == "assistant"

    def test_build_context_does_not_use_project_history(self, interface, mock_project_manager):
        """Verify that _build_context() does NOT use project.conversation_history."""
        context = interface._build_context()

        # Verify project.conversation_history was not accessed for context
        # (it should still exist but not be used for building context)
        assert len(context.conversation_history) == 2  # From conversations service
        # Project history should be empty (not used)
        assert len(mock_project_manager.current_project.conversation_history) == 0

    def test_build_context_handles_no_active_session(self, interface, mock_conversations):
        """Verify that _build_context() handles the case when no session is active."""
        # Simulate no active session by returning empty history
        mock_conversations.get_history.return_value = []

        context = interface._build_context()

        # Should still return a valid context with empty history
        assert isinstance(context, ProjectContext)
        assert context.conversation_history == []
        assert context.active_project == "test-project"

    def test_build_context_handles_conversation_load_error(self, interface, mock_conversations):
        """Verify that _build_context() handles errors when loading conversation history."""
        # Simulate an error when loading conversation history
        mock_conversations.get_history.side_effect = Exception("Database error")

        context = interface._build_context()

        # Should return a context with empty history (gracefully handles error)
        assert isinstance(context, ProjectContext)
        assert context.conversation_history == []
        assert context.active_project == "test-project"

    def test_persist_project_metadata_does_not_save_conversation(self, interface, mock_project_manager):
        """Verify that _persist_project_metadata() does NOT save conversation history."""
        agent_messages = [
            {"role": "assistant", "content": "test response", "metadata": {"action_type": "code"}}
        ]

        interface._persist_project_metadata(agent_messages=agent_messages)

        # Verify save_project was called
        mock_project_manager.save_project.assert_called_once()

        # Verify conversation_history is still empty (not persisted)
        project = mock_project_manager.current_project
        assert len(project.conversation_history) == 0

    def test_persist_project_metadata_updates_metadata(self, interface, mock_project_manager):
        """Verify that _persist_project_metadata() updates project metadata."""
        agent_messages = [
            {"role": "assistant", "content": "test", "metadata": {"topics": ["testing", "pytest"]}}
        ]

        interface._persist_project_metadata(agent_messages=agent_messages)

        # Verify metadata was updated
        project = mock_project_manager.current_project
        assert project.metadata["current_language"] == "python"
        assert "recent_topics" in project.metadata
        assert "testing" in project.metadata["recent_topics"]

    def test_project_command_uses_conversation_service(self, interface, mock_conversations, mock_event_bus):
        """Verify that /project commands record messages in conversation service."""
        event = Event(event_type="SEND_USER_MESSAGE", payload={"text": "/project list"})

        interface._handle_user_message(event)

        # Verify add_message was called for both user and assistant messages
        assert mock_conversations.add_message.call_count == 2

        # Check the calls
        calls = mock_conversations.add_message.call_args_list
        assert calls[0][0][0] == "user"  # First call: role="user"
        assert "/project list" in calls[0][0][1]  # First call: content contains command
        assert calls[1][0][0] == "assistant"  # Second call: role="assistant"

    def test_conversation_session_started_handler(self, interface, mock_event_bus):
        """Verify that CONVERSATION_SESSION_STARTED event is handled."""
        event = Event(
            event_type="CONVERSATION_SESSION_STARTED",
            payload={"session_id": "test-123", "project_name": "test-project"}
        )

        # Should not raise an error
        interface._handle_conversation_session_started(event)

    def test_conversation_message_added_handler(self, interface, mock_event_bus):
        """Verify that CONVERSATION_MESSAGE_ADDED event is handled."""
        event = Event(
            event_type="CONVERSATION_MESSAGE_ADDED",
            payload={"role": "user", "content": "test message", "session_id": "test-123"}
        )

        # Should not raise an error
        interface._handle_conversation_message_added(event)

    def test_conversation_events_are_subscribed(self, interface, mock_event_bus):
        """Verify that interface subscribes to conversation events."""
        # Check that subscribe was called for conversation events
        subscribe_calls = [call[0][0] for call in mock_event_bus.subscribe.call_args_list]

        assert "CONVERSATION_SESSION_STARTED" in subscribe_calls
        assert "CONVERSATION_MESSAGE_ADDED" in subscribe_calls


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
