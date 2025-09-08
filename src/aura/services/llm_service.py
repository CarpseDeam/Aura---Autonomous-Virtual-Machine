import os
import logging
import threading
import json
import copy
import re
from typing import Dict

# Application-specific imports
from src.aura.app.event_bus import EventBus
from src.aura.config import AGENT_CONFIG, SETTINGS_FILE
from src.aura.models.events import Event
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.task_management_service import TaskManagementService
from src.aura.services.context_retrieval_service import ContextRetrievalService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.models.task import Task
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
            context_retrieval_service: ContextRetrievalService,
            workspace_service: WorkspaceService
    ):
        """Initializes the LLMService."""
        self.event_bus = event_bus
        self.prompt_manager = prompt_manager
        self.task_management_service = task_management_service
        self.conversation_management_service = conversation_management_service
        self.context_retrieval_service = context_retrieval_service
        self.workspace_service = workspace_service

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

    def _extract_file_path(self, task_description: str) -> str:
        """
        Intelligently extracts file path from task description using multi-layered approach.
        First tries explicit regex patterns, then uses ASTService intelligence for symbol lookup.
        """
        # Step 1: Try existing regex patterns to find explicit file path
        
        # Pattern 1: Look for explicit file paths in backticks
        backtick_match = re.search(r'`([^`]+\.py)`', task_description)
        if backtick_match:
            return backtick_match.group(1)
        
        # Pattern 2: Look for file paths without backticks (workspace/*.py, src/*.py, etc.)
        file_path_pattern = r'\b(?:workspace|src|app|lib|modules?|components?)/[^\s]+\.py\b'
        file_match = re.search(file_path_pattern, task_description, re.IGNORECASE)
        if file_match:
            return file_match.group(0)
        
        # Pattern 3: Look for bare .py files
        py_file_match = re.search(r'\b\w+\.py\b', task_description)
        if py_file_match:
            return f"workspace/{py_file_match.group(0)}"
        
        # Step 2: If explicit paths fail, attempt intelligent symbol lookup using ASTService
        
        # Look for class or function names that might exist in the codebase
        # Pattern matches: "Player class", "Player(", "GameEngine:", "create_player function", etc.
        symbol_patterns = [
            r'\b([A-Z][a-zA-Z0-9_]*)\s+class\b',  # "Player class"
            r'\bclass\s+([A-Z][a-zA-Z0-9_]*)\b',  # "class Player"
            r'\b([A-Z][a-zA-Z0-9_]*)\s*[\(\:]\s*',  # "Player(" or "Player:"
            r'\b([a-z_][a-zA-Z0-9_]*)\s+function\b',  # "create_player function"
            r'\bfunction\s+([a-z_][a-zA-Z0-9_]*)\b',  # "function create_player"
            r'\bdef\s+([a-z_][a-zA-Z0-9_]*)\b',  # "def create_player"
            r'\b([A-Z][a-zA-Z0-9_]*)\b(?=\s+(?:methods|functionality|logic|implementation))',  # "Player methods"
        ]
        
        for pattern in symbol_patterns:
            symbol_match = re.search(pattern, task_description, re.IGNORECASE)
            if symbol_match:
                symbol_name = symbol_match.group(1)
                logger.debug(f"Found potential symbol '{symbol_name}' in task description")
                
                # Query ASTService to find the file containing this symbol
                try:
                    # First try searching for classes (typically capitalized symbols)
                    if symbol_name and symbol_name[0].isupper():
                        class_results = self.context_retrieval_service.ast_service.search_classes(symbol_name)
                        if class_results and len(class_results) > 0:
                            found_file = class_results[0]['file']
                            logger.info(f"ASTService found class '{symbol_name}' in file: {found_file}")
                            return found_file
                    
                    # Then try searching for functions (typically lowercase symbols)
                    function_results = self.context_retrieval_service.ast_service.search_functions(symbol_name)
                    if function_results and len(function_results) > 0:
                        found_file = function_results[0]['file']
                        logger.info(f"ASTService found function '{symbol_name}' in file: {found_file}")
                        return found_file
                        
                except Exception as e:
                    logger.debug(f"ASTService lookup failed for symbol '{symbol_name}': {e}")
                    continue
        
        # Step 3: Look for common Python keywords that might indicate file types
        if re.search(r'\b(?:main|app|server|client|model|view|controller|service|util|helper)\b', task_description, re.IGNORECASE):
            main_match = re.search(r'\b(main|app|server|client|model|view|controller|service|util|helper)\b', task_description, re.IGNORECASE)
            if main_match:
                return f"workspace/{main_match.group(1).lower()}.py"
        
        # Step 4: Final fallback only if all intelligence fails
        logger.warning(f"Could not extract file path from task: '{task_description}'. Using default.")
        return "workspace/generated.py"

    def _sanitize_code(self, code: str) -> str:
        """
        Removes markdown fences and language identifiers from generated code.
        """
        # Remove opening markdown fence with optional language identifier
        code = re.sub(r'^```\w*\s*\n?', '', code, flags=re.MULTILINE)
        
        # Remove closing markdown fence
        code = re.sub(r'\n?```\s*$', '', code, flags=re.MULTILINE)
        
        # Clean up any remaining triple backticks
        code = code.replace('```', '')
        
        # Strip leading/trailing whitespace
        code = code.strip()
        
        return code

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
        file_path = self._extract_file_path(task.description)

        # Use the new ContextRetrievalService to get all relevant context
        context_data = self.context_retrieval_service.get_context_for_task(task.description, file_path)

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
        # Pass task info for Phoenix Initiative validation
        thread = threading.Thread(target=self._generate_and_dispatch_code, args=(prompt, "engineer_agent", file_path, task))
        thread.start()

    def _generate_and_dispatch_code(self, prompt: str, agent_name: str, file_path: str, task=None):
        """Generates code and dispatches it through the Phoenix Initiative validation pipeline."""
        provider, model_name, config = self._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            self._handle_error(f"Agent '{agent_name}' is not configured for code generation.")
            return

        try:
            # Aura Command Deck: Show code generation start
            filename = file_path.split('/')[-1] if '/' in file_path else file_path
            self.event_bus.dispatch(Event(
                event_type="WORKFLOW_STATUS_UPDATE",
                payload={"message": f"Generating code for {filename}...", "status": "in-progress"}
            ))
            
            logger.info(f"🔧 Engineer: Generating code for '{file_path}' with agent '{agent_name}'.")
            response_stream = provider.stream_chat(model_name, prompt, config)
            full_code = "".join(list(response_stream))

            if full_code.startswith("ERROR:"):
                self._handle_error(full_code)
                return

            # Sanitize the code to remove markdown fences
            sanitized_code = self._sanitize_code(full_code)

            # Phoenix Initiative: Route through Quality Gate instead of direct dispatch
            if task and hasattr(task, 'spec') and task.spec:
                # This is a spec-driven task - send to ValidationService
                logger.info(f"🚦 Phoenix Initiative: Routing task {task.id} through Quality Gate")
                validation_event = Event(
                    event_type="VALIDATE_CODE",
                    payload={
                        "spec": task.spec,
                        "generated_code": sanitized_code,
                        "task_id": task.id,
                        "file_path": file_path
                    }
                )
                self.event_bus.dispatch(validation_event)
            else:
                # Legacy path: Direct dispatch for non-spec tasks
                logger.info(f"📋 Legacy: Direct dispatch for non-spec task in '{file_path}'")
                code_generated_event = Event(
                    event_type="CODE_GENERATED",
                    payload={"file_path": file_path, "code": sanitized_code}
                )
                self.event_bus.dispatch(code_generated_event)

            logger.info(f"Successfully processed code generation for '{file_path}'.")

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
            # Render only the system instructions from the template
            system_instructions = self.prompt_manager.render("lead_companion_master.jinja2", user_prompt="")
            
            # Create structured messages with proper role separation
            messages = []
            
            # Add system message
            messages.append({
                "role": "system",
                "content": system_instructions
            })
            
            # Add conversation history with proper roles
            for message in history:
                role = "user" if message["role"] == "user" else "assistant"
                messages.append({
                    "role": role,
                    "content": message["content"]
                })
            
            # Use structured messages if provider supports it, otherwise fallback to concatenated prompt
            if hasattr(provider, 'stream_chat_structured') and callable(getattr(provider, 'stream_chat_structured')):
                response_stream = provider.stream_chat_structured(model_name, messages, config)
            else:
                # Fallback: concatenate messages into a single prompt for legacy providers
                prompt_parts = [system_instructions]
                for message in history:
                    role_prefix = "User: " if message["role"] == "user" else "Assistant: "
                    prompt_parts.append(f"{role_prefix}{message['content']}")
                
                fallback_prompt = "\n\n".join(prompt_parts)
                response_stream = provider.stream_chat(model_name, fallback_prompt, config)
            
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
                # Aura Command Deck: Show architect engagement
                self.event_bus.dispatch(Event(
                    event_type="WORKFLOW_STATUS_UPDATE",
                    payload={"message": "Engaging Architect Agent for project planning...", "status": "info"}
                ))
                self._execute_architect_consultation(arguments.get("user_request"))
            elif tool_name == "consult_engineer":
                # Aura Command Deck: Show engineer engagement
                self.event_bus.dispatch(Event(
                    event_type="WORKFLOW_STATUS_UPDATE", 
                    payload={"message": "Engaging Engineer Agent for code refinement...", "status": "info"}
                ))
                self._execute_engineer_consultation(arguments)
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
        """Invokes the Architect agent to generate a granular blueprint and orchestrates multi-specialist execution."""
        provider, model_name, config = self._get_provider_for_agent("architect_agent")
        if not provider or not model_name:
            self._handle_error("Architect agent is not configured.")
            return

        try:
            prompt = self.prompt_manager.render("plan_project.jinja2", user_request=user_request)
            response_stream = provider.stream_chat(model_name, prompt, config)
            full_response = "".join(list(response_stream))

            # Clean up potential markdown backticks from the response
            blueprint_data = json.loads(full_response.strip().replace("```json", "").replace("```", ""))
            project_name = blueprint_data.get("project_name")
            blueprint = blueprint_data.get("blueprint", {})

            if not project_name:
                self._handle_error("The Architect returned a blueprint without a project_name.")
                return

            if not blueprint:
                self._handle_error("The Architect returned an empty blueprint.")
                return

            # Set active project (triggers Prime Directive: automatic re-indexing)
            try:
                self.workspace_service.set_active_project(project_name)
                logger.info(f"Project '{project_name}' activated and indexed successfully")
            except Exception as e:
                self._handle_error(f"Failed to activate project '{project_name}': {e}")
                return

            # Phoenix Initiative: Multi-Specialist Execution
            # Parse the granular blueprint and create highly specific tasks for each symbol
            total_tasks = 0
            
            for file_path, file_spec in blueprint.items():
                # Generate tasks for each class and its methods
                for class_spec in file_spec.get("classes", []):
                    class_name = class_spec.get("name", "UnknownClass")
                    
                    for method_spec in class_spec.get("methods", []):
                        method_name = method_spec.get("name", "unknown_method")
                        method_signature = method_spec.get("signature", "def unknown_method(self)")
                        method_description = method_spec.get("description", "No description provided")
                        
                        # Create highly specific task for this individual method
                        task_description = (
                            f"In the file `{file_path}`, implement the `{method_name}` method for the `{class_name}` class. "
                            f"Method signature: `{method_signature}`. "
                            f"Functionality: {method_description}"
                        )
                        
                        self.event_bus.dispatch(
                            Event(event_type="ADD_TASK", payload={
                                "description": task_description,
                                "spec": {
                                    "file_path": file_path,
                                    "class_name": class_name,
                                    "method_name": method_name,
                                    "signature": method_signature,
                                    "return_type": method_spec.get("return_type", "None"),
                                    "description": method_description
                                }
                            })
                        )
                        total_tasks += 1
                
                # Generate tasks for standalone functions
                for function_spec in file_spec.get("functions", []):
                    function_name = function_spec.get("name", "unknown_function")
                    function_signature = function_spec.get("signature", "def unknown_function()")
                    function_description = function_spec.get("description", "No description provided")
                    
                    # Create highly specific task for this individual function
                    task_description = (
                        f"In the file `{file_path}`, implement the `{function_name}` function. "
                        f"Function signature: `{function_signature}`. "
                        f"Functionality: {function_description}"
                    )
                    
                    self.event_bus.dispatch(
                        Event(event_type="ADD_TASK", payload={
                            "description": task_description,
                            "spec": {
                                "file_path": file_path,
                                "function_name": function_name,
                                "signature": function_signature,
                                "return_type": function_spec.get("return_type", "None"),
                                "description": function_description
                            }
                        })
                    )
                    total_tasks += 1

            # Mission Control: Display the task list in the chat window
            task_list_html = self._format_task_list_for_chat(blueprint, total_tasks)
            
            # Send the formatted task list to the chat display
            self.event_bus.dispatch(Event(
                event_type="MODEL_CHUNK_RECEIVED", 
                payload={"chunk": task_list_html}
            ))
            
            # Send a confirmation message back to the user
            confirmation_prompt = f"\n\n🔥 Phoenix Initiative Activated! I've created a granular blueprint with {total_tasks} precise tasks. Use the **Dispatch All Tasks** button to begin the build process!"
            self._stream_generation(confirmation_prompt, "lead_companion_agent")

        except Exception as e:
            logger.error(f"Error during architect consultation: {e}", exc_info=True)
            self._handle_error("The Architect specialist encountered an error while drafting the blueprint.")

    def _execute_engineer_consultation(self, arguments: dict):
        """
        Executes a direct engineer consultation for fast-lane refinement tasks.
        This bypasses the full planning process for small, iterative changes.
        """
        task_description = arguments.get("task_description")
        if not task_description:
            logger.error("Engineer consultation received without task_description")
            self._handle_error("I need a clear task description to work with the engineer.")
            return

        logger.info(f"Direct engineer consultation: {task_description}")

        try:
            # Create a new Task object directly from the description
            new_task = Task(description=task_description)
            
            # Add this temporary task to TaskManagementService so handle_dispatch_task can find it
            self.task_management_service.add_temporary_task(new_task)
            
            # Dispatch the task directly to the engineer, bypassing Mission Control
            self.event_bus.dispatch(Event(
                event_type="DISPATCH_TASK",
                payload={"task_id": new_task.id}
            ))
            
            # Send confirmation to the user
            confirmation_prompt = "Perfect! I'm sending this refinement task directly to our engineer. This will be completed quickly using our fast-lane process."
            self._stream_generation(confirmation_prompt, "lead_companion_agent")
            
        except Exception as e:
            logger.error(f"Error during engineer consultation: {e}", exc_info=True)
            self._handle_error("I encountered an error while setting up the engineer consultation.")

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
                self.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": chunk}))

        except Exception as e:
            logger.error(f"Error dispatching to provider {provider.provider_name}: {e}", exc_info=True)
            self._handle_error(f"An error occurred with the {provider.provider_name} provider.")
        finally:
            self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED"))

    def _handle_error(self, message: str):
        """Dispatches a model error event."""
        logger.error(message)
        self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": message}))

    def _format_task_list_for_chat(self, blueprint: dict, total_tasks: int) -> str:
        """
        Mission Control: Format the architect's blueprint as HTML for the chat display.
        """
        html_parts = []
        
        # Header
        html_parts.append(f"""
        <div style="margin: 15px 0; padding: 15px; border-left: 4px solid #FFB74D; background-color: rgba(255, 183, 77, 0.1);">
            <div style="color: #FFB74D; font-weight: bold; font-size: 16px; margin-bottom: 10px;">
                📋 PROJECT BLUEPRINT
            </div>
            <div style="color: #dcdcdc; font-size: 14px; margin-bottom: 15px;">
                Generated {total_tasks} precision tasks across {len(blueprint)} files
            </div>
        """)
        
        # Task list
        task_counter = 1
        for file_path, file_spec in blueprint.items():
            file_name = file_path.split('/')[-1] if '/' in file_path else file_path
            
            html_parts.append(f"""
            <div style="margin: 8px 0;">
                <div style="color: #64B5F6; font-weight: bold; font-size: 14px;">
                    📁 {file_name}
                </div>
            """)
            
            # Classes and methods
            for class_spec in file_spec.get("classes", []):
                class_name = class_spec.get("name", "UnknownClass")
                methods = class_spec.get("methods", [])
                
                if methods:
                    html_parts.append(f"""
                    <div style="margin-left: 15px; color: #39FF14;">
                        🔧 {class_name} class ({len(methods)} methods)
                    </div>
                    """)
                    
                    for method in methods[:3]:  # Show first 3 methods to avoid clutter
                        method_name = method.get("name", "unknown")
                        html_parts.append(f"""
                        <div style="margin-left: 30px; color: #a0a0a0; font-size: 12px;">
                            • {method_name}()
                        </div>
                        """)
                        task_counter += 1
                    
                    if len(methods) > 3:
                        html_parts.append(f"""
                        <div style="margin-left: 30px; color: #666666; font-size: 12px; font-style: italic;">
                            + {len(methods) - 3} more methods...
                        </div>
                        """)
            
            # Standalone functions
            functions = file_spec.get("functions", [])
            if functions:
                for func in functions:
                    func_name = func.get("name", "unknown")
                    html_parts.append(f"""
                    <div style="margin-left: 15px; color: #FF69B4;">
                        ⚙️ {func_name}() function
                    </div>
                    """)
                    task_counter += 1
                    
            html_parts.append("</div>")
        
        # Footer
        html_parts.append("""
            <div style="margin-top: 15px; padding-top: 10px; border-top: 1px solid #4a4a4a;">
                <div style="color: #FFB74D; font-size: 13px;">
                    ✨ Ready for dispatch - Each task will be validated through the Phoenix Quality Gate
                </div>
            </div>
        </div>
        """)
        
        return "".join(html_parts)

    def _handle_request_available_models(self, event: Event):
        """Handles the request for available models."""
        models_by_provider = {}
        for provider_name, provider in self.providers.items():
            models_by_provider[provider_name] = provider.get_available_models()

        self.event_bus.dispatch(Event(
            event_type="AVAILABLE_MODELS_RECEIVED",
            payload={"models": models_by_provider}
        ))
