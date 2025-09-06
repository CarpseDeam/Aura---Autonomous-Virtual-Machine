import logging
from typing import List

from src.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    """
    Provides a curated list of available models for Ollama.
    """
    @property
    def provider_name(self) -> str:
        """Returns the name of the provider."""
        return "Ollama"

    def get_available_models(self) -> List[str]:
        """
        Returns a hardcoded list of common Ollama models.
        In the future, this could make an API call to the local Ollama server.
        """
        return [
            "llama3",
            "codellama",
            "mistral",
            "phi3",
            "gemma",
        ]