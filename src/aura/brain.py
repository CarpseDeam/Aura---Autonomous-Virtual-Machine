import json
import logging
import re
from typing import Any, Dict, List

from src.aura.models.action import Action, ActionType
from src.aura.models.project_context import ProjectContext
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class AuraBrain:
    """Brain layer: the single source of truth for decisions."""

    def __init__(self, llm: LLMService, prompts: PromptManager):
        self.llm = llm
        self.prompts = prompts

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
        clean = self._strip_code_fences(raw)
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
    def _strip_code_fences(text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"^```\w*\s*\n?", "", t, flags=re.MULTILINE)
        t = re.sub(r"\n?```\s*$", "", t, flags=re.MULTILINE)
        return t.strip()

