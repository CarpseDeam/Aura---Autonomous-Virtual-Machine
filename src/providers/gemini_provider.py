import logging
import os
from typing import List, Dict, Any, Generator

import google.generativeai as genai

from src.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """
    Provides model lists and streaming chat for Google Gemini models.
    """

    def __init__(self):
        """Initializes the GeminiProvider and configures the API key."""
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key or self.api_key == "YOUR_API_KEY_HERE":
            logger.warning("Gemini API key not found. GeminiProvider will be disabled.")
            self.configured = False
        else:
            try:
                genai.configure(api_key=self.api_key)
                self.configured = True
                logger.info("GeminiProvider configured successfully.")
            except Exception as e:
                logger.error(f"Failed to configure Gemini API: {e}", exc_info=True)
                self.configured = False

    @property
    def provider_name(self) -> str:
        """Returns the name of the provider."""
        return "Google"

    def get_available_models(self) -> List[str]:
        """Returns a list of available Gemini models that support content generation."""
        if not self.configured:
            return []
        try:
            return [
                m.name.replace("models/", "")
                for m in genai.list_models()
                if "generateContent" in m.supported_generation_methods
            ]
        except Exception as e:
            logger.error(f"Could not fetch Gemini models from API: {e}")
            # Fallback to a hardcoded list on failure
            return [
                "gemini-2.5-pro",
                "gemini-2.5-flash",
            ]

    def stream_chat(
        self,
        model_name: str,
        prompt: str,
        config: Dict[str, Any]
    ) -> Generator[str, None, None]:
        """Streams a chat response from the Gemini API."""
        if not self.configured:
            yield "ERROR: GeminiProvider is not configured. Please check your API key."
            return

        logger.info(f"Streaming from Gemini model: {model_name}")
        try:
            generation_config = genai.GenerationConfig(
                temperature=config.get("temperature", 0.7),
                top_p=config.get("top_p", 1.0)
            )
            model = genai.GenerativeModel(model_name, generation_config=generation_config)
            response_stream = model.generate_content(prompt, stream=True)

            for chunk in response_stream:
                yield chunk.text

        except Exception as e:
            logger.error(f"Error during Gemini stream: {e}", exc_info=True)
            yield f"ERROR: An error occurred with the Gemini API: {e}"