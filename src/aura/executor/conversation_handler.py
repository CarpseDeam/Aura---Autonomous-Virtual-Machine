"""Conversation-related action handlers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.aura.models.action import Action
from src.aura.models.exceptions import LLMServiceError
from src.aura.models.project_context import ProjectContext
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.llm_service import LLMService
from src.aura.services.research_service import ResearchService

from .code_sanitizer import CodeSanitizer
from .conversation_utils import (
    build_discuss_fallback_response,
    normalize_string_list,
    summarize_original_action,
)


logger = logging.getLogger(__name__)


class ConversationHandler:
    """Handle discussion, replies, and research requests."""

    def __init__(
        self,
        llm: LLMService,
        prompts: PromptManager,
        code_sanitizer: CodeSanitizer,
        research_service: Optional[ResearchService] = None,
    ) -> None:
        self.llm = llm
        self.prompts = prompts
        self.code_sanitizer = code_sanitizer
        self.research_service = research_service or ResearchService()

    def execute_research(self, action: Action, ctx: ProjectContext) -> Dict[str, Any]:
        """Perform external research on behalf of the user."""
        topic = action.get_param("topic") or action.get_param("subject") or action.get_param("request", "")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("Missing 'topic' parameter for research action")
        return self.research_service.research(topic.strip())

    def execute_simple_reply(self, action: Action, ctx: ProjectContext) -> str:
        """Generate a lightweight conversational response."""
        user_text = action.get_param("request", "")
        history = ctx.conversation_history or []
        recent_history = history[-6:] if history else []

        prompt = self.prompts.render(
            "chitchat_reply.jinja2",
            user_text=user_text,
            conversation_history=recent_history,
        )
        if not prompt:
            raise RuntimeError("Failed to render chitchat prompt")

        latest_user_message = next(
            (msg for msg in reversed(recent_history or []) if (msg or {}).get("role") == "user"),
            None,
        )
        attachments = []
        if latest_user_message:
            attachments = list((latest_user_message or {}).get("images") or [])

        prompt_payload = {"text": prompt, "images": attachments} if attachments else prompt

        fallback_message = (
            "I'm having connection issues right now. Please check your API key and network connection."
        )

        try:
            stream = self.llm.stream_chat_for_agent("lead_companion_agent", prompt_payload)
            chunks: List[str] = []
            for chunk in stream:
                if chunk:
                    chunks.append(chunk)
        except LLMServiceError as exc:
            logger.error("Streaming chitchat reply failed after retries: %s", exc, exc_info=True)
            return fallback_message
        except Exception as exc:
            logger.error("Error while gathering chitchat stream: %s", exc, exc_info=True)
            raise RuntimeError("Failed to gather conversational reply stream.") from exc

        reply_text = self.code_sanitizer.strip_code_fences("".join(chunks))
        if not reply_text:
            logger.warning("Chitchat model returned an empty reply.")
            raise RuntimeError("Chitchat model returned empty reply")
        return reply_text

    def execute_discuss(self, action: Action, ctx: ProjectContext) -> str:
        """Ask the user clarifying questions for a planned action."""
        clarifying_questions = normalize_string_list(action.get_param("questions", []))
        unclear_aspects = normalize_string_list(action.get_param("unclear_aspects", []))
        original_action = action.get_param("original_action")
        understood_summary = summarize_original_action(original_action)

        if not clarifying_questions:
            logger.warning("DISCUSS action missing clarifying questions; using generic clarification prompt.")
            return (
                "I want to make sure I build exactly what you have in mind. "
                "Could you share a bit more detail about the specifics you need so I can continue confidently?"
            )

        latest_user_message = next(
            (
                (msg or {}).get("content")
                for msg in reversed(ctx.conversation_history or [])
                if (msg or {}).get("role") == "user"
            ),
            None,
        )

        prompt_lines = [
            "You are Aura, a collaborative senior software engineer.",
            "Compose a short, friendly message asking for clarification before you proceed with the work.",
        ]
        if latest_user_message:
            prompt_lines.append(f"Latest user message: {latest_user_message.strip()}")
        if understood_summary:
            prompt_lines.append(f"What you believe the user wants: {understood_summary}")
        if unclear_aspects:
            prompt_lines.append("Still unclear details: " + "; ".join(unclear_aspects))
        prompt_lines.append("Ask the developer the following clarifying questions in bullet form:")
        for question in clarifying_questions:
            prompt_lines.append(f"- {question}")
        prompt_lines.append(
            "Briefly explain why these answers matter so they know you're being thoughtful, then reassure them you'll continue once you have clarity."
        )
        prompt_lines.append(
            "Tone guidelines: warm, collaborative, confident; no code fences; keep it concise but personable."
        )
        prompt_lines.append("Return only the final message you would send to the developer.")

        prompt_text = "\n".join(prompt_lines)

        try:
            response = self.llm.run_for_agent("lead_companion_agent", prompt_text)
        except LLMServiceError as exc:
            logger.error(
                "DISCUSS response generation failed after retries: %s",
                exc,
                exc_info=True,
            )
            return build_discuss_fallback_response(
                clarifying_questions,
                unclear_aspects,
                understood_summary,
            )
        except Exception as exc:
            logger.error("Failed formatting DISCUSS response: %s", exc, exc_info=True)
            return build_discuss_fallback_response(
                clarifying_questions,
                unclear_aspects,
                understood_summary,
            )

        formatted = self.code_sanitizer.strip_code_fences(response or "").strip()
        if not formatted:
            logger.warning("Empty DISCUSS response from companion agent; using fallback message.")
            return build_discuss_fallback_response(
                clarifying_questions,
                unclear_aspects,
                understood_summary,
            )

        return formatted
