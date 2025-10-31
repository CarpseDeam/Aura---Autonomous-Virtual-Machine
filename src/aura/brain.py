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
        clarification_context = self._build_clarification_context(history, user_text)

        prompt = self.prompts.render(
            "reasoning_prompt.jinja2",
            user_text=user_text,
            conversation_history=history,
            clarification_context=clarification_context,
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

        proposed_action = self._action_from_payload(action_payload)

        confidence, unclear_aspects, clarifying_questions = self._extract_confidence_data(data)
        if unclear_aspects:
            logger.info(
                "LLM confidence %.2f; unclear aspects: %s",
                confidence,
                "; ".join(unclear_aspects),
            )
        else:
            logger.info("LLM confidence %.2f; no unclear aspects flagged.", confidence)

        if confidence < 0.7:
            if clarifying_questions:
                logger.info(
                    "Confidence below threshold; overriding to DISCUSS with %d questions.",
                    len(clarifying_questions),
                )
                return Action(
                    type=ActionType.DISCUSS,
                    params={
                        "questions": clarifying_questions,
                        "unclear_aspects": unclear_aspects,
                        "original_action": action_payload,
                        "original_confidence": confidence,
                    },
                )

            logger.warning(
                "Confidence below threshold but no clarifying questions provided; requesting additional detail via simple reply."
            )
            fallback_message = self._build_fallback_request(unclear_aspects)
            return Action(
                type=ActionType.SIMPLE_REPLY,
                params={"request": fallback_message},
            )

        return proposed_action

    def _build_clarification_context(self, history: List[Dict[str, Any]], _latest_user_text: str) -> str:
        """Summarize prior DISCUSS rounds so the LLM knows what was unclear."""
        if not history:
            return ""

        for idx in range(len(history) - 1, -1, -1):
            message = history[idx] or {}
            role = str(message.get("role") or "").lower()
            if role != "assistant":
                continue

            action_marker = str(
                message.get("action_type")
                or (message.get("metadata") or {}).get("action_type")
                or ""
            ).lower()
            if action_marker != ActionType.DISCUSS.value:
                continue

            unclear_aspects = self._coerce_list_of_str(message.get("unclear_aspects"))
            clarifying_questions = self._coerce_list_of_str(
                message.get("clarifying_questions") or message.get("questions")
            )
            original_action = message.get("original_action") if isinstance(message, dict) else None

            previous_user = next(
                (
                    msg
                    for msg in reversed(history[:idx])
                    if (msg or {}).get("role") == "user"
                ),
                None,
            )

            parts: List[str] = []
            if previous_user and isinstance(previous_user.get("content"), str):
                previous_content = previous_user["content"].strip()
                if previous_content:
                    parts.append(f"Previous user request: {previous_content}")

            if unclear_aspects:
                parts.append("Unclear aspects noted: " + "; ".join(unclear_aspects))

            if clarifying_questions:
                formatted_questions = "\n".join(f"- {question}" for question in clarifying_questions)
                parts.append("Questions previously asked:\n" + formatted_questions)

            if isinstance(original_action, dict):
                orig_type = original_action.get("type")
                if isinstance(orig_type, str) and orig_type:
                    parts.append(f"Original intended action after clarification: {orig_type}")

            parts.append(
                "The current user message likely addresses these questions. Incorporate the new details before deciding the next action."
            )
            return "\n".join(parts).strip()

        return ""

    def _extract_confidence_data(self, data: Dict[str, Any]) -> Tuple[float, List[str], List[str]]:
        confidence = self._safe_float(data.get("confidence"), 0.8)
        confidence = max(0.0, min(1.0, confidence))

        unclear_aspects = self._coerce_list_of_str(data.get("unclear_aspects"))
        clarifying_questions = self._coerce_list_of_str(data.get("clarifying_questions"))

        return confidence, unclear_aspects, clarifying_questions

    def _build_fallback_request(self, unclear_aspects: List[str]) -> str:
        if unclear_aspects:
            summary = "; ".join(unclear_aspects)
            return (
                "I'd love to help, but I need a bit more clarity first. "
                f"Could you share more about {summary}?"
            )
        return "I'd love to help. Could you share a bit more detail so I know exactly how to proceed?"

    @staticmethod
    def _coerce_list_of_str(value: Any) -> List[str]:
        if isinstance(value, list):
            coerced: List[str] = []
            for item in value:
                if item is None:
                    continue
                if isinstance(item, dict):
                    candidate = next(
                        (
                            item.get(key)
                            for key in ("question", "text", "content", "value")
                            if isinstance(item.get(key), str) and item.get(key).strip()
                        ),
                        None,
                    )
                    text = candidate if isinstance(candidate, str) else str(item)
                else:
                    text = str(item)
                text = text.strip()
                if text:
                    coerced.append(text)
            return coerced
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        return []

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

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

