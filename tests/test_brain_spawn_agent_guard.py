from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import MagicMock

from src.aura.brain import AuraBrain
from src.aura.models.action import ActionType
from src.aura.models.intent import Intent
from src.aura.models.project_context import ProjectContext


def _make_brain(response: Dict[str, Any]) -> AuraBrain:
    llm = MagicMock()
    llm.run_for_agent.return_value = json.dumps(response)
    prompts = MagicMock()
    prompts.render.return_value = "prompt"
    brain = AuraBrain(llm=llm, prompts=prompts)
    brain._detect_user_intent = MagicMock(return_value=Intent.BUILD_CLEAR)  # type: ignore[attr-defined]
    return brain


def test_spawn_agent_invalid_inline_spec_triggers_blueprint_guard() -> None:
    response = {
        "thought": "Spawning Gemini agent.",
        "confidence": 0.9,
        "request": "scaffold weather dashboard",
        "action": {
            "type": "spawn_agent",
            "params": {
                "specification": {
                    "name": "scaffold-weather-app",
                    "context": {"file_paths": []},
                }
            },
        },
    }
    brain = _make_brain(response)
    context = ProjectContext()

    action = brain.decide("Please scaffold a weather dashboard", context)

    assert action.type == ActionType.DESIGN_BLUEPRINT
    assert action.params.get("auto_spawn") is True
    assert action.params.get("request") == "scaffold weather dashboard"


def test_spawn_agent_invalid_inline_spec_uses_latest_specification() -> None:
    response = {
        "thought": "Spawn agent using cached plan.",
        "confidence": 0.95,
        "request": "build reporting module",
        "action": {
            "type": "spawn_agent",
            "params": {
                "specification": {
                    "name": "reporting-module",
                    "context": {"file_paths": []},
                }
            },
        },
    }
    brain = _make_brain(response)
    context = ProjectContext(
        extras={
            "latest_specification": {
                "task_id": "cached-123",
                "request": "build reporting module",
                "project_name": "reports",
                "prompt": "cached prompt",
                "blueprint": {},
            }
        }
    )

    action = brain.decide("Please build the reporting module", context)

    assert action.type == ActionType.SPAWN_AGENT
    assert action.params.get("specification") == "latest"


def test_spawn_agent_valid_inline_spec_passes_through() -> None:
    inline_spec = {
        "task_id": "inline-456",
        "request": "add metrics collector",
        "project_name": "metrics",
        "prompt": "prompt body",
        "blueprint": {},
    }
    response = {
        "thought": "Inline spec prepared.",
        "confidence": 0.92,
        "action": {
            "type": "spawn_agent",
            "params": {
                "specification": inline_spec,
            },
        },
    }
    brain = _make_brain(response)
    context = ProjectContext()

    action = brain.decide("Add a metrics collector", context)

    assert action.type == ActionType.SPAWN_AGENT
    assert action.params.get("specification") == inline_spec


def test_spawn_agent_unknown_string_spec_triggers_blueprint_guard() -> None:
    response = {
        "thought": "Spawn using provided token.",
        "confidence": 0.9,
        "request": "build weather app",
        "action": {
            "type": "spawn_agent",
            "params": {
                "specification": "gemini",
            },
        },
    }
    brain = _make_brain(response)
    context = ProjectContext()

    action = brain.decide("Create a weather app", context)

    assert action.type == ActionType.DESIGN_BLUEPRINT
    assert action.params.get("auto_spawn") is True
    assert action.params.get("request") == "build weather app"


def test_spawn_agent_latest_string_spec_passes_through() -> None:
    response = {
        "thought": "Spawn latest spec.",
        "confidence": 0.93,
        "action": {
            "type": "spawn_agent",
            "params": {
                "specification": "latest",
            },
        },
    }
    brain = _make_brain(response)
    context = ProjectContext(
        extras={
            "latest_specification": {
                "task_id": "latest-001",
                "request": "upgrade pipeline",
                "prompt": "cached prompt",
            }
        }
    )

    action = brain.decide("Upgrade the deployment pipeline", context)

    assert action.type == ActionType.SPAWN_AGENT
    assert action.params.get("specification") == "latest"
