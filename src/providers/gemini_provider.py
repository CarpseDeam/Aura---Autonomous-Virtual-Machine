"""Google Gemini LLM Provider for Aura."""
import logging
import os
from typing import Any, Dict, Generator, List, Optional

from src.aura.services.user_settings_manager import load_user_settings


logger = logging.getLogger(__name__)


class GeminiProvider:
    """
    Provider for Google Gemini models.

    Environment variables take precedence - standard security practice.
    Checks GEMINI_API_KEY and GOOGLE_API_KEY before user_settings.json.
    """

    def __init__(self, image_storage: Optional[Any] = None) -> None:
        """
        Initialize Gemini provider with API key from environment or settings.

        Args:
            image_storage: Optional image storage service for vision capabilities.
        """
        self.image_storage = image_storage
        self.provider_name = "Google"
        self.api_key = self._load_api_key()

        # Initialize Gemini client if API key is available
        if self.api_key:
            logger.info("GeminiProvider initialized with API key from %s",
                       self._get_api_key_source())
            self._init_client()
        else:
            logger.warning(
                "GeminiProvider initialized without API key. "
                "Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable, "
                "or configure api_keys.google in user_settings.json"
            )
            self.client = None

    def _load_api_key(self) -> Optional[str]:
        """
        Load API key from environment variables or user settings.

        Environment variables checked:
        1. GEMINI_API_KEY
        2. GOOGLE_API_KEY
        3. user_settings.json api_keys.google
        4. user_settings.json api_keys.gemini

        Returns:
            API key string if found, None otherwise.
        """
        # Check environment variables first (standard practice)
        logger.debug("Checking GEMINI_API_KEY environment variable...")
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            logger.info("Found API key in GEMINI_API_KEY environment variable")
            return api_key.strip()

        logger.debug("Checking GOOGLE_API_KEY environment variable...")
        api_key = os.getenv("GOOGLE_API_KEY")
        if api_key:
            logger.info("Found API key in GOOGLE_API_KEY environment variable")
            return api_key.strip()

        # Fall back to user_settings.json
        logger.debug("No API key in environment, checking user_settings.json...")
        try:
            user_settings = load_user_settings()
            api_keys = user_settings.get("api_keys", {})

            # Check both 'google' and 'gemini' keys for compatibility
            api_key = api_keys.get("google") or api_keys.get("gemini")
            if api_key and isinstance(api_key, str) and api_key.strip():
                logger.info("Found API key in user_settings.json")
                return api_key.strip()
        except Exception as exc:
            logger.debug("Failed to load API key from user_settings.json: %s", exc)

        logger.debug("No API key found in environment or user_settings.json")
        return None

    def _get_api_key_source(self) -> str:
        """Get human-readable description of where API key was loaded from."""
        if os.getenv("GEMINI_API_KEY"):
            return "GEMINI_API_KEY environment variable"
        if os.getenv("GOOGLE_API_KEY"):
            return "GOOGLE_API_KEY environment variable"
        return "user_settings.json"

    def _init_client(self) -> None:
        """Initialize the Google Generative AI client."""
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            self.client = genai
            logger.debug("Google Generative AI client configured successfully")
        except ImportError as exc:
            logger.error(
                "Failed to import google.generativeai. "
                "Install with: pip install google-generativeai"
            )
            raise ImportError(
                "google-generativeai package not installed. "
                "Install with: pip install google-generativeai"
            ) from exc
        except Exception as exc:
            logger.error("Failed to initialize Gemini client: %s", exc)
            raise

    def get_available_models(self) -> List[str]:
        """
        Return list of available Gemini model names.

        Returns:
            List of model identifiers.
        """
        if not self.client:
            logger.warning("Cannot list models: Gemini client not initialized")
            return []

        # Common Gemini models as of 2025
        models = [
            "gemini-2.5-pro",
            "gemini-2.0-flash-exp",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-pro",
            "gemini-pro-vision",
        ]

        logger.debug("Available Gemini models: %s", models)
        return models

    def stream_chat(
        self,
        model_name: str,
        prompt: Any,
        config: Dict[str, Any]
    ) -> Generator[str, None, None]:
        """
        Stream chat responses from Gemini.

        Args:
            model_name: The Gemini model identifier.
            prompt: The prompt to send (string or structured format).
            config: Configuration dict with temperature, top_p, etc.

        Yields:
            Response chunks as strings.

        Raises:
            RuntimeError: If client is not initialized.
        """
        if not self.client:
            raise RuntimeError(
                "Gemini client not initialized. Check API key configuration."
            )

        try:
            # Extract generation config
            generation_config = {
                "temperature": config.get("temperature", 0.7),
                "top_p": config.get("top_p", 0.95),
                "max_output_tokens": config.get("max_tokens", 8192),
            }

            # Create model instance
            model = self.client.GenerativeModel(
                model_name=model_name,
                generation_config=generation_config
            )

            # Generate content with streaming
            response = model.generate_content(prompt, stream=True)

            for chunk in response:
                if hasattr(chunk, 'text'):
                    yield chunk.text

        except Exception as exc:
            logger.error("Gemini streaming failed for model '%s': %s", model_name, exc)
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
            model_name: The Gemini model identifier.
            messages: List of message dicts with 'role' and 'content'.
            config: Configuration dict with temperature, top_p, etc.

        Yields:
            Response chunks as strings.
        """
        if not self.client:
            raise RuntimeError(
                "Gemini client not initialized. Check API key configuration."
            )

        try:
            # Extract generation config
            generation_config = {
                "temperature": config.get("temperature", 0.7),
                "top_p": config.get("top_p", 0.95),
                "max_output_tokens": config.get("max_tokens", 8192),
            }

            # Convert messages to Gemini format
            system_instruction = None
            history = []
            user_message = ""

            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")

                if role == "system":
                    system_instruction = content
                elif role == "user":
                    user_message = content
                elif role == "assistant":
                    # Add to history for multi-turn conversations
                    history.append({"role": "model", "parts": [content]})

            # Create model with system instruction if present
            model_kwargs = {"model_name": model_name, "generation_config": generation_config}
            if system_instruction:
                model_kwargs["system_instruction"] = system_instruction

            model = self.client.GenerativeModel(**model_kwargs)

            # Start chat with history
            chat = model.start_chat(history=history)

            # Send user message and stream response
            response = chat.send_message(user_message, stream=True)

            for chunk in response:
                if hasattr(chunk, 'text'):
                    yield chunk.text

        except Exception as exc:
            logger.error(
                "Gemini structured streaming failed for model '%s': %s",
                model_name,
                exc
            )
            raise
