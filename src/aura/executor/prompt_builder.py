"""Prompt construction utilities for Aura orchestrator."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.aura.prompts.prompt_manager import PromptManager

logger = logging.getLogger(__name__)


class PromptBuilder:
    """Build core system prompts for downstream services."""

    def __init__(self, prompts: PromptManager) -> None:
        self.prompts = prompts
        self._system_prompt_cache: Optional[str] = None

    def get_system_prompt(self) -> str:
        """Memoize and return the core system prompt."""
        if self._system_prompt_cache is None:
            prompt = self.prompts.render("system_prompt.jinja2")
            if not prompt:
                raise RuntimeError("Failed to render system prompt template.")
            self._system_prompt_cache = prompt
        return self._system_prompt_cache

    def build_generation_messages(
        self,
        prompt: str,
    ) -> List[Dict[str, str]]:
        """Construct the chat payload delivered to downstream agents."""
        system_prompt = self.get_system_prompt()
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "user", "content": prompt})
        return messages
