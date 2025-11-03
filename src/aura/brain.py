import json
import logging
import re
from typing import Any, Dict, List, Tuple

from src.aura.models.action import Action, ActionType
from src.aura.models.exceptions import LLMServiceError
from src.aura.models.intent import Intent
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

    def _summarize_history_for_intent(self, history_slice: List[Dict[str, Any]]) -> str:
        """Convert recent conversation messages into a compact textual summary."""
        parts: List[str] = []
        for message in history_slice:
            if not isinstance(message, dict):
                continue

            role = str(message.get("role") or "unknown").lower()
            content = message.get("content")
            text = ""

            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                fragments = [
                    fragment.strip()
                    for fragment in content
                    if isinstance(fragment, str) and fragment.strip()
                ]
                text = " ".join(fragments)
            elif content is not None:
                try:
                    text = json.dumps(content)
                except TypeError:
                    text = str(content)

            if not text:
                text = "(no content provided)"

            metadata = message.get("metadata")
            action_type = None
            if isinstance(metadata, dict):
                raw_action_type = metadata.get("action_type")
                if isinstance(raw_action_type, str) and raw_action_type:
                    action_type = raw_action_type

            if not action_type:
                fallback_action_type = message.get("action_type")
                if isinstance(fallback_action_type, str) and fallback_action_type:
                    action_type = fallback_action_type

            if action_type:
                parts.append(f"{role}: {text} [action_type={action_type}]")
            else:
                parts.append(f"{role}: {text}")

        if not parts:
            return "No recent conversation history provided."

        return "\n".join(parts)

    def _detect_user_intent(self, user_text: str, conversation_history: List[Dict[str, Any]]) -> Intent:
        """
        Detect what the user actually wants before deciding action.

        Uses the LLM to classify user intent based on:
        - The latest user message
        - Recent conversation history for context

        Args:
            user_text: Latest user message.
            conversation_history: Recent conversation for context.

        Returns:
            Intent enum value.
        """
        recent_history = conversation_history[-5:] if conversation_history else []
        serialized_history = self._summarize_history_for_intent(recent_history)

        prompt = self.prompts.render(
            "intent_detection_prompt.jinja2",
            user_text=user_text,
            conversation_history=serialized_history,
        )
        if not prompt:
            logger.warning("Failed to render intent detection prompt; defaulting to CASUAL_CHAT.")
            return Intent.CASUAL_CHAT

        try:
            raw_response = self.llm.run_for_agent("intent_detection_agent", prompt)
        except LLMServiceError as exc:
            logger.warning(
                "Intent detection failed after retries for agent 'intent_detection_agent': %s. "
                "Defaulting to CASUAL_CHAT.",
                exc,
            )
            return Intent.CASUAL_CHAT
        except Exception as exc:
            logger.error("Intent detection agent call failed: %s", exc, exc_info=True)
            return Intent.CASUAL_CHAT

        if not isinstance(raw_response, str):
            logger.warning(
                "Intent detection agent returned non-string response; defaulting to CASUAL_CHAT."
            )
            return Intent.CASUAL_CHAT

        normalized = raw_response.strip()
        if not normalized:
            logger.warning("Intent detection agent provided empty response; defaulting to CASUAL_CHAT.")
            return Intent.CASUAL_CHAT

        normalized_upper = normalized.upper()
        normalized_lower = normalized.lower()

        for intent in Intent:
            if normalized_upper == intent.name or normalized_lower == intent.value:
                logger.info("Detected user intent: %s", intent.name)
                return intent

        logger.warning("Unrecognized intent response '%s'; defaulting to CASUAL_CHAT.", normalized)
        return Intent.CASUAL_CHAT

    def _summarize_context_value(self, value: Any, empty_default: str) -> str:
        """Convert context extras into a human-friendly summary string."""
        if value is None:
            return empty_default

        if isinstance(value, str):
            stripped = value.strip()
            return stripped or empty_default

        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return ", ".join(items) if items else empty_default

        if isinstance(value, dict):
            pairs = []
            for key, item in value.items():
                key_str = str(key).strip()
                item_str = str(item).strip() if isinstance(item, str) else str(item)
                if key_str and item_str:
                    pairs.append(f"{key_str}: {item_str}")
            return ", ".join(pairs) if pairs else empty_default

        try:
            return str(value)
        except Exception:
            return empty_default

    def _project_relationship_context(self, context: ProjectContext) -> Tuple[str, str, str]:
        """Extract project name and relationship notes for prompt conditioning."""
        project_name = context.active_project or "Untitled project"
        extras: Dict[str, Any] = context.extras or {}
        recent_topics = self._summarize_context_value(
            extras.get("recent_topics"),
            "None yet.",
        )
        ongoing_work = self._summarize_context_value(
            extras.get("ongoing_work"),
            "No active tasks noted.",
        )
        return project_name, recent_topics, ongoing_work

    def decide(self, user_text: str, context: ProjectContext) -> Action:
        """Return the next Action based on the user input and context."""
        history = context.conversation_history or []

        # CRITICAL: Pre-check for advice-seeking phrases before LLM intent detection
        # These phrases ALWAYS indicate seeking advice, regardless of other keywords
        advice_trigger_phrases = [
            "what do you think",
            "what would you",
            "what should",
            "should i",
            "not sure",
            "not totally sure",
            "not certain",
            "uncertain",
            "recommend",
            "recommendation",
            "your opinion",
            "your thoughts",
            "advice on",
            "thoughts on",
            "which is better",
            " vs ",
            " versus ",
            "evaluate",
            "evaluating",
            "considering",
            "or should",
        ]

        user_text_lower = user_text.lower()
        has_advice_phrase = any(phrase in user_text_lower for phrase in advice_trigger_phrases)

        if has_advice_phrase:
            logger.info(
                "PRE-CHECK: Advice-seeking phrase detected, routing directly to SIMPLE_REPLY (bypassing intent detection)"
            )
            return Action(
                type=ActionType.SIMPLE_REPLY,
                params={"request": user_text},
            )

        # No advice phrases found, proceed with normal intent detection
        intent = self._detect_user_intent(user_text, history)

        if intent in (Intent.CASUAL_CHAT, Intent.SEEKING_ADVICE):
            logger.info("Routing to SIMPLE_REPLY for intent: %s", intent.name)
            return Action(
                type=ActionType.SIMPLE_REPLY,
                params={"request": user_text},
            )

        clarification_context = self._build_clarification_context(history, user_text)
        project_name, recent_topics, ongoing_work = self._project_relationship_context(context)

        prompt = self.prompts.render(
            "reasoning_prompt.jinja2",
            user_text=user_text,
            conversation_history=history,
            clarification_context=clarification_context,
            detected_intent=intent.name,
            project_name=project_name,
            recent_topics=recent_topics,
            ongoing_work=ongoing_work,
        )
        if not prompt:
            raise RuntimeError("Failed to render reasoning prompt")

        try:
            raw = self.llm.run_for_agent("reasoning_agent", prompt)
        except LLMServiceError as exc:
            logger.error(
                "Reasoning agent call failed after retries; returning SIMPLE_REPLY fallback. Error: %s",
                exc,
                exc_info=True,
            )
            return Action(
                type=ActionType.SIMPLE_REPLY,
                params={
                    "request": (
                        "I'm having trouble connecting to my reasoning engine right now. "
                        "Let me try to help you anyway - what do you need?"
                    )
                },
            )
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

        # Guardrail for build transition: ensure SPAWN_AGENT has a specification
        # Option B (preferred fast-path): if a latest specification exists in context, reference it
        if proposed_action.type == ActionType.SPAWN_AGENT:
            extras = context.extras or {}
            has_latest = isinstance(extras.get("latest_specification"), (dict,))
            params = dict(proposed_action.params or {})
            has_inline_spec = isinstance(params.get("specification"), (dict, str))

            if has_inline_spec:
                logger.debug("SPAWN_AGENT provided with inline specification; proceeding.")
                return proposed_action

            if has_latest:
                logger.info(
                    "SPAWN_AGENT selected with no inline spec; using cached latest_specification."
                )
                params["specification"] = "latest"
                return Action(type=ActionType.SPAWN_AGENT, params=params)

            # Option A: No spec available; design blueprint first and auto-spawn
            logger.info(
                "SPAWN_AGENT selected but no specification available; switching to DESIGN_BLUEPRINT with auto_spawn."
            )
            request_text = (
                str(data.get("request")).strip() if isinstance(data.get("request"), str) and str(data.get("request")).strip() else user_text
            )
            return Action(
                type=ActionType.DESIGN_BLUEPRINT,
                params={
                    "request": request_text,
                    "auto_spawn": True,
                },
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

