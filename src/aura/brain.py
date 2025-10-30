import json
import logging
import re
from typing import Any, Dict, List, Tuple

from src.aura.models.action import Action, ActionType
from src.aura.models.project_context import ProjectContext
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class AuraBrain:
    """Brain layer: the single source of truth for decisions."""

    _CODE_TASK_VERBS: Tuple[str, ...] = (
        "add",
        "build",
        "change",
        "create",
        "extend",
        "fix",
        "generate",
        "implement",
        "modify",
        "refactor",
        "scaffold",
        "update",
        "upgrade",
        "write",
    )
    _CODE_TASK_NOUNS: Tuple[str, ...] = (
        "api",
        "app",
        "application",
        "blueprint",
        "cli",
        "command",
        "component",
        "config",
        "endpoint",
        "feature",
        "file",
        "flag",
        "function",
        "handler",
        "library",
        "module",
        "pipeline",
        "project",
        "script",
        "service",
        "tool",
        "ui",
    )
    _CODE_TASK_PHRASES: Tuple[str, ...] = (
        "generate code",
        "create a project",
        "build me",
        "scaffold",
        "bootstrap",
        "refactor the",
        "fix the bug",
        "add a feature",
        "modify the code",
        "update the code",
        "change the code",
        "implement the",
    )
    _CODE_FILE_EXT_PATTERN = re.compile(
        r"\b[\w/\-]+\.(py|js|ts|tsx|jsx|java|go|rs|rb|swift|kt|c|cpp|cs|sh|ps1|json|yaml|yml|toml|ini|cfg|md|txt)\b"
    )

    def __init__(self, llm: LLMService, prompts: PromptManager):
        self.llm = llm
        self.prompts = prompts

    def is_code_request(self, user_text: str) -> bool:
        """Heuristically determine whether the user is asking for code generation or edits."""
        if not isinstance(user_text, str):
            return False
        normalized = " ".join(user_text.lower().split())
        if not normalized:
            return False

        if any(phrase in normalized for phrase in self._CODE_TASK_PHRASES):
            return True

        if self._CODE_FILE_EXT_PATTERN.search(normalized):
            return True

        if "--" in normalized and any(verb in normalized for verb in ("add", "update", "enable", "introduce", "support")):
            return True

        verb_hit = any(re.search(rf"\b{re.escape(verb)}\b", normalized) for verb in self._CODE_TASK_VERBS)
        if not verb_hit:
            return False

        if any(re.search(rf"\b{re.escape(noun)}\b", normalized) for noun in self._CODE_TASK_NOUNS):
            return True

        if "code" in normalized or "project" in normalized or "app" in normalized or "bug" in normalized:
            return True

        return False

    def decide(self, user_text: str, context: ProjectContext) -> Action:
        """Return the next Action based on the user input and context."""
        history = context.conversation_history or []

        prompt = self.prompts.render(
            "reasoning_prompt.jinja2",
            user_text=user_text,
            conversation_history=history,
        )
        if not prompt:
            raise RuntimeError("Failed to render reasoning prompt")

        raw = self.llm.run_for_agent("reasoning_agent", prompt)
        clean = self._extract_json_from_response(raw)
        try:
            data = json.loads(clean) if clean else {}
        except Exception as exc:
            logger.error("Failed to parse reasoning response: %s", exc, exc_info=True)
            raise RuntimeError("Reasoning response was not valid JSON") from exc

        if not isinstance(data, dict):
            raise RuntimeError("Reasoning response must be a JSON object")

        thought = data.get("thought")
        if isinstance(thought, str):
            logger.debug("Reasoning thought: %s", thought)

        action_payload = data.get("action")
        if not isinstance(action_payload, dict):
            raise RuntimeError("Reasoning response missing 'action' object")

        return self._action_from_payload(action_payload)

    def _action_from_payload(self, payload: Dict[str, Any]) -> Action:
        type_value = payload.get("type")
        if not isinstance(type_value, str):
            raise RuntimeError("Reasoning response action.type must be a string")

        action_type = self._resolve_action_type(type_value)

        params = payload.get("params")
        if not isinstance(params, dict):
            params = {}

        return Action(type=action_type, params=params)

    @staticmethod
    def _resolve_action_type(type_value: str) -> ActionType:
        candidate = type_value.strip()
        try:
            return ActionType[candidate.upper()]
        except KeyError:
            pass

        for fallback in (candidate, candidate.lower()):
            try:
                return ActionType(fallback)
            except ValueError:
                continue

        raise RuntimeError(f"Unknown action type returned by reasoning prompt: {type_value}")

    @staticmethod
    def _extract_json_from_response(text: str) -> str:
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            return match.group(0).strip()
        return text.strip()

