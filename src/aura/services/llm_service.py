import logging
import json
import copy
from typing import Any, Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.config import AGENT_CONFIG, SETTINGS_FILE
from src.aura.models.events import Event
from src.providers.gemini_provider import GeminiProvider
from src.providers.ollama_provider import OllamaProvider


logger = logging.getLogger(__name__)


class LLMService:
    """
    Low-level dispatcher to LLM providers.

    Responsibilities:
    - Load providers and model configurations.
    - Map configured agents to provider models.
    - Offer simple streaming and non-streaming interfaces for a given agent.
    - Answer model list/config reload requests for the UI.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.agent_config: Dict = {}
        self.providers: Dict = {}
        self.model_to_provider_map: Dict[str, str] = {}

        self._load_providers()
        self._load_agent_configurations()
        self._register_event_handlers()

    # ------------------- Boot / Config -------------------
    def _load_providers(self):
        logger.info("Loading LLM providers...")
        provider_instances = [GeminiProvider(), OllamaProvider()]
        for provider in provider_instances:
            self.providers[provider.provider_name] = provider
            for model_name in provider.get_available_models():
                self.model_to_provider_map[model_name] = provider.provider_name
        logger.info(f"Loaded {len(self.providers)} providers managing {len(self.model_to_provider_map)} models.")

    def _load_agent_configurations(self):
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
        self.event_bus.subscribe("RELOAD_LLM_CONFIG", lambda event: self._load_agent_configurations())
        self.event_bus.subscribe("REQUEST_AVAILABLE_MODELS", self._handle_request_available_models)

    # ------------------- Provider Mapping -------------------
    def _get_provider_for_agent(self, agent_name: str):
        config = self.agent_config.get(agent_name)
        if not config:
            return None, None, None

        model_name = config.get("model")
        if not model_name:
            return None, None, config

        provider_name = self.model_to_provider_map.get(model_name)
        if not provider_name:
            # Attempt to infer from model prefix
            for p_name in self.providers:
                if model_name.lower().startswith(p_name.lower()):
                    provider_name = p_name
                    break
            # Fallback for gemini naming
            if not provider_name and 'gemini' in model_name:
                provider_name = 'Google'

        provider = self.providers.get(provider_name)
        return provider, model_name, config

    # ------------------- Public Dispatcher APIs -------------------
    def stream_chat_for_agent(self, agent_name: str, prompt: Any):
        """Return a generator streaming chunks for the configured agent."""
        provider, model_name, config = self._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            raise ValueError(f"Agent '{agent_name}' is not configured with a valid model.")
        return provider.stream_chat(model_name, prompt, config)

    def stream_structured_for_agent(self, agent_name: str, messages: List[Dict[str, Any]]):
        provider, model_name, config = self._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            raise ValueError(f"Agent '{agent_name}' is not configured with a valid model.")
        if hasattr(provider, 'stream_chat_structured'):
            return provider.stream_chat_structured(model_name, messages, config)
        # Fallback: concatenate messages
        prompt_parts = []
        for m in messages:
            role_prefix = f"{m['role'].capitalize()}: " if m['role'] != 'system' else ""
            content = m.get("content", "")
            if m.get("images"):
                content = f"{content} [Image attached]" if content else "[Image attached]"
            prompt_parts.append(f"{role_prefix}{content}")
        return provider.stream_chat(model_name, "\n\n".join(prompt_parts), config)

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        """Run a blocking generation and return the full text by joining the stream."""
        try:
            stream = self.stream_chat_for_agent(agent_name, prompt)
            return "".join(list(stream))
        except Exception as e:
            logger.error(f"LLM dispatch error for agent '{agent_name}': {e}")
            return "ERROR: Provider dispatch failed"

    # ------------------- UI Support -------------------
    def _handle_request_available_models(self, event: Event):
        models_by_provider = {}
        for provider_name, provider in self.providers.items():
            models_by_provider[provider_name] = provider.get_available_models()
        self.event_bus.dispatch(Event(
            event_type="AVAILABLE_MODELS_RECEIVED",
            payload={"models": models_by_provider}
        ))

    # ------------------- Capability Queries -------------------
    def get_provider_name_for_agent(self, agent_name: str) -> Optional[str]:
        provider, _, _ = self._get_provider_for_agent(agent_name)
        if provider:
            return provider.provider_name
        return None

    def provider_supports_vision(self, agent_name: str) -> bool:
        provider_name = (self.get_provider_name_for_agent(agent_name) or "").lower()
        return provider_name == "google"
