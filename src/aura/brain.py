import json
import logging
import re
from typing import Any, Dict

from src.aura.models.action import Action, ActionType
from src.aura.models.project_context import ProjectContext
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class AuraBrain:
    """Brain layer: the single source of truth for decisions.

    Responsibilities:
    - Parse user requests with a Cognitive Router (LLM-backed with heuristics fallback).
    - Use the current ProjectContext when making decisions.
    - Never executes work; only returns an Action.
    """

    def __init__(self, llm: LLMService, prompts: PromptManager):
        self.llm = llm
        self.prompts = prompts

    def decide(self, user_text: str, context: ProjectContext) -> Action:
        """Return the next Action based on the user input and context."""
        try:
            routed = self._cognitive_route(user_text)
            action = (routed or {}).get("action")
            params = (routed or {}).get("params") or {}
        except Exception:
            action = None
            params = {}

        if action == ActionType.DESIGN_BLUEPRINT.value:
            return Action(type=ActionType.DESIGN_BLUEPRINT, params={"request": user_text} | params)
        if action == ActionType.REFINE_CODE.value:
            file_path = params.get("file_path") or "workspace/generated.py"
            request_text = params.get("request") or user_text
            return Action(type=ActionType.REFINE_CODE, params={"file_path": file_path, "request": request_text})

        # Safe default
        return Action(type=ActionType.REFINE_CODE, params={"file_path": "workspace/generated.py", "request": user_text})

    # --------------- Cognitive Router ---------------
    def _cognitive_route(self, user_text: str) -> Dict[str, Any]:
        prompt = self.prompts.render("lead_companion.jinja2", user_text=user_text)
        if not prompt:
            return self._fallback_route(user_text)
        raw = self.llm.run_for_agent("cognitive_router", prompt)
        clean = self._strip_code_fences(raw)
        try:
            data = json.loads(clean)
            return data if isinstance(data, dict) else self._fallback_route(user_text)
        except Exception:
            return self._fallback_route(user_text)

    def _fallback_route(self, user_text: str) -> Dict[str, Any]:
        text = (user_text or "").lower()
        if any(kw in text for kw in ["new project", "blueprint", "plan", "design"]):
            return {"action": ActionType.DESIGN_BLUEPRINT.value, "params": {"request": user_text}}
        return {"action": ActionType.REFINE_CODE.value, "params": {"file_path": "workspace/generated.py", "request": user_text}}

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"^```\w*\s*\n?", "", t, flags=re.MULTILINE)
        t = re.sub(r"\n?```\s*$", "", t, flags=re.MULTILINE)
        return t.strip()

