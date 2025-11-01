"""Shared streaming helpers for code generation."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.services.llm_service import LLMService

from .code_sanitizer import CodeSanitizer
from .prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


def stream_and_finalize(
    *,
    llm: LLMService,
    event_bus: EventBus,
    prompt_builder: PromptBuilder,
    code_sanitizer: CodeSanitizer,
    prompt: str,
    agent_name: str,
    file_path: str,
    validate_with_spec: Optional[Dict[str, Any]],
    prototype_override: Optional[bool],
    on_error: Callable[[str], None],
) -> None:
    """Stream generated code from the LLM and route outputs through the event bus."""

    def run() -> None:
        try:
            messages = prompt_builder.build_generation_messages(prompt, prototype_override=prototype_override)
            logger.info("Streaming generation for %s via %s", file_path, agent_name)
            _dispatch_progress(event_bus, f"Generating {file_path}...", "SYSTEM")

            stream = llm.stream_structured_for_agent(agent_name, messages)
            full_parts: List[str] = [chunk for chunk in stream if chunk]
            full_text = "".join(full_parts)
            if full_text.startswith("ERROR:"):
                on_error(full_text)
                return

            code = code_sanitizer.sanitize_code(full_text)
            line_count = len(code.splitlines()) if code else 0
            _dispatch_progress(event_bus, f"Drafted {file_path} ({line_count} lines)", "SUCCESS")

            if validate_with_spec:
                _dispatch_progress(event_bus, f"Validating {file_path}...", "SYSTEM")
                payload = {
                    "task_id": None,
                    "file_path": file_path,
                    "spec": validate_with_spec,
                    "generated_code": code,
                }
                event_bus.dispatch(Event(event_type="VALIDATE_CODE", payload=payload))
            else:
                event_bus.dispatch(Event(
                    event_type="CODE_GENERATED",
                    payload={"file_path": file_path, "code": code},
                ))
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Generation error for %s: %s", file_path, exc, exc_info=True)
            on_error(f"A critical error occurred while generating code for {file_path}.")

    threading.Thread(target=run, daemon=True).start()


def _dispatch_progress(event_bus: EventBus, message: str, category: str) -> None:
    try:
        payload = {"message": message, "category": category}
        event_bus.dispatch(Event(event_type="GENERATION_PROGRESS", payload=payload))
    except Exception:
        logger.debug("Failed to dispatch generation progress event for '%s'.", message, exc_info=True)

