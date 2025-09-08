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
                "gemini-1.5-pro-latest",
                "gemini-1.5-flash-latest",
                "gemini-1.0-pro",
            ]

    def stream_chat(
        self,
        model_name: str,
        prompt: str,
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
        messages: List[Dict[str, str]],
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
            chat_history = []
            
            for message in messages:
                if message['role'] == 'system':
                    system_instruction = message['content']
                elif message['role'] == 'user':
                    chat_history.append({'role': 'user', 'parts': [message['content']]})
                elif message['role'] == 'assistant':
                    chat_history.append({'role': 'model', 'parts': [message['content']]})
            
            # Create model with system instruction
            model = genai.GenerativeModel(
                model_name, 
                generation_config=generation_config,
                system_instruction=system_instruction
            )
            
            # Start chat with history (excluding the last user message)
            user_messages = [msg for msg in chat_history if msg['role'] == 'user']
            if len(user_messages) > 1:
                # If there's conversation history, use it
                chat = model.start_chat(history=chat_history[:-1])
                response_stream = chat.send_message(chat_history[-1]['parts'][0], stream=True)
            else:
                # First message - no history
                if chat_history:
                    response_stream = model.generate_content(chat_history[-1]['parts'][0], stream=True)
                else:
                    # Fallback to system instruction only
                    response_stream = model.generate_content("Please respond", stream=True)

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