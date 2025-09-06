import os
import logging
import threading
import json
import copy
from typing import Dict

# Application-specific imports
from src.aura.app.event_bus import EventBus
from src.aura.config import AGENT_CONFIG, SETTINGS_FILE
from src.aura.models.events import Event
from src.aura.prompts.prompt_manager import PromptManager

from src.aura.services.task_management_service import TaskManagementService
from src.providers.gemini_provider import GeminiProvider
from src.providers.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class LLMService:
    """
    A dispatcher service that routes LLM requests to the appropriate provider
    based on user-configured agent settings.
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

        self.agent_config = {}
        self.providers = {}
        self.model_to_provider_map = {}

        self._load_providers()
        self._load_agent_configurations()
        self._register_event_handlers()

    def _load_providers(self):
        """Initializes providers and builds a map of models to their provider."""
        logger.info("Loading LLM providers...")
        provider_instances = [GeminiProvider(), OllamaProvider()]

        for provider in provider_instances:
            self.providers[provider.provider_name] = provider
            models = provider.get_available_models()
            for model_name in models:
                self.model_to_provider_map[model_name] = provider.provider_name
        logger.info(f"Loaded {len(self.providers)} providers managing {len(self.model_to_provider_map)} models.")

    def _load_agent_configurations(self):
        """Loads agent configurations, merging user settings over defaults."""
        config = copy.deepcopy(AGENT_CONFIG)
        logger.info("Loading default agent configurations.")

        if SETTINGS_FILE.exists():
            try:
                logger.info(f"Found user settings file at {SETTINGS_FILE}, merging...")
                with open(SETTINGS_FILE, 'r') as f:
                    user_config = json.load(f)

                for agent_name, user_settings in user_config.items():
                    if agent_name in config:
                        if user_settings.get("model"):
                            config[agent_name].update(user_settings)
                    else:
                        config[agent_name] = user_settings
            except (IOError, json.JSONDecodeError) as e:
                logger.error(f"Failed to load or parse user settings: {e}. Using defaults.")

        self.agent_config = config
        logger.info("Final agent configurations loaded.")

    def _register_event_handlers(self):
        """Subscribes the service to relevant events."""
        self.event_bus.subscribe("SEND_USER_MESSAGE", self.handle_user_message)
        self.event_bus.subscribe("DISPATCH_TASK", self.handle_dispatch_task)
        self.event_bus.subscribe("RELOAD_LLM_CONFIG", lambda event: self._load_agent_configurations())
        self.event_bus.subscribe("REQUEST_AVAILABLE_MODELS", self._handle_request_available_models)

    def _get_provider_for_agent(self, agent_name: str) -> tuple[any, str, Dict]:
        """Determines the correct provider and model for a given agent."""
        config = self.agent_config.get(agent_name)
        if not config: return None, None, None

        model_name = config.get("model")
        if not model_name: return None, None, config

        provider_name = self.model_to_provider_map.get(model_name)

        if not provider_name:
            # If model not in map, try to infer provider from model name.
            for p_name in self.providers:
                if model_name.lower().startswith(p_name.lower()):
                    provider_name = p_name
                    break

            # Fallback for models that don't follow the prefix convention (e.g., gemini).
            if not provider_name:
                if 'gemini' in model_name:
                    provider_name = 'Google'

        provider = self.providers.get(provider_name)
        return provider, model_name, config

    def handle_user_message(self, event: Event):
        """Handles user messages by routing them through the lead companion's cognitive loop."""
        prompt = event.payload.get("text")
        if not prompt: return

        thread = threading.Thread(target=self._handle_conversation, args=(prompt,))
        thread.start()

    def handle_dispatch_task(self, event: Event):
        """Handles dispatched tasks by routing them to the engineer agent."""
        task = self.task_management_service.get_task_by_id(event.payload.get("task_id"))
        if not task:
            self._handle_error(f"Could not find task with ID {event.payload.get('task_id')}")
            return

        logger.info(f"Engineer Agent activated for task: '{task.description}'")
        file_path = f"generated/{task.description.split('`')[1]}" if '`' in task.description else "generated/file.py"

        prompt = self.prompt_мanager.render(
            "generate_code.jinja2",
            task_description=task.description,
            file_path=file_path
        )
        if not prompt:
            self._handle_error("Failed to render the engineer prompt.")
            return

        thread = threading.Thread(target=self._stream_generation, args=(prompt, "engineer_agent"))
        thread.start()

    def _handle_conversation(self, user_prompt: str):
        """
        Manages the main conversational flow with the Lead Companion agent.
        This method embodies the "Cognitive Loop".
        """
        provider, model_name, config = self._get_provider_for_agent("lead_companion_agent")
        if not provider or not model_name:
            self._handle_error("Lead Companion agent not configured correctly.")
            return

        try:
            prompt = self.prompt_manager.render("lead_companion_master.jinja2", user_prompt=user_prompt)
            response_stream = provider.stream_chat(model_name, prompt, config)
            full_response = "".join(list(response_stream))

            if "tool_name" in full_response:
                self._handle_tool_call(full_response)
            else:
                self.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": full_response}))
                self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED"))

        except Exception as e:
            logger.error(f"Error in conversation handler: {e}", exc_info=True)
            self._handle_error("A critical error occurred in the conversation handler.")

    def _handle_tool_call(self, tool_call_json: str):
        """Parses and executes a tool call from the Lead Companion."""
        try:
            data = json.loads(tool_call_json)
            tool_name = data.get("tool_name")
            arguments = data.get("arguments", {})
            logger.info(f"Lead Companion requested tool: '{tool_name}' with args: {arguments}")

            if tool_name == "consult_architect":
                self._execute_architect_consultation(arguments.get("user_request"))
            else:
                self._handle_error(f"Received unknown tool call: {tool_name}")

        except json.JSONDecodeError:
            logger.error(f"Failed to decode tool call JSON: {tool_call_json}")
            self._stream_generation(
                f"My apologies, I had a formatting error in my thinking process. Could you please rephrase your request? Original request: {tool_call_json}",
                "lead_companion_agent"
            )

    def _execute_architect_consultation(self, user_request: str):
        """Invokes the Architect agent to generate a project plan."""
        provider, model_name, config = self._get_provider_for_agent("architect_agent")
        if not provider or not model_name:
            self._handle_error("Architect agent is not configured.")
            return

        try:
            prompt = self.prompt_manager.render("plan_project.jinja2", user_request=user_request)
            response_stream = provider.stream_chat(model_name, prompt, config)
            full_response = "".join(list(response_stream))

            plan_data = json.loads(full_response.strip().replace("```json", "").replace("```", ""))
            tasks = plan_data.get("plan", [])

            if not tasks:
                self._handle_error("The Architect returned an empty plan.")
                return

            for task in tasks:
                self.event_bus.dispatch(Event("ADD_TASK", {"description": task["description"]}))

            confirmation_prompt = "Excellent! I've drafted a plan and added it to Mission Control. Take a look and let me know when you're ready to start building. You can dispatch them all at once or we can refine the plan further."
            self._stream_generation(confirmation_prompt, "lead_companion_agent")

        except Exception as e:
            logger.error(f"Error during architect consultation: {e}", exc_info=True)
            self._handle_error("The Architect specialist encountered an error while drafting the plan.")

    def _stream_generation(self, prompt: str, agent_name: str):
        """The main generation method that dispatches a request to the correct provider."""
        provider, model_name, config = self._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            self._handle_error(f"Agent '{agent_name}' is not configured with a valid model.")
            return

        try:
            logger.info(
                f"Dispatching to '{provider.provider_name}' for agent '{agent_name}' with model '{model_name}'.")
            response_stream = provider.stream_chat(model_name, prompt, config)

            for chunk in response_stream:
                if chunk.startswith("ERROR:"):
                    self._handle_error(chunk)
                    break
                self.event_bus.dispatch(Event("MODEL_CHUNK_RECEIVED", payload={"chunk": chunk}))

        except Exception as e:
            logger.error(f"Error dispatching to provider {provider.provider_name}: {e}", exc_info=True)
            self._handle_error(f"An error occurred with the {provider.provider_name} provider.")
        finally:
            self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED"))

    def _handle_error(self, message: str):
        """Dispatches a model error event."""
        logger.error(message)
        self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": message}))

    def _handle_request_available_models(self, event: Event):
        """Handles the request for available models."""
        models_by_provider = {}
        for provider_name, provider in self.providers.items():
            models_by_provider[provider_name] = provider.get_available_models()

        self.event_bus.dispatch(Event(
            event_type="AVAILABLE_MODELS_RECEIVED",
            payload={"models": models_by_provider}
        ))
