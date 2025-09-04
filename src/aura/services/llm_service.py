import os
import logging
import threading
import json
import google.generativeai as genai
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.models.intent import Intent

logger = logging.getLogger(__name__)


class LLMService:
    """
    Handles all interactions with the language model providers (e.g., Gemini).
    This service now includes the "Cognitive Router" to detect user intent.
    """

    def __init__(self, event_bus: EventBus, prompt_manager: PromptManager):
        """Initializes the LLMService."""
        self.event_bus = event_bus
        self.prompt_manager = prompt_manager
        self.model = None
        self._configure_client()
        self._register_event_handlers()

    def _configure_client(self):
        """Configures the Gemini client using the API key from environment variables."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "YOUR_API_KEY_HERE":
            logger.critical("GEMINI_API_KEY not found or not set. LLMService will be disabled.")
            return
        try:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-1.5-pro-latest')
            logger.info("Gemini client configured successfully.")
        except Exception as e:
            logger.critical(f"Failed to configure Gemini client: {e}", exc_info=True)
            self.model = None

    def _register_event_handlers(self):
        """Subscribes the service to relevant events from the event bus."""
        self.event_bus.subscribe("SEND_USER_MESSAGE", self.handle_user_message)
        self.event_bus.subscribe("DISPATCH_TASK", self.handle_dispatch_task)

    def handle_user_message(self, event: Event):
        """
        Handles the SEND_USER_MESSAGE event by starting a new thread to process
        the user's request, starting with intent detection.
        """
        prompt = event.payload.get("text")
        if not self.model or not prompt:
            self._handle_error("LLM not configured or empty prompt.")
            return

        thread = threading.Thread(target=self._cognitive_router, args=(prompt,))
        thread.start()

    def handle_dispatch_task(self, event: Event):
        """
        Handles the DISPATCH_TASK event, preparing for the Engineer Agent's work.
        """
        task_id = event.payload.get("task_id")
        if not task_id:
            return

        logger.info(f"Received DISPATCH_TASK for task ID: {task_id}. Triggering Engineer Agent.")
        # Placeholder for Engineer Agent logic
        # For now, we'll just send a confirmation message to the main chat
        dispatch_prompt = f"Acknowledge that the user has dispatched a task with ID {task_id} and that the engineering team is now working on it."

        thread = threading.Thread(target=self._stream_generation, args=(dispatch_prompt,))
        thread.start()

    def _cognitive_router(self, user_prompt: str):
        """
        First step: Detect the user's intent.
        Second step: Route to the appropriate handler based on intent.
        """
        try:
            intent = self._detect_intent(user_prompt)
            logger.info(f"Detected intent: {intent.value}")

            if intent == Intent.PLANNING_SESSION:
                self._handle_planning_session(user_prompt)
            else:  # Default to chitchat for CHITCHAT or UNKNOWN
                self._stream_generation(user_prompt)

        except Exception as e:
            logger.error(f"Error in cognitive router: {e}", exc_info=True)
            self._handle_error(f"An error occurred during intent processing: {e}")

    def _detect_intent(self, user_prompt: str) -> Intent:
        """
        Calls the LLM with a specific prompt to classify the user's intent.
        This is a non-streaming, fast call.
        """
        try:
            prompt = self.prompt_manager.render(
                "detect_intent.jinja2",
                user_prompt=user_prompt
            )
            if not prompt:
                return Intent.UNKNOWN

            logger.info("Requesting intent detection from LLM...")
            response = self.model.generate_content(prompt)

            response_text = response.text.strip().replace("```json", "").replace("```", "")

            data = json.loads(response_text)
            intent_str = data.get("intent", "UNKNOWN").upper()

            return Intent(intent_str)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Could not parse intent from LLM response: {response.text}. Error: {e}")
            return Intent.UNKNOWN
        except Exception as e:
            logger.error(f"Error during intent detection: {e}", exc_info=True)
            return Intent.UNKNOWN

    def _handle_planning_session(self, user_prompt: str):
        """
        Handles the PLANNING_SESSION intent. It adds a task to Mission Control
        and sends a confirmatory message.
        """
        task_description = f"Plan and design: {user_prompt[:100]}..."
        self.event_bus.dispatch(Event(
            event_type="ADD_TASK",
            payload={"description": task_description}
        ))

        confirm_prompt = f"Acknowledge that you are ready to start a planning session based on the user's request: '{user_prompt}'"
        self._stream_generation(confirm_prompt)

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
            self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED"))

    def _handle_error(self, message: str):
        """Dispatches a model error event."""
        logger.error(message)
        error_event = Event(event_type="MODEL_ERROR", payload={"message": message})
        self.event_bus.dispatch(error_event)