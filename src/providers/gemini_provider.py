import logging
import os
from typing import Any, Dict, Generator, List, Optional

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

    @staticmethod
    def _build_parts(text: Optional[str], images: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """
        Combine text and optional images into Gemini content parts.
        """
        parts: List[Dict[str, Any]] = []
        if text:
            parts.append({"text": text})
        for image in images or []:
            data = image.get("data")
            if not data:
                continue
            mime_type = image.get("mime_type") or "image/png"
            parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": data,
                }
            })
        if not parts:
            parts.append({"text": ""})
        return parts

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
                "gemini-1.5-pro-latest",
                "gemini-1.5-flash-latest",
                "gemini-1.0-pro",
            ]

    def stream_chat(
        self,
        model_name: str,
        prompt: Any,
        config: Dict[str, Any]
    ) -> Generator[str, None, None]:
        """
        Streams a chat response from the Gemini API character by character.
        """
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

            if isinstance(prompt, dict):
                text_prompt = prompt.get("text")
                image_payloads = prompt.get("images") or []
                parts = self._build_parts(text_prompt, image_payloads)
                response_stream = model.generate_content(
                    [{"role": "user", "parts": parts}],
                    stream=True
                )
            else:
                response_stream = model.generate_content(prompt, stream=True)

            for chunk in response_stream:
                try:
                    for char in chunk.text:
                        yield char
                except ValueError:
                    # Ignore empty chunks, which can happen at the end of a stream.
                    pass

        except Exception as e:
            logger.error(f"Error during Gemini stream: {e}", exc_info=True)
            yield f"ERROR: An error occurred with the Gemini API: {e}"

    def stream_chat_structured(
        self,
        model_name: str,
        messages: List[Dict[str, Any]],
        config: Dict[str, Any]
    ) -> Generator[str, None, None]:
        """
        Streams a chat response using structured messages with the Gemini API character by character.
        """
        if not self.configured:
            yield "ERROR: GeminiProvider is not configured. Please check your API key."
            return

        logger.info(f"Streaming structured chat from Gemini model: {model_name}")
        try:
            generation_config = genai.GenerationConfig(
                temperature=config.get("temperature", 0.7),
                top_p=config.get("top_p", 1.0)
            )
            
            # Extract system instruction and convert messages to Gemini format
            system_instruction = None
            chat_history: List[Dict[str, Any]] = []
            
            for message in messages:
                role = message.get('role')
                content = message.get('content')
                images = message.get('images') or []

                if role == 'system' and system_instruction is None:
                    system_instruction = content
                    continue

                mapped_role = 'user' if role == 'user' else 'model'
                parts = self._build_parts(content, images)
                chat_history.append({'role': mapped_role, 'parts': parts})
            
            # Create model with system instruction
            model = genai.GenerativeModel(
                model_name, 
                generation_config=generation_config,
                system_instruction=system_instruction
            )
            
            response_stream = None
            if chat_history and chat_history[-1]['role'] == 'user':
                last_message = chat_history[-1]
                prior_history = chat_history[:-1]
                if prior_history:
                    chat = model.start_chat(history=prior_history)
                    response_stream = chat.send_message(last_message['parts'], stream=True)
                else:
                    response_stream = model.generate_content(
                        [{"role": "user", "parts": last_message['parts']}],
                        stream=True
                    )
            else:
                # Fallback: send entire history as a single request
                response_stream = model.generate_content(chat_history or [{"role": "user", "parts": self._build_parts(None, None)}], stream=True)

            for chunk in response_stream:
                try:
                    for char in chunk.text:
                        yield char
                except ValueError:
                    # Ignore empty chunks, which can happen at the end of a stream.
                    pass

        except Exception as e:
            logger.error(f"Error during Gemini structured stream: {e}", exc_info=True)
            yield f"ERROR: An error occurred with the Gemini structured API: {e}"
