import os
import logging
import threading
import google.generativeai as genai
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event

logger = logging.getLogger(__name__)


class LLMService:
    """
    Handles all interactions with the language model providers (e.g., Gemini).
    """

    def __init__(self, event_bus: EventBus):
        """Initializes the LLMService."""
        self.event_bus = event_bus
        self.model = None
        self._configure_client()
        self.event_bus.subscribe("SEND_USER_MESSAGE", self.handle_user_message)

    def _configure_client(self):
        """Configures the Gemini client using the API key from environment variables."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "YOUR_API_KEY_HERE":
            logger.critical("GEMINI_API_KEY not found or not set in .env file. LLMService will be disabled.")
            return
        try:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-1.5-pro-latest')
            logger.info("Gemini client configured successfully.")
        except Exception as e:
            logger.critical(f"Failed to configure Gemini client: {e}", exc_info=True)
            self.model = None

    def handle_user_message(self, event: Event):
        """
        Handles the SEND_USER_MESSAGE event by starting a new thread
        to stream the response from the LLM.
        """
        prompt = event.payload.get("text")
        if not self.model or not prompt:
            self._handle_error("LLM not configured or empty prompt.")
            return

        # Run the streaming generation in a separate thread to avoid blocking the UI
        thread = threading.Thread(target=self._stream_generation, args=(prompt,))
        thread.start()

    def _stream_generation(self, prompt: str):
        """Generates content from the LLM and streams the response."""
        try:
            logger.info(f"Sending prompt to Gemini for streaming: '{prompt[:80]}...'")
            response_stream = self.model.generate_content(prompt, stream=True)

            for chunk in response_stream:
                chunk_event = Event(
                    event_type="MODEL_CHUNK_RECEIVED",
                    payload={"chunk": chunk.text}
                )
                self.event_bus.dispatch(chunk_event)

        except Exception as e:
            logger.error(f"Error communicating with Gemini API: {e}", exc_info=True)
            self._handle_error(f"An error occurred with the Gemini API: {e}")
        finally:
            # Signal that the stream has ended
            self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED"))

    def _handle_error(self, message: str):
        """Dispatches a model error event."""
        logger.error(message)
        error_event = Event(event_type="MODEL_ERROR", payload={"message": message})
        self.event_bus.dispatch(error_event)