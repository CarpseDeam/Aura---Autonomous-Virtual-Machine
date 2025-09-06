import logging
from typing import List
from src.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """
    Provides a curated list of available models from Google Gemini.
    """

    @property
    def provider_name(self) -> str:
        """Returns the name of the provider."""
        return "Google"

    def get_available_models(self) -> List[str]:
        """
        Returns a hardcoded list of popular Gemini models.
        In the future, this could make an API call to discover models.
        """
        return [
            "gemini-1.5-pro-latest",
            "gemini-1.5-flash-latest",
            "gemini-1.0-pro",
        ]