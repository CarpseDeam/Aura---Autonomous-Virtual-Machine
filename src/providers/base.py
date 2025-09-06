from abc import ABC, abstractmethod
from typing import List


class LLMProvider(ABC):
    """
    Abstract Base Class for all Large Language Model (LLM) providers.
    This defines the contract that all concrete provider implementations must follow.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """The official name of the provider (e.g., 'Google', 'Ollama')."""
        pass

    @abstractmethod
    def get_available_models(self) -> List[str]:
        """
        Returns a list of available model names for this provider.

        Returns:
            A list of strings, where each string is a model identifier.
        """
        pass