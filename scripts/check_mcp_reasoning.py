"""
Quick check to validate that the Brain maps
"Start an MCP filesystem server" to MCP_START_SERVER with template=filesystem.

This uses a local LLM stub that inspects the rendered reasoning prompt
and returns a JSON action accordingly. It avoids external API calls.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
import sys
from pathlib import Path

# Ensure repository src/ is on sys.path for local imports
repo_root = Path(__file__).resolve().parents[1]
# Add both the repo root (so 'src.*' imports inside modules resolve)
# and the 'src' itself (so we can import 'aura.*' directly here).
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from aura.brain import AuraBrain
from aura.models.action import ActionType
from aura.models.intent import Intent
from aura.models.project_context import ProjectContext
from aura.prompts.prompt_manager import PromptManager


class EchoingPromptManager(PromptManager):
    """Use real template rendering from PromptManager."""
    pass


class MCPAwareLLMStub:
    """LLM stub that returns intents and MCP actions based on prompt content."""

    def __init__(self, expected_user_snippet: str) -> None:
        self.expected_user_snippet = expected_user_snippet

    def run_for_agent(self, agent: str, prompt: str) -> str:
        if agent == "intent_detection_agent":
            # Treat this as a clear build/execution request
            return Intent.BUILD_CLEAR.name

        if agent == "reasoning_agent":
            # Very small heuristic: if the user request appears to start an MCP server,
            # return an MCP_START_SERVER action with template=filesystem.
            lower_prompt = prompt.lower()
            if "start an mcp" in lower_prompt or "start an mcp server" in lower_prompt:
                payload = {
                    "thought": "User asked to start an MCP server; selecting MCP_START_SERVER.",
                    "confidence": 0.95,
                    "unclear_aspects": [],
                    "clarifying_questions": [],
                    "action": {
                        "type": "MCP_START_SERVER",
                        "params": {"template": "filesystem"},
                    },
                }
                return json.dumps(payload)

            # Fallback simple reply
            return json.dumps(
                {
                    "action": {"type": "SIMPLE_REPLY", "params": {"request": "fallback"}},
                    "confidence": 1.0,
                    "unclear_aspects": [],
                    "clarifying_questions": [],
                }
            )

        raise RuntimeError(f"Unexpected agent: {agent}")


def main() -> int:
    user_text = "Start an MCP filesystem server"
    llm = MCPAwareLLMStub(expected_user_snippet=user_text)
    prompts = EchoingPromptManager()
    brain = AuraBrain(llm, prompts)

    action = brain.decide(user_text, ProjectContext())

    print("Selected action:", action.type.value)
    print("Params:", action.params)

    assert action.type == ActionType.MCP_START_SERVER, "Expected MCP_START_SERVER action"
    assert action.get_param("template") == "filesystem", "Expected template=filesystem"
    print("OK: Brain maps request to MCP_START_SERVER with template=filesystem")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
