import logging
import json
import requests
from typing import List, Dict, Any, Generator

from src.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    """
    Provides model lists and streaming chat for a local Ollama server.
    """
    BASE_URL = "http://localhost:11434"

    def __init__(self):
        """Initializes the OllamaProvider and checks for server connectivity."""
        self.configured = self._check_server_status()
        if self.configured:
            logger.info(f"OllamaProvider configured. Connected to server at {self.BASE_URL}")
        else:
            logger.warning(f"Ollama server not found at {self.BASE_URL}. OllamaProvider will be disabled.")

    def _check_server_status(self) -> bool:
        """Checks if the Ollama server is running."""
        try:
            response = requests.get(self.BASE_URL, timeout=2)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    @property
    def provider_name(self) -> str:
        """Returns the name of the provider."""
        return "Ollama"

    def get_available_models(self) -> List[str]:
        """Fetches the list of locally available models from the Ollama server."""
        if not self.configured:
            return []
        try:
            response = requests.get(f"{self.BASE_URL}/api/tags")
            response.raise_for_status()
            models_data = response.json().get("models", [])
            return [model["name"] for model in models_data]
        except requests.exceptions.RequestException as e:
            logger.error(f"Could not fetch Ollama models from server: {e}")
            return []

    def stream_chat(
        self,
        model_name: str,
        prompt: str,
        config: Dict[str, Any]
    ) -> Generator[str, None, None]:
        """Streams a chat response from the Ollama server API."""
        if not self.configured:
            yield "ERROR: OllamaProvider is not configured. Is the server running?"
            return

        logger.info(f"Streaming from Ollama model: {model_name}")
        url = f"{self.BASE_URL}/api/generate"
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": config.get("temperature", 0.7),
                "top_p": config.get("top_p", 1.0)
            }
        }
        try:
            response = requests.post(url, json=payload, stream=True)
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    chunk = json.loads(line)
                    yield chunk.get("response", "")
                    if chunk.get("done"):
                        break

        except requests.exceptions.RequestException as e:
            logger.error(f"Error during Ollama stream: {e}", exc_info=True)
            yield f"ERROR: Could not connect to Ollama server: {e}"