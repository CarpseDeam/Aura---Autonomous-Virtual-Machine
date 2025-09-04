import os
import logging
import threading
import json
import google.generativeai as genai
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.task_management_service import TaskManagementService
from src.aura.models.intent import Intent

logger = logging.getLogger(__name__)


class LLMService:
    """
    Handles all interactions with the language model providers (e.g., Gemini).
    This service now includes the "Cognitive Router" to detect user intent.
    """

    def __init__(
        self,
        event_bus: EventBus,
        prompt_manager: PromptManager,
        task_management_service: TaskManagementService
    ):
        """Initializes the LLMService."""
        self.event_bus = event_bus
        self.prompt_manager = prompt_manager
        self.task_management_service = task_management_service
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
        Handles the DISPATCH_TASK event, activating the Engineer Agent.
        """
        task_id = event.payload.get("task_id")
        if not task_id:
            logger.warning("DISPATCH_TASK event received with no task_id.")
            return

        task = self.task_management_service.get_task_by_id(task_id)
        if not task:
            self._handle_error(f"Could not find task with ID {task_id} to dispatch.")
            return

        logger.info(f"Engineer Agent activated for task: '{task.description}'")
        # For now, we assume the task description implies the filename.
        # This will become more robust later.
        file_path = f"generated/{task.description.split('`')[1]}" if '`' in task.description else "generated/file.py"

        engineer_prompt = self.prompt_manager.render(
            "generate_code.jinja2",
            task_description=task.description,
            file_path=file_path
        )

        if not engineer_prompt:
            self._handle_error("Failed to render the engineer prompt.")
            return

        # Run code generation in a thread to keep the UI responsive.
        thread = threading.Thread(target=self._generate_code, args=(engineer_prompt, file_path))
        thread.start()

    def _generate_code(self, prompt: str, file_path: str):
        """
        The Engineer Agent's core logic for generating code.
        """
        try:
            logger.info("Sending prompt to Engineer Agent for code generation...")
            # This is a non-streaming call because we want the full code at once.
            response = self.model.generate_content(prompt)
            generated_code = response.text

            # Clean up the response to get only the raw code
            if "```python" in generated_code:
                generated_code = generated_code.split("```python\n")[1].split("```")
            elif "```" in generated_code:
                 generated_code = generated_code.split("```\n").split("```")[0]

            logger.info(f"Code generation successful for file: {file_path}")
            # TODO: In the future, this event will be caught by the Code Viewer window
            # For now, we'll log it and send a success message to the main chat.
            print("--- GENERATED CODE ---")
            print(generated_code)
            print("----------------------")

            success_prompt = f"Confirm that the Engineer Agent has successfully generated the code for the task: '{file_path}'. State that it is ready for review."
            self._stream_generation(success_prompt)

        except Exception as e:
            logger.error(f"Error during code generation: {e}", exc_info=True)
            self._handle_error(f"An error occurred while generating code: {e}")

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
            else: # Default to chitchat for CHITCHAT or UNKNOWN
                self._stream_generation(user_prompt)

        except Exception as e:
            logger.error(f"Error in cognitive router: {e}", exc_info=True)
            self._handle_error(f"An error occurred during intent processing: {e}")

    def _detect_intent(self, user_prompt: str) -> Intent:
        """Calls the LLM with a specific prompt to classify the user's intent."""
        try:
            prompt = self.prompt_manager.render(
                "detect_intent.jinja2",
                user_prompt=user_prompt
            )
            if not prompt: return Intent.UNKNOWN

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
        """Handles the PLANNING_SESSION intent."""
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
                self.event_bus.dispatch(Event(
                    event_type="MODEL_CHUNK_RECEIVED",
                    payload={"chunk": chunk.text}
                ))

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