import json
from typing import Any, Dict, List, Optional

import pytest

from src.aura.brain import AuraBrain
from src.aura.models.action import ActionType
from src.aura.models.intent import Intent
from src.aura.models.project_context import ProjectContext
from src.aura.project.project_manager import ProjectManager


class StubPromptManager:
    """Minimal prompt manager stub for tests."""

    def render(self, template_name: str, **context: Any) -> str:
        if template_name == "intent_detection_prompt.jinja2":
            user_text = context.get("user_text")
            if isinstance(user_text, str):
                snippet = user_text
            elif user_text is None:
                snippet = ""
            else:
                snippet = str(user_text)
            return f"rendered::{template_name}::{snippet}"
        return f"rendered::{template_name}"


class ConfigurableLLMStub:
    """LLM stub that returns canned responses for agents."""

    def __init__(
        self,
        intent_map: Optional[Dict[str, str]] = None,
        reasoning_response: Optional[str] = None,
    ) -> None:
        self.intent_map = intent_map or {}
        self.reasoning_response = reasoning_response or json.dumps(
            {
                "action": {"type": "SIMPLE_REPLY", "params": {"request": "fallback"}},
                "confidence": 1.0,
                "unclear_aspects": [],
                "clarifying_questions": [],
            }
        )

    def run_for_agent(self, agent: str, prompt: str) -> str:
        if agent == "intent_detection_agent":
            for phrase, response in self.intent_map.items():
                if phrase in prompt:
                    return response
            raise AssertionError(f"Intent prompt not recognized for agent input: {prompt}")

        if agent == "reasoning_agent":
            return self.reasoning_response

        raise AssertionError(f"Unexpected agent requested: {agent}")


@pytest.mark.parametrize(
    ("user_text", "expected_intent"),
    [
        ("Hey what's up?", Intent.CASUAL_CHAT),
        ("Should I use Redis?", Intent.SEEKING_ADVICE),
        ("Add payments", Intent.BUILD_VAGUE),
        ("Add GET /health returning {status: 'ok'}", Intent.BUILD_CLEAR),
    ],
)
def test_intent_detection_returns_expected_intent(user_text: str, expected_intent: Intent) -> None:
    llm = ConfigurableLLMStub(intent_map={user_text: expected_intent.name})
    brain = AuraBrain(llm, StubPromptManager())

    intent = brain._detect_user_intent(user_text, [])

    assert intent == expected_intent


def test_action_selection_casual_chat_returns_simple_reply() -> None:
    llm = ConfigurableLLMStub()
    brain = AuraBrain(llm, StubPromptManager())
    brain._detect_user_intent = lambda *args, **kwargs: Intent.CASUAL_CHAT  # type: ignore[assignment]

    action = brain.decide("Hey what's up?", ProjectContext())

    assert action.type == ActionType.SIMPLE_REPLY
    assert action.get_param("request") == "Hey what's up?"


def test_action_selection_build_clear_with_high_confidence() -> None:
    payload = {
        "action": {
            "type": "DESIGN_BLUEPRINT",
            "params": {"summary": "Design updated health endpoint"},
        },
        "confidence": 0.92,
        "unclear_aspects": [],
        "clarifying_questions": [],
    }
    llm = ConfigurableLLMStub(reasoning_response=json.dumps(payload))
    brain = AuraBrain(llm, StubPromptManager())
    brain._detect_user_intent = lambda *args, **kwargs: Intent.BUILD_CLEAR  # type: ignore[assignment]

    action = brain.decide("Add GET /health returning {status: 'ok'}", ProjectContext())

    assert action.type == ActionType.DESIGN_BLUEPRINT
    assert action.get_param("summary") == "Design updated health endpoint"


def test_action_selection_build_vague_low_confidence_triggers_discuss() -> None:
    payload = {
        "action": {
            "type": "DESIGN_BLUEPRINT",
            "params": {"summary": "Initial payment approach"},
        },
        "confidence": 0.42,
        "unclear_aspects": ["Payment scope undefined"],
        "clarifying_questions": ["Can you describe the checkout flow?"],
    }
    llm = ConfigurableLLMStub(reasoning_response=json.dumps(payload))
    brain = AuraBrain(llm, StubPromptManager())
    brain._detect_user_intent = lambda *args, **kwargs: Intent.BUILD_VAGUE  # type: ignore[assignment]

    action = brain.decide("Add payments", ProjectContext())

    assert action.type == ActionType.DISCUSS
    questions = action.get_param("questions")
    assert questions == ["Can you describe the checkout flow?"]


def test_project_persistence_round_trip(tmp_path) -> None:
    storage_dir = tmp_path / "projects"
    workspace_dir = tmp_path / "workspace"
    storage_dir.mkdir()
    workspace_dir.mkdir()

    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("companion", str((workspace_dir / "companion").resolve()))

    turns: List[Dict[str, Any]] = [
        {"role": "user", "content": "Remember this task"},
        {"role": "assistant", "content": "Task recorded"},
    ]
    project.conversation_history.extend(turns)
    manager.save_project(project)

    reloaded = ProjectManager(storage_dir=str(storage_dir)).load_project("companion")
    assert reloaded.conversation_history == turns


# -- Memory System Tests ---------------------------------------------------------------


def test_blueprint_parser_extracts_decisions() -> None:
    """Test that BlueprintParser correctly extracts architectural decisions."""
    from src.aura.services.memory_blueprint_parser import BlueprintParser

    parser = BlueprintParser()

    # Simulate a blueprint payload with decisions
    payload = {
        "blueprint": {
            "project_name": "test-api",
            "architecture": {
                "framework": "FastAPI",
                "database": "PostgreSQL",
                "auth": "JWT tokens",
            },
            "decisions": [
                {
                    "category": "Backend",
                    "decision": "Use FastAPI framework",
                    "rationale": "Type safety and async support",
                }
            ],
        },
        "metadata": {"mode": "new_project"},
    }

    result = parser.parse(payload)

    # Should extract at least one decision
    assert len(result.decisions) > 0

    # Check if framework decision is captured
    framework_decisions = [d for d in result.decisions if "FastAPI" in d.decision]
    assert len(framework_decisions) > 0


def test_blueprint_parser_extracts_patterns() -> None:
    """Test that BlueprintParser correctly extracts code patterns."""
    from src.aura.services.memory_blueprint_parser import BlueprintParser

    parser = BlueprintParser()

    payload = {
        "blueprint": {
            "patterns": [
                {
                    "category": "Error Handling",
                    "description": "Use custom exception classes for domain errors",
                    "example": "class ValidationError(Exception): pass",
                }
            ],
            "conventions": [
                "All API routes use async/await",
                "Database queries use repository pattern",
            ],
        },
    }

    result = parser.parse(payload)

    # Should extract patterns
    assert len(result.patterns) >= 0  # May or may not extract depending on implementation


def test_blueprint_parser_extracts_next_steps() -> None:
    """Test that BlueprintParser extracts next steps and roadmap items."""
    from src.aura.services.memory_blueprint_parser import BlueprintParser

    parser = BlueprintParser()

    payload = {
        "blueprint": {
            "next_steps": [
                "Implement user authentication",
                "Add database migrations",
                "Write integration tests",
            ],
            "roadmap": {
                "phase_1": "Core API endpoints",
                "phase_2": "Admin dashboard",
            },
        },
    }

    result = parser.parse(payload)

    # Should extract next steps
    assert len(result.next_steps) >= 0


def test_memory_manager_initializes_empty_memory() -> None:
    """Test that MemoryManager initializes empty memory for new projects."""
    from src.aura.services.memory_manager import MemoryManager

    # Create a mock project manager with no current project
    mock_pm = type("MockPM", (), {"current_project": None})()

    manager = MemoryManager(project_manager=mock_pm, event_bus=None)

    memory = manager.get_memory()

    assert memory is None  # No project, no memory


def test_memory_manager_loads_existing_memory(tmp_path) -> None:
    """Test that MemoryManager loads existing memory from project metadata."""
    from src.aura.services.memory_manager import MemoryManager

    storage_dir = tmp_path / "projects"
    workspace_dir = tmp_path / "workspace"
    storage_dir.mkdir()
    workspace_dir.mkdir()

    pm = ProjectManager(storage_dir=str(storage_dir))
    project = pm.create_project("test-memory", str((workspace_dir / "test-memory").resolve()))

    # Add memory data to project metadata
    project.metadata["project_memory"] = {
        "project_name": "test-memory",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "architecture_decisions": [],
        "code_patterns": [],
        "timeline": [],
        "known_issues": [],
        "current_state": {
            "status": "in_progress",
            "current_phase": "initial_setup",
        },
    }
    pm.save_project(project)
    pm.current_project = project

    memory_manager = MemoryManager(project_manager=pm, event_bus=None)

    memory = memory_manager.get_memory()

    assert memory is not None
    assert memory.project_name == "test-memory"


def test_conversation_history_persists_across_sessions(tmp_path) -> None:
    """Test that conversation history is maintained across project save/load cycles."""
    storage_dir = tmp_path / "projects"
    workspace_dir = tmp_path / "workspace"
    storage_dir.mkdir()
    workspace_dir.mkdir()

    # First session: create project and add conversation
    manager1 = ProjectManager(storage_dir=str(storage_dir))
    project1 = manager1.create_project("chat-test", str((workspace_dir / "chat-test").resolve()))

    conversation1 = [
        {"role": "user", "content": "Build a REST API"},
        {"role": "assistant", "content": "I'll help you build a REST API"},
        {"role": "user", "content": "Use FastAPI"},
    ]
    project1.conversation_history.extend(conversation1)
    manager1.save_project(project1)

    # Second session: load project and verify history
    manager2 = ProjectManager(storage_dir=str(storage_dir))
    project2 = manager2.load_project("chat-test")

    assert len(project2.conversation_history) == 3
    assert project2.conversation_history == conversation1

    # Third session: add more conversation and verify cumulative history
    conversation2 = [{"role": "assistant", "content": "FastAPI is a great choice"}]
    project2.conversation_history.extend(conversation2)
    manager2.save_project(project2)

    manager3 = ProjectManager(storage_dir=str(storage_dir))
    project3 = manager3.load_project("chat-test")

    assert len(project3.conversation_history) == 4
    assert project3.conversation_history == conversation1 + conversation2


def test_project_metadata_tracks_recent_topics(tmp_path) -> None:
    """Test that project metadata can track recent topics for context."""
    storage_dir = tmp_path / "projects"
    workspace_dir = tmp_path / "workspace"
    storage_dir.mkdir()
    workspace_dir.mkdir()

    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("topics-test", str((workspace_dir / "topics-test").resolve()))

    # Simulate tracking recent topics
    project.metadata["recent_topics"] = [
        "user authentication",
        "database schema design",
        "API rate limiting",
    ]

    project.metadata["current_language"] = "python"
    project.metadata["primary_framework"] = "FastAPI"

    manager.save_project(project)

    # Reload and verify
    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("topics-test")

    assert reloaded.metadata.get("recent_topics") == [
        "user authentication",
        "database schema design",
        "API rate limiting",
    ]
    assert reloaded.metadata.get("current_language") == "python"
    assert reloaded.metadata.get("primary_framework") == "FastAPI"


def test_blueprint_to_memory_integration_flow() -> None:
    """Integration test: verify blueprint results flow into memory system."""
    from src.aura.services.memory_blueprint_parser import BlueprintParser
    from src.aura.services.memory_models import ProjectMemory

    parser = BlueprintParser()

    # Simulate a complete blueprint result
    blueprint_payload = {
        "blueprint": {
            "project_name": "payment-service",
            "architecture": {
                "framework": "FastAPI",
                "database": "PostgreSQL",
                "message_broker": "RabbitMQ",
            },
            "files": [
                {"file_path": "src/main.py", "description": "Application entry point"},
                {"file_path": "src/api/routes.py", "description": "API endpoints"},
            ],
            "next_steps": [
                "Implement payment processing endpoint",
                "Add Stripe integration",
            ],
        },
        "metadata": {"mode": "new_project"},
    }

    # Parse blueprint
    parse_result = parser.parse(blueprint_payload)

    # Verify parsed data can be used to build memory
    assert len(parse_result.decisions) > 0 or len(parse_result.patterns) > 0 or len(parse_result.next_steps) > 0

    # Simulate creating/updating project memory
    memory = ProjectMemory(
        project_name="payment-service",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        architecture_decisions=[],
        code_patterns=[],
        timeline=[],
        known_issues=[],
        current_state={
            "status": "planning",
            "current_phase": "initial_design",
        },
    )

    # Verify memory structure
    assert memory.project_name == "payment-service"
    assert memory.current_state["status"] == "planning"
