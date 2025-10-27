import json
import logging
import re
from typing import Dict, List

from src.aura.models.action import Action, ActionType
from src.aura.models.intent import Intent
from src.aura.models.project_context import ProjectContext
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class AuraBrain:
    """Brain layer: the single source of truth for decisions.

    Responsibilities:
    - Classify user intent as the single source of truth.
    - Use the current ProjectContext when making decisions.
    - Never executes work; only returns an Action.
    """

    def __init__(self, llm: LLMService, prompts: PromptManager):
        self.llm = llm
        self.prompts = prompts

    def decide(self, user_text: str, context: ProjectContext) -> List[Action]:
        """Return a plan (list of Actions) based on the user input and context."""
        intent = self._detect_intent(user_text, context)

        if intent == Intent.CHITCHAT:
            return [Action(type=ActionType.SIMPLE_REPLY, params={"request": user_text})]

        if intent == Intent.PLANNING_SESSION:
            return [Action(type=ActionType.DESIGN_BLUEPRINT, params={"request": user_text})]

        # Safe default for ambiguous intents: treat as refine code on the scratchpad.
        return [
            Action(
                type=ActionType.REFINE_CODE,
                params={"file_path": "workspace/generated.py", "request": user_text},
            )
        ]

    # --------------- Intent Router ---------------
    def _detect_intent(self, user_text: str, context: ProjectContext) -> Intent:
        """Use a lightweight LLM prompt with heuristics fallback to classify user intent."""
        heuristic_guess = self._intent_from_heuristics(user_text)

        history = context.conversation_history or []
        recent_history: List[Dict[str, str]] = history[-6:] if history else []

        prompt = self.prompts.render(
            "cognitive_router.jinja2",
            user_text=user_text,
            conversation_history=recent_history,
        )
        if not prompt:
            return heuristic_guess

        raw = self.llm.run_for_agent("cognitive_router", prompt)
        clean = self._strip_code_fences(raw)
        try:
            data = json.loads(clean) if clean else {}
        except Exception:
            data = {}

        intent_value = None
        if isinstance(data, dict):
            intent_value = data.get("intent") or data.get("Intent")

        try:
            if intent_value:
                return Intent(intent_value.upper())
        except Exception:
            pass

        confidence = data.get("confidence") if isinstance(data, dict) else None
        if isinstance(confidence, (int, float)) and confidence >= 0.75 and heuristic_guess != Intent.UNKNOWN:
            return heuristic_guess

        return heuristic_guess

    @staticmethod
    def _intent_from_heuristics(user_text: str) -> Intent:
        """Keyword heuristics to keep the router resilient if the LLM fails."""
        text = (user_text or "").strip().lower()
        if not text:
            return Intent.UNKNOWN

        greetings = {"hi", "hey", "hello", "yo", "sup", "good morning", "good afternoon", "good evening"}
        farewell = {"bye", "goodbye", "see ya", "later", "ttyl"}
        polite_nops = {"thanks", "thank you", "appreciate it"}

        normalized = re.sub(r"[^\w\s]", "", text)
        if normalized in greetings or normalized in farewell:
            return Intent.CHITCHAT
        if any(text.startswith(greet) for greet in greetings):
            return Intent.CHITCHAT
        if len(text.split()) <= 4 and any(word in text for word in greetings | polite_nops):
            return Intent.CHITCHAT

        planning_keywords = {"plan", "planning", "blueprint", "design", "architecture", "roadmap"}
        if any(word in text for word in planning_keywords):
            return Intent.PLANNING_SESSION

        return Intent.UNKNOWN

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"^```\w*\s*\n?", "", t, flags=re.MULTILINE)
        t = re.sub(r"\n?```\s*$", "", t, flags=re.MULTILINE)
        return t.strip()

