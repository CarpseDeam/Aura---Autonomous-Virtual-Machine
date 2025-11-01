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

    def render(self, template_name: str, **_: Any) -> str:
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
