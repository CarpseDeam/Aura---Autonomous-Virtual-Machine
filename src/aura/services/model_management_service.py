import logging
from typing import Dict, List
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.providers.base import LLMProvider
from src.providers.gemini_provider import GeminiProvider
from src.providers.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class ModelManagementService:
    """
    Manages available LLM models by loading and orchestrating multiple
    LLMProvider implementations.
    """

    def __init__(self, event_bus: EventBus):
        """Initializes the ModelManagementService."""
        self.event_bus = event_bus
        self.providers: List[LLMProvider] = []
        self._load_providers()
        self._register_event_handlers()
        logger.info(f"ModelManagementService initialized with {len(self.providers)} providers.")

    def _load_providers(self):
        """
        Instantiates all available LLM providers. In a more advanced system,
        this could use plugin discovery.
        """
        # Add new provider instances here
        self.providers.append(GeminiProvider())
        self.providers.append(OllamaProvider())
        # To add more, just append a new provider instance.

    def _register_event_handlers(self):
        """Subscribes the service to relevant events."""
        self.event_bus.subscribe("REQUEST_AVAILABLE_MODELS", self.handle_request_available_models)

    def get_all_models(self) -> Dict[str, List[str]]:
        """
        Gathers all available models from all loaded providers.

        Returns:
            A dictionary where keys are provider names and values are lists
            of their available models.
        """
        all_models = {}
        for provider in self.providers:
            try:
                provider_name = provider.provider_name
                models = provider.get_available_models()
                if provider_name in all_models:
                    all_models[provider_name].extend(models)
                else:
                    all_models[provider_name] = models
                logger.info(f"Loaded {len(models)} models from {provider_name} provider.")
            except Exception as e:
                logger.error(f"Failed to get models from provider {type(provider).__name__}: {e}", exc_info=True)
        return all_models

    def handle_request_available_models(self, event: Event):
        """
        Handles the request for available models and dispatches them.
        """
        logger.info("Fetching all available models from loaded providers...")
        models = self.get_all_models()
        self.event_bus.dispatch(Event(
            event_type="AVAILABLE_MODELS_RECEIVED",
            payload={"models": models}
        ))