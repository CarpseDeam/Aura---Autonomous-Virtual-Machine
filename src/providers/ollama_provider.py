"""Ollama LLM Provider for Aura."""
import logging
import os
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


class OllamaProvider:
    """
    Provider for Ollama local models.

    Ollama runs locally, so typically no API key is needed.
    Checks OLLAMA_HOST environment variable for custom server location.
    """

    def __init__(self) -> None:
        """Initialize Ollama provider."""
        self.provider_name = "Ollama"
        self.host = self._get_ollama_host()

        logger.info("OllamaProvider initialized with host: %s", self.host)
        self._init_client()

    def _get_ollama_host(self) -> str:
        """
        Get Ollama server host from environment or use default.

        Returns:
            Ollama server URL.
        """
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        if os.getenv("OLLAMA_HOST"):
            logger.info("Using custom OLLAMA_HOST from environment: %s", host)
        else:
            logger.debug("Using default Ollama host: %s", host)
        return host

    def _init_client(self) -> None:
        """Initialize the Ollama client."""
        try:
            # Import ollama library
            import ollama
            self.client = ollama
            logger.debug("Ollama client initialized successfully")
        except ImportError as exc:
            logger.error(
                "Failed to import ollama. "
                "Install with: pip install ollama"
            )
            raise ImportError(
                "ollama package not installed. "
                "Install with: pip install ollama"
            ) from exc
        except Exception as exc:
            logger.error("Failed to initialize Ollama client: %s", exc)
            raise

    def get_available_models(self) -> List[str]:
        """
        Return list of available Ollama models.

        Queries the local Ollama server for installed models.

        Returns:
            List of model identifiers.
        """
        if not self.client:
            logger.warning("Cannot list models: Ollama client not initialized")
            return []

        try:
            # Query Ollama for available models
            response = self.client.list()
            models = [model['name'] for model in response.get('models', [])]

            if models:
                logger.info("Found %d Ollama models: %s", len(models), models)
            else:
                logger.warning(
                    "No Ollama models found. "
                    "Install models with: ollama pull <model-name>"
                )

            return models

        except Exception as exc:
            logger.warning(
                "Failed to list Ollama models (is Ollama running?): %s",
                exc
            )
            # Return common models as fallback
            fallback_models = [
                "llama3.2",
                "llama3.1",
                "llama2",
                "mistral",
                "mixtral",
                "codellama",
            ]
            logger.debug("Using fallback model list: %s", fallback_models)
            return fallback_models

    def stream_chat(
        self,
        model_name: str,
        prompt: Any,
        config: Dict[str, Any]
    ) -> Generator[str, None, None]:
        """
        Stream chat responses from Ollama.

        Args:
            model_name: The Ollama model identifier.
            prompt: The prompt to send (string or structured format).
            config: Configuration dict with temperature, top_p, etc.

        Yields:
            Response chunks as strings.

        Raises:
            RuntimeError: If client is not initialized.
        """
        if not self.client:
            raise RuntimeError(
                "Ollama client not initialized. Check installation."
            )

        try:
            # Extract generation config
            options = {
                "temperature": config.get("temperature", 0.7),
                "top_p": config.get("top_p", 0.95),
            }

            # Convert prompt to string if needed
            prompt_text = str(prompt) if not isinstance(prompt, str) else prompt

            # Stream from Ollama
            response = self.client.generate(
                model=model_name,
                prompt=prompt_text,
                stream=True,
                options=options
            )

            for chunk in response:
                if isinstance(chunk, dict) and 'response' in chunk:
                    yield chunk['response']

        except Exception as exc:
            logger.error("Ollama streaming failed for model '%s': %s", model_name, exc)
            raise

    def stream_chat_structured(
        self,
        model_name: str,
        messages: List[Dict[str, Any]],
        config: Dict[str, Any]
    ) -> Generator[str, None, None]:
        """
        Stream chat with structured messages (system, user, assistant).

        Args:
            model_name: The Ollama model identifier.
            messages: List of message dicts with 'role' and 'content'.
            config: Configuration dict with temperature, top_p, etc.

        Yields:
            Response chunks as strings.
        """
        if not self.client:
            raise RuntimeError(
                "Ollama client not initialized. Check installation."
            )

        try:
            # Extract generation config
            options = {
                "temperature": config.get("temperature", 0.7),
                "top_p": config.get("top_p", 0.95),
            }

            # Use Ollama's chat API for structured messages
            response = self.client.chat(
                model=model_name,
                messages=messages,
                stream=True,
                options=options
            )

            for chunk in response:
                if isinstance(chunk, dict):
                    # Extract message content
                    message = chunk.get('message', {})
                    content = message.get('content', '')
                    if content:
                        yield content

        except Exception as exc:
            logger.error(
                "Ollama structured streaming failed for model '%s': %s",
                model_name,
                exc
            )
            raise
