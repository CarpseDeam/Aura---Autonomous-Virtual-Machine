import copy
import logging
import socket
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, TypeVar

import asyncio

from src.aura.app.event_bus import EventBus
from src.aura.config import AGENT_CONFIG
from src.aura.models.exceptions import (
    LLMConnectionError,
    LLMRateLimitError,
    LLMServiceError,
    LLMTimeoutError,
)
from src.aura.models.events import Event
from src.aura.services.image_storage_service import ImageStorageService
from src.aura.services.user_settings_manager import load_user_settings
from src.providers.gemini_provider import GeminiProvider
from src.providers.ollama_provider import OllamaProvider

try:
    from requests import exceptions as requests_exceptions
except Exception:  # pragma: no cover - requests may be optional in some deployments
    requests_exceptions = None

try:
    from google.api_core import exceptions as google_exceptions
except Exception:  # pragma: no cover - google client library may be optional
    google_exceptions = None

T = TypeVar("T")


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

    _RETRY_BACKOFF_SECONDS: Tuple[int, ...] = (1, 2, 4)
    _RETRY_SUGGESTIONS: Tuple[str, ...] = (
        "Check your LLM API key configuration.",
        "Verify your provider quota usage.",
        "Ensure your network connection is stable.",
    )

    def __init__(self, event_bus: EventBus, image_storage: ImageStorageService):
        self.event_bus = event_bus
        self.image_storage = image_storage
        self.agent_config: Dict = {}
        self.providers: Dict = {}
        self.model_to_provider_map: Dict[str, str] = {}

        self._load_providers()
        self._load_agent_configurations()
        self._register_event_handlers()

    # ------------------- Boot / Config -------------------
    def _load_providers(self):
        logger.info("Loading LLM providers...")
        provider_instances = [
            GeminiProvider(image_storage=self.image_storage),
            OllamaProvider(),
        ]
        for provider in provider_instances:
            self.providers[provider.provider_name] = provider
            for model_name in provider.get_available_models():
                self.model_to_provider_map[model_name] = provider.provider_name
        logger.info(f"Loaded {len(self.providers)} providers managing {len(self.model_to_provider_map)} models.")

    def _load_agent_configurations(self):
        config = copy.deepcopy(AGENT_CONFIG)
        logger.info("Loading default agent configurations.")

        try:
            user_settings = load_user_settings()
            # New simplified structure centers configuration around a single Aura brain model.
            aura_brain_model = user_settings.get("aura_brain_model")
            if isinstance(aura_brain_model, str) and aura_brain_model.strip():
                for agent_name in config.keys():
                    agent_config = config.setdefault(agent_name, {})
                    agent_config["model"] = aura_brain_model.strip()

            # Legacy compatibility: allow per-agent overrides if still present.
            legacy_agents = user_settings.get("agents", {})
            if isinstance(legacy_agents, dict):
                for agent_name, user_agent_config in legacy_agents.items():
                    if not isinstance(user_agent_config, dict):
                        logger.debug("Skipping non-dict agent config for '%s'.", agent_name)
                        continue
                    base_config = config.setdefault(agent_name, {})
                    if user_agent_config.get("model"):
                        base_config.update(user_agent_config)
        except Exception as e:
            logger.error(f"Failed to load or merge user agent settings: {e}. Using defaults.")

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
    def stream_chat_for_agent(self, agent_name: str, prompt: Any) -> Generator[str, None, None]:
        """
        Return a generator streaming chunks for the configured agent.

        Args:
            agent_name: The configured agent name.
            prompt: The prompt payload to send to the provider.

        Returns:
            A generator yielding response chunks from the provider.

        Raises:
            ValueError: If no provider/model mapping exists for the agent.
            LLMServiceError: If the provider call fails after all retries.
        """
        provider, model_name, config = self._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            raise ValueError(f"Agent '{agent_name}' is not configured with a valid model.")
        return self._stream_with_retries(
            agent_name=agent_name,
            operation_name="stream_chat",
            stream_factory=lambda: provider.stream_chat(model_name, prompt, config),
        )

    def stream_structured_for_agent(self, agent_name: str, messages: List[Dict[str, Any]]) -> Generator[str, None, None]:
        provider, model_name, config = self._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            raise ValueError(f"Agent '{agent_name}' is not configured with a valid model.")
        operation_name = "stream_chat_structured"

        def _factory() -> Generator[str, None, None]:
            if hasattr(provider, "stream_chat_structured"):
                return provider.stream_chat_structured(model_name, messages, config)

            prompt_parts: List[str] = []
            for message in messages:
                role_prefix = (
                    f"{message['role'].capitalize()}: " if message.get("role") != "system" else ""
                )
                content = message.get("content", "")
                if message.get("images"):
                    content = f"{content} [Image attached]" if content else "[Image attached]"
                prompt_parts.append(f"{role_prefix}{content}")
            fallback_prompt = "\n\n".join(prompt_parts)
            return provider.stream_chat(model_name, fallback_prompt, config)

        return self._stream_with_retries(
            agent_name=agent_name,
            operation_name=operation_name,
            stream_factory=_factory,
        )

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        """
        Execute a blocking LLM call and return the concatenated response text.

        Args:
            agent_name: The configured agent name.
            prompt: The textual prompt to send to the provider.

        Returns:
            The full response text returned by the provider.

        Raises:
            ValueError: If no provider/model mapping exists for the agent.
            LLMServiceError: If the provider call fails after all retries.
        """
        provider, model_name, config = self._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            raise ValueError(f"Agent '{agent_name}' is not configured with a valid model.")

        def _operation() -> str:
            stream = provider.stream_chat(model_name, prompt, config)
            chunks: List[str] = []
            for chunk in stream:
                if chunk is None:
                    continue
                chunks.append(str(chunk))
            return "".join(chunks)

        return self._invoke_with_retries(
            agent_name=agent_name,
            operation_name="run_for_agent",
            operation=_operation,
        )

    def _invoke_with_retries(
        self,
        agent_name: str,
        operation_name: str,
        operation: Callable[[], T],
    ) -> T:
        """
        Execute a provider operation with exponential backoff handling.

        Args:
            agent_name: The agent associated with the request.
            operation_name: Human-readable operation identifier for logging.
            operation: Callable that executes the provider request.

        Returns:
            The result returned by the operation.

        Raises:
            LLMServiceError: If the operation fails after retries or encounters a non-retryable error.
        """
        total_attempts = len(self._RETRY_BACKOFF_SECONDS) + 1
        for attempt in range(total_attempts):
            try:
                result = operation()
                logger.info(
                    "LLM %s succeeded for agent '%s' on attempt %d/%d.",
                    operation_name,
                    agent_name,
                    attempt + 1,
                    total_attempts,
                )
                return result
            except Exception as exc:  # noqa: BLE001 - we classify below
                error, retryable = self._categorize_exception(exc, agent_name, operation_name)
                if not retryable or attempt == len(self._RETRY_BACKOFF_SECONDS):
                    self._handle_permanent_failure(agent_name, operation_name, error)
                retry_count = attempt + 1
                self._handle_retry(agent_name, operation_name, error, retry_count)
                time.sleep(self._RETRY_BACKOFF_SECONDS[attempt])

        # The loop exits via return or permanent failure; this guard satisfies type checkers.
        raise LLMServiceError(
            f"Unexpected retry state for operation '{operation_name}' on agent '{agent_name}'.",
            agent_name=agent_name,
        )

    def _stream_with_retries(
        self,
        agent_name: str,
        operation_name: str,
        stream_factory: Callable[[], Generator[str, None, None]],
    ) -> Generator[str, None, None]:
        """
        Execute a streaming provider operation with exponential backoff handling.

        Args:
            agent_name: The agent associated with the request.
            operation_name: Human-readable operation identifier for logging.
            stream_factory: Callable that returns the provider's streaming generator.

        Returns:
            A generator yielding response chunks from the provider.

        Raises:
            LLMServiceError: If streaming fails after retries or encounters a non-retryable error.
        """
        total_attempts = len(self._RETRY_BACKOFF_SECONDS) + 1

        def _generator() -> Generator[str, None, None]:
            for attempt in range(total_attempts):
                try:
                    stream = stream_factory()
                except Exception as exc:  # noqa: BLE001 - classification occurs below
                    error, retryable = self._categorize_exception(exc, agent_name, operation_name)
                    if not retryable or attempt == len(self._RETRY_BACKOFF_SECONDS):
                        self._handle_permanent_failure(agent_name, operation_name, error)
                    retry_count = attempt + 1
                    self._handle_retry(agent_name, operation_name, error, retry_count)
                    time.sleep(self._RETRY_BACKOFF_SECONDS[attempt])
                    continue

                try:
                    for chunk in stream:
                        yield chunk
                except Exception as exc:  # noqa: BLE001 - classification occurs below
                    error, retryable = self._categorize_exception(exc, agent_name, operation_name)
                    if not retryable or attempt == len(self._RETRY_BACKOFF_SECONDS):
                        self._handle_permanent_failure(agent_name, operation_name, error)
                    retry_count = attempt + 1
                    self._handle_retry(agent_name, operation_name, error, retry_count)
                    time.sleep(self._RETRY_BACKOFF_SECONDS[attempt])
                    continue
                else:
                    logger.info(
                        "LLM %s completed for agent '%s' on attempt %d/%d.",
                        operation_name,
                        agent_name,
                        attempt + 1,
                        total_attempts,
                    )
                    return

            raise LLMServiceError(
                f"Streaming operation '{operation_name}' entered an unexpected retry state for agent '{agent_name}'.",
                agent_name=agent_name,
            )

        return _generator()

    def _handle_retry(
        self,
        agent_name: str,
        operation_name: str,
        error: LLMServiceError,
        retry_count: int,
    ) -> None:
        """
        Log and emit telemetry for a retry attempt.

        Args:
            agent_name: The agent associated with the request.
            operation_name: Human-readable operation identifier for logging.
            error: The classified error that triggered the retry.
            retry_count: One-based retry attempt counter.
        """
        total_retries = len(self._RETRY_BACKOFF_SECONDS)
        logger.warning(
            "LLM %s failed for agent '%s' (%s). Retrying attempt %d/%d.",
            operation_name,
            agent_name,
            error,
            retry_count,
            total_retries,
        )
        self._dispatch_service_event(
            "LLM_SERVICE_WARNING",
            {
                "message": f"Retrying LLM call (attempt {retry_count}/{total_retries})...",
            },
        )

    def _handle_permanent_failure(
        self,
        agent_name: str,
        operation_name: str,
        error: LLMServiceError,
    ) -> None:
        """
        Convert a classified error into a final exception, log, notify, and raise it.

        Args:
            agent_name: The agent associated with the request.
            operation_name: Human-readable operation identifier for logging.
            error: The classified error instance.

        Raises:
            LLMServiceError: Always raised with contextual information.
        """
        final_error = self._finalize_exception(error, agent_name, operation_name)
        logger.error(
            "LLM %s failed for agent '%s' after %d attempts: %s",
            operation_name,
            agent_name,
            len(self._RETRY_BACKOFF_SECONDS) + 1,
            final_error,
        )
        self._dispatch_failure_event(final_error)
        raise final_error

    def _finalize_exception(
        self,
        error: LLMServiceError,
        agent_name: str,
        operation_name: str,
    ) -> LLMServiceError:
        """
        Attach retry metadata to a classified LLM service error.

        Args:
            error: The classified error instance.
            agent_name: The agent associated with the request.
            operation_name: Human-readable operation identifier for logging.

        Returns:
            LLMServiceError enriched with contextual metadata.
        """
        message = (
            f"{operation_name} for agent '{agent_name}' failed after "
            f"{len(self._RETRY_BACKOFF_SECONDS) + 1} attempts: {error}"
        )
        return error.__class__(
            message,
            agent_name=agent_name,
            cause=error.__cause__ or error,
        )

    def _dispatch_failure_event(self, error: LLMServiceError) -> None:
        """
        Dispatch a user-facing error notification for a terminal failure.

        Args:
            error: The classified error describing the failure.
        """
        payload = {
            "message": str(error),
            "suggestions": list(self._RETRY_SUGGESTIONS),
        }
        self._dispatch_service_event("LLM_SERVICE_ERROR", payload)

    def _dispatch_service_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """
        Dispatch an event while preventing telemetry failures from bubbling up.

        Args:
            event_type: The event type to dispatch.
            payload: Event payload dictionary.
        """
        try:
            self.event_bus.dispatch(Event(event_type=event_type, payload=payload))
        except Exception:  # pragma: no cover - defensive safety net
            logger.debug(
                "Failed to dispatch '%s' event with payload %s",
                event_type,
                payload,
                exc_info=True,
            )

    def _categorize_exception(
        self,
        exc: Exception,
        agent_name: str,
        operation_name: str,
    ) -> Tuple[LLMServiceError, bool]:
        """
        Classify the exception and determine if it is transient.

        Args:
            exc: The exception raised by the provider.
            agent_name: The agent associated with the request.
            operation_name: Human-readable operation identifier for logging.

        Returns:
            A tuple of (classified error, is_retryable).
        """
        if isinstance(exc, LLMServiceError):
            return exc, False

        if self._is_timeout_error(exc):
            return (
                LLMTimeoutError(
                    f"Timeout while executing {operation_name} for agent '{agent_name}': {exc}",
                    agent_name=agent_name,
                    cause=exc,
                ),
                True,
            )

        if self._is_rate_limit_error(exc):
            return (
                LLMRateLimitError(
                    f"Rate limit encountered during {operation_name} for agent '{agent_name}': {exc}",
                    agent_name=agent_name,
                    cause=exc,
                ),
                True,
            )

        if self._is_connection_error(exc):
            return (
                LLMConnectionError(
                    f"Connection issue during {operation_name} for agent '{agent_name}': {exc}",
                    agent_name=agent_name,
                    cause=exc,
                ),
                True,
            )

        return (
            LLMServiceError(
                f"Unhandled provider error during {operation_name} for agent '{agent_name}': {exc}",
                agent_name=agent_name,
                cause=exc,
            ),
            False,
        )

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        """
        Determine whether the exception represents a timeout condition.

        Args:
            exc: Exception raised by the provider.

        Returns:
            True if the error indicates a timeout, otherwise False.
        """
        timeout_types: Tuple[type, ...] = (asyncio.TimeoutError, TimeoutError)
        if any(isinstance(exc, timeout_type) for timeout_type in timeout_types):
            return True

        if isinstance(exc, socket.timeout):
            return True

        if requests_exceptions:
            timeout_attrs = (
                getattr(requests_exceptions, "Timeout", None),
                getattr(requests_exceptions, "ReadTimeout", None),
                getattr(requests_exceptions, "ConnectTimeout", None),
            )
            if any(timeout_attr and isinstance(exc, timeout_attr) for timeout_attr in timeout_attrs):
                return True

        if google_exceptions:
            timeout_classes = (
                getattr(google_exceptions, "DeadlineExceeded", None),
                getattr(google_exceptions, "ServiceUnavailable", None),
            )
            if any(cls and isinstance(exc, cls) for cls in timeout_classes):
                return True

        message = str(exc).lower()
        return "timeout" in message or "timed out" in message

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        """
        Determine whether the exception represents a rate limit or quota issue.

        Args:
            exc: Exception raised by the provider.

        Returns:
            True if the error indicates a rate limit, otherwise False.
        """
        if requests_exceptions:
            http_error = getattr(requests_exceptions, "HTTPError", None)
            if http_error and isinstance(exc, http_error):
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 429:
                    return True

        if google_exceptions:
            resource_exhausted = getattr(google_exceptions, "ResourceExhausted", None)
            if resource_exhausted and isinstance(exc, resource_exhausted):
                return True

        message = str(exc).lower()
        return "rate limit" in message or "quota" in message

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        """
        Determine whether the exception represents a connectivity problem.

        Args:
            exc: Exception raised by the provider.

        Returns:
            True if the error indicates a connection issue, otherwise False.
        """
        connection_indicators = (
            "connection reset",
            "connection aborted",
            "connection refused",
            "temporary failure in name resolution",
            "network unreachable",
            "connection closed",
            "dns failure",
        )

        if isinstance(exc, ConnectionError):
            return True

        if isinstance(exc, socket.gaierror):
            return True

        if requests_exceptions:
            connection_types = (
                getattr(requests_exceptions, "ConnectionError", None),
                getattr(requests_exceptions, "ProxyError", None),
                getattr(requests_exceptions, "SSLError", None),
                getattr(requests_exceptions, "ChunkedEncodingError", None),
            )
            if any(conn_type and isinstance(exc, conn_type) for conn_type in connection_types):
                return True

        if google_exceptions:
            unavailable = getattr(google_exceptions, "ServiceUnavailable", None)
            if unavailable and isinstance(exc, unavailable):
                return True

        message = str(exc).lower()
        return any(indicator in message for indicator in connection_indicators)

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
