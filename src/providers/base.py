from abc import ABC, abstractmethod
from typing import List, Dict, Any, Generator


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

    @abstractmethod
    def stream_chat(
        self,
        model_name: str,
        prompt: str,
        config: Dict[str, Any]
    ) -> Generator[str, None, None]:
        """
        Streams a chat response from the provider's API.

        Args:
            model_name: The specific model to use for the chat.
            prompt: The user's input prompt.
            config: A dictionary containing generation parameters like 'temperature' and 'top_p'.

        Yields:
            A stream of strings, where each string is a chunk of the response.
        """
        pass