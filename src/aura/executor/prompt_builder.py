"""Prompt construction utilities with prototype-mode awareness."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.aura.prompts.prompt_manager import PromptManager
from src.aura.prompts.prototype_keywords import PROTOTYPE_KEYWORDS, matches_prototype_request


logger = logging.getLogger(__name__)


class PromptBuilder:
    """Build system and user prompts while tracking prototype mode state."""

    def __init__(self, prompts: PromptManager) -> None:
        self.prompts = prompts
        self._system_prompt_cache: Optional[str] = None
        self._prototype_prompt_cache: Optional[str] = None
        self._prototype_mode_requested: bool = False

    @property
    def prototype_mode_requested(self) -> bool:
        """Whether prototype mode is currently active."""
        return self._prototype_mode_requested

    def update_prototype_mode(self, user_text: str) -> None:
        """Toggle prototype mode based on the latest user request."""
        should_enable = matches_prototype_request(user_text or "")
        if should_enable and not self._prototype_mode_requested:
            logger.debug(
                "Prototype mode activated for request '%s' using keywords: %s",
                user_text,
                PROTOTYPE_KEYWORDS,
            )
        elif not should_enable and self._prototype_mode_requested:
            logger.debug("Prototype mode disabled for request '%s'.", user_text)
        self._prototype_mode_requested = should_enable

    def get_system_prompt(self) -> str:
        """Memoize and return the core system prompt."""
        if self._system_prompt_cache is None:
            prompt = self.prompts.render("system_prompt.jinja2")
            if not prompt:
                raise RuntimeError("Failed to render system prompt template.")
            self._system_prompt_cache = prompt
        return self._system_prompt_cache

    def get_prototype_prompt(self) -> Optional[str]:
        """Memoize and return the prototype-mode system prompt."""
        if self._prototype_prompt_cache is None:
            prompt = self.prompts.render("prototype_mode.jinja2")
            self._prototype_prompt_cache = prompt or ""
        return self._prototype_prompt_cache or None

    def build_generation_messages(
        self,
        prompt: str,
        *,
        prototype_override: Optional[bool] = None,
    ) -> List[Dict[str, str]]:
        """Construct the chat payload for engineer agents."""
        system_prompt = self.get_system_prompt()
        use_prototype = (
            self._prototype_mode_requested if prototype_override is None else bool(prototype_override)
        )
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

        if use_prototype:
            prototype_prompt = self.get_prototype_prompt()
            if prototype_prompt:
                messages.append({"role": "system", "content": prototype_prompt})

        messages.append({"role": "user", "content": prompt})
        return messages

