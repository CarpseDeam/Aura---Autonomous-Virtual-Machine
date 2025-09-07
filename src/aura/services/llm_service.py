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
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.task_management_service import TaskManagementService
from src.aura.services.ast_service import ASTService
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
            task_management_service: TaskManagementService,
            conversation_management_service: ConversationManagementService,
            ast_service: ASTService
    ):
        """Initializes the LLMService."""
        self.event_bus = event_bus
        self.prompt_manager = prompt_manager
        self.task_management_service = task_management_service
        self.conversation_management_service = conversation_management_service
        self.ast_service = ast_service

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
        """Handles dispatched tasks by routing them to the engineer agent with AST-powered context."""
        task_id = event.payload.get("task_id")
        if not task_id:
            self._handle_error("Dispatch event received with no task_id.")
            return

        task = self.task_management_service.get_task_by_id(task_id)
        if not task:
            self._handle_error(f"Could not find task with ID {task_id}")
            return

        logger.info(f"Engineer Agent activated for task: '{task.description}'")
        try:
            file_path = task.description.split('`')[1]
        except IndexError:
            logger.warning(f"Could not extract file path from task: '{task.description}'. Using a default.")
            file_path = "generated/default.py"

        # Get relevant context using AST service
        context_files = self.ast_service.get_relevant_context(file_path)
        context_data = []
        
        for context_file in context_files:
            try:
                full_path = os.path.join(self.ast_service.project_root, context_file) if self.ast_service.project_root else context_file
                if os.path.exists(full_path):
                    with open(full_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    context_data.append({
                        'path': context_file,
                        'content': content
                    })
                    logger.debug(f"Added context file: {context_file}")
            except Exception as e:
                logger.warning(f"Failed to read context file {context_file}: {e}")
                continue

        prompt = self.prompt_manager.render(
            "generate_code.jinja2",
            task_description=task.description,
            file_path=file_path,
            context_files=context_data
        )
        if not prompt:
            self._handle_error("Failed to render the engineer prompt.")
            return

        # Run the code generation in a thread to avoid blocking the UI
        thread = threading.Thread(target=self._generate_and_dispatch_code, args=(prompt, "engineer_agent", file_path))
        thread.start()

    def _generate_and_dispatch_code(self, prompt: str, agent_name: str, file_path: str):
        """Generates code and dispatches it in a dedicated thread."""
        provider, model_name, config = self._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            self._handle_error(f"Agent '{agent_name}' is not configured for code generation.")
            return

        try:
            logger.info(f"Generating code for '{file_path}' with agent '{agent_name}'.")
            response_stream = provider.stream_chat(model_name, prompt, config)
            full_code = "".join(list(response_stream))

            if full_code.startswith("ERROR:"):
                self._handle_error(full_code)
                return

            # Dispatch the completed code to the Code Viewer
            code_generated_event = Event(
                event_type="CODE_GENERATED",
                payload={"file_path": file_path, "code": full_code}
            )
            self.event_bus.dispatch(code_generated_event)
            logger.info(f"Successfully generated and dispatched code for '{file_path}'.")

        except Exception as e:
            logger.error(f"Error during code generation thread for {file_path}: {e}", exc_info=True)
            self._handle_error(f"A critical error occurred while generating code for {file_path}.")

    def _handle_conversation(self, user_prompt: str):
        """
        Manages the main conversational flow with the Lead Companion agent.
        This method embodies the "Cognitive Loop".
        """
        self.conversation_management_service.add_message("user", user_prompt)
        history = self.conversation_management_service.get_history()

        provider, model_name, config = self._get_provider_for_agent("lead_companion_agent")
        if not provider or not model_name:
            self._handle_error("Lead Companion agent not configured correctly.")
            return

        try:
            prompt = self.prompt_manager.render("lead_companion_master.jinja2", history=history)
            response_stream = provider.stream_chat(model_name, prompt, config)
            # Buffer the full response to check for tool calls
            full_response = "".join(list(response_stream))

            self.conversation_management_service.add_message("model", full_response)

            # This is the "Cognitive Loop" decision point
            if "tool_name" in full_response:
                self._handle_tool_call(full_response)
            else:
                # If no tool, it's a standard chat response. Dispatch it.
                self.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": full_response}))
                self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED"))

        except Exception as e:
            logger.error(f"Error in conversation handler: {e}", exc_info=True)
            self._handle_error("A critical error occurred in the conversation handler.")

    def _handle_tool_call(self, tool_call_json: str):
        """Parses and executes a tool call from the Lead Companion."""
        try:
            # Clean up potential markdown backticks from the response
            clean_json = tool_call_json.strip().replace("```json", "").replace("```", "")
            data = json.loads(clean_json)
            tool_name = data.get("tool_name")
            arguments = data.get("arguments", {})
            logger.info(f"Lead Companion requested tool: '{tool_name}' with args: {arguments}")

            if tool_name == "consult_architect":
                self._execute_architect_consultation(arguments.get("user_request"))
            else:
                self._handle_error(f"Received unknown tool call: {tool_name}")

        except json.JSONDecodeError:
            logger.error(f"Failed to decode tool call JSON: {tool_call_json}")
            # If the AI messes up the JSON, tell the user gracefully.
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

            # Clean up potential markdown backticks from the response
            plan_data = json.loads(full_response.strip().replace("```json", "").replace("```", ""))
            tasks = plan_data.get("plan", [])

            if not tasks:
                self._handle_error("The Architect returned an empty plan.")
                return

            # Add all the planned tasks to Mission Control!
            for task in tasks:
                self.event_bus.dispatch(Event("ADD_TASK", {"description": task["description"]}))

            # Send a confirmation message back to the user
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
