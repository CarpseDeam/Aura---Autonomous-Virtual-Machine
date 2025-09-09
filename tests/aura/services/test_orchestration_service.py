import json
from typing import Any, Dict, List, Optional

import pytest

from src.aura.models.events import Event
from src.aura.services.orchestration_service import _OrchestrationWorker


class DummyPromptManager:
    def render(self, template_name: str, **kwargs) -> str:
        # Return a non-empty string for any template
        return f"prompt:{template_name}"


class FakeLLM:
    def __init__(self, responses: Optional[Dict[str, str]] = None):
        # Map of agent_name -> response string
        self.responses = responses or {}
        self.calls: List[Dict[str, Any]] = []

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        self.calls.append({"agent": agent_name, "prompt": prompt})
        return self.responses.get(agent_name, "")


class DummyAST:
    def __init__(self, project_root: str = ""):
        self.project_root = project_root


# ------------------- Router Tests -------------------
def test_router_picks_design_blueprint_on_design_keyword():
    # LLM returns invalid JSON so fallback router triggers based on keyword
    llm = FakeLLM(responses={"cognitive_router": "not json"})
    worker = _OrchestrationWorker(
        user_text="Please design a snake game",
        llm=llm,
        ast=DummyAST(),
        prompts=DummyPromptManager(),
    )
    result = worker._cognitive_route("Please design a snake game")
    assert result.get("action") == "design_blueprint"


def test_router_picks_refine_code_on_modify_prompt():
    llm = FakeLLM(responses={"cognitive_router": "not json"})
    worker = _OrchestrationWorker(
        user_text="modify main.py",
        llm=llm,
        ast=DummyAST(),
        prompts=DummyPromptManager(),
    )
    result = worker._cognitive_route("modify main.py")
    assert result.get("action") == "refine_code"
    params = result.get("params") or {}
    assert "file_path" in params


# ------------------- design_blueprint Workflow -------------------
def test_design_blueprint_guardian_protocol_blocks_empty_blueprint(qcore_app):
    # Router says: design_blueprint
    router_json = json.dumps({"action": "design_blueprint", "params": {}})
    # Architect returns empty dict (no files)
    llm = FakeLLM(responses={
        "cognitive_router": router_json,
        "architect_agent": json.dumps({}),
    })

    worker = _OrchestrationWorker(
        user_text="design a tool",
        llm=llm,
        ast=DummyAST(),
        prompts=DummyPromptManager(),
    )

    emitted_events: List[Event] = []
    errors: List[str] = []

    worker.event_ready.connect(lambda ev: emitted_events.append(ev))
    worker.error.connect(lambda msg: errors.append(msg))

    worker.run()

    # Guardian Protocol: should emit an error and no ADD_TASK/BLUEPRINT_APPROVED events
    assert errors, "Expected an error to be emitted for empty blueprint"
    assert not [e for e in emitted_events if e.event_type == "ADD_TASK"], "No tasks should be created"
    assert not [e for e in emitted_events if e.event_type == "BLUEPRINT_APPROVED"], "Blueprint should not be auto-approved"
    assert not [e for e in emitted_events if e.event_type == "BLUEPRINT_GENERATED"], "No blueprint summary for invalid blueprint"


def test_design_blueprint_emits_summary_and_tasks(qcore_app):
    router_json = json.dumps({"action": "design_blueprint", "params": {}})
    valid_blueprint = {
        "project_name": "Demo",
        "files": [
            {
                "file_path": "workspace/foo.py",
                "functions": [
                    {"function_name": "f", "signature": "def f():", "description": "make f"}
                ],
            },
            {
                "file_path": "workspace/bar.py",
                "classes": [
                    {
                        "class_name": "C",
                        "methods": [
                            {"method_name": "m", "signature": "def m(self):", "description": "make m"}
                        ],
                    }
                ],
            },
        ],
    }

    llm = FakeLLM(responses={
        "cognitive_router": router_json,
        "architect_agent": json.dumps(valid_blueprint),
    })

    worker = _OrchestrationWorker(
        user_text="design demo",
        llm=llm,
        ast=DummyAST(),
        prompts=DummyPromptManager(),
    )

    emitted: List[Event] = []
    worker.event_ready.connect(lambda ev: emitted.append(ev))
    worker.error.connect(lambda msg: pytest.fail(f"Unexpected error: {msg}"))

    worker.run()

    # Expect: BLUEPRINT_GENERATED, 2x ADD_TASK, BLUEPRINT_APPROVED
    types = [e.event_type for e in emitted]
    assert "BLUEPRINT_GENERATED" in types
    assert types.count("ADD_TASK") == 2
    assert "BLUEPRINT_APPROVED" in types


# ------------------- refine_code Workflow -------------------
def test_refine_code_calls_engineer_and_emits_code(qcore_app, tmp_path):
    # Force refine_code via router
    router_json = json.dumps({
        "action": "refine_code",
        "params": {"file_path": "workspace/test.py", "request": "update it"},
    })
    engineer_output = """```python\nprint('hello')\n```"""

    llm = FakeLLM(responses={
        "cognitive_router": router_json,
        "engineer_agent": engineer_output,
    })

    # Point AST project root to tmp so file read check is safe
    ast = DummyAST(project_root=str(tmp_path))
    worker = _OrchestrationWorker(
        user_text="modify workspace/test.py",
        llm=llm,
        ast=ast,
        prompts=DummyPromptManager(),
    )

    captured: List[Event] = []
    worker.event_ready.connect(lambda ev: captured.append(ev))
    worker.error.connect(lambda msg: pytest.fail(f"Unexpected error: {msg}"))

    worker.run()

    # Validate engineer was invoked
    assert any(c["agent"] == "engineer_agent" for c in llm.calls), "Engineer agent was not called"

    # Validate CODE_GENERATED emission with sanitized code
    code_events = [e for e in captured if e.event_type == "CODE_GENERATED"]
    assert len(code_events) == 1
    payload = code_events[0].payload
    assert payload.get("file_path") == "workspace/test.py"
    assert payload.get("code") == "print('hello')"

