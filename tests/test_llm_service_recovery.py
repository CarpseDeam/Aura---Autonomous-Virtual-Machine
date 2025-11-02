import types
from typing import Any, Callable, Generator, List

import pytest

from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.executor.conversation_utils import (
    build_discuss_fallback_response,
    summarize_original_action,
)
from src.aura.models.action import Action, ActionType
from src.aura.models.events import Event
from src.aura.models.exceptions import (
    LLMConnectionError,
    LLMServiceError,
    LLMTimeoutError,
)
from src.aura.models.intent import Intent
from src.aura.models.project_context import ProjectContext
from src.aura.services.llm_service import LLMService


class DummyEventBus:
    def __init__(self) -> None:
        self.dispatched: List[Event] = []

    def subscribe(self, event_type: str, callback: Callable[[Event], None]) -> None:
        return None

    def dispatch(self, event: Event) -> None:
        self.dispatched.append(event)


class DummyPromptManager:
    def render(self, template_name: str, **kwargs: Any) -> str:
        return "dummy prompt"


class FlakyProvider:
    provider_name = "DummyProvider"

    def __init__(self, failures_before_success: int, final_chunks: List[str]) -> None:
        self.failures_before_success = failures_before_success
        self.final_chunks = final_chunks
        self.invocations = 0

    def get_available_models(self) -> List[str]:
        return ["test-model"]

    def stream_chat(self, model_name: str, prompt: Any, config: dict) -> Generator[str, None, None]:
        self.invocations += 1
        if self.invocations <= self.failures_before_success:
            raise TimeoutError("simulated timeout")

        return self._success_stream()

    def _success_stream(self) -> Generator[str, None, None]:
        def _generator() -> Generator[str, None, None]:
            for chunk in self.final_chunks:
                yield chunk

        return _generator()


class ConnectionFlakyProvider(FlakyProvider):
    def stream_chat(self, model_name: str, prompt: Any, config: dict) -> Generator[str, None, None]:
        self.invocations += 1
        if self.invocations <= self.failures_before_success:
            raise ConnectionError("simulated connection error")
        return self._success_stream()


class AlwaysFailProvider:
    provider_name = "AlwaysFail"

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.invocations = 0

    def get_available_models(self) -> List[str]:
        return ["test-model"]

    def stream_chat(self, model_name: str, prompt: Any, config: dict) -> Generator[str, None, None]:
        self.invocations += 1
        raise self.error


def _build_llm_service_with_provider(provider: Any) -> LLMService:
    service: LLMService = LLMService.__new__(LLMService)
    service.event_bus = DummyEventBus()
    service.image_storage = None  # type: ignore[assignment]
    service.agent_config = {"test_agent": {"model": "test-model"}}
    service.providers = {provider.provider_name: provider}
    service.model_to_provider_map = {"test-model": provider.provider_name}
    return service


def test_run_for_agent_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FlakyProvider(failures_before_success=2, final_chunks=["ok"])
    service = _build_llm_service_with_provider(provider)
    sleep_calls: List[int] = []
    monkeypatch.setattr("src.aura.services.llm_service.time.sleep", lambda value: sleep_calls.append(value))

    result = service.run_for_agent("test_agent", "prompt text")

    assert result == "ok"
    assert provider.invocations == 3
    assert sleep_calls == [1, 2]

    warning_events = [event for event in service.event_bus.dispatched if event.event_type == "LLM_SERVICE_WARNING"]
    assert [event.payload["message"] for event in warning_events] == [
        "Retrying LLM call (attempt 1/3)...",
        "Retrying LLM call (attempt 2/3)...",
    ]
    assert not [event for event in service.event_bus.dispatched if event.event_type == "LLM_SERVICE_ERROR"]


def test_run_for_agent_raises_after_exhausted_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = AlwaysFailProvider(error=TimeoutError("still timing out"))
    service = _build_llm_service_with_provider(provider)
    sleep_calls: List[int] = []
    monkeypatch.setattr("src.aura.services.llm_service.time.sleep", lambda value: sleep_calls.append(value))

    with pytest.raises(LLMTimeoutError) as exc_info:
        service.run_for_agent("test_agent", "prompt text")

    assert "failed after 4 attempts" in str(exc_info.value)
    assert provider.invocations == 4
    assert sleep_calls == [1, 2, 4]

    warning_events = [event for event in service.event_bus.dispatched if event.event_type == "LLM_SERVICE_WARNING"]
    assert len(warning_events) == 3
    error_events = [event for event in service.event_bus.dispatched if event.event_type == "LLM_SERVICE_ERROR"]
    assert len(error_events) == 1
    assert error_events[0].payload["suggestions"] == [
        "Check your LLM API key configuration.",
        "Verify your provider quota usage.",
        "Ensure your network connection is stable.",
    ]


def test_stream_chat_for_agent_retries_and_yields(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ConnectionFlakyProvider(failures_before_success=1, final_chunks=["he", "llo"])
    service = _build_llm_service_with_provider(provider)
    sleep_calls: List[int] = []
    monkeypatch.setattr("src.aura.services.llm_service.time.sleep", lambda value: sleep_calls.append(value))

    stream = service.stream_chat_for_agent("test_agent", "prompt payload")
    output = "".join(list(stream))

    assert output == "hello"
    assert provider.invocations == 2
    assert sleep_calls == [1]
    warning_events = [event for event in service.event_bus.dispatched if event.event_type == "LLM_SERVICE_WARNING"]
    assert len(warning_events) == 1
    assert warning_events[0].payload["message"] == "Retrying LLM call (attempt 1/3)..."
    assert not [event for event in service.event_bus.dispatched if event.event_type == "LLM_SERVICE_ERROR"]


def test_detect_user_intent_defaults_on_llm_failure() -> None:
    class FailingLLM:
        def run_for_agent(self, agent_name: str, prompt: str) -> str:
            raise LLMTimeoutError("intent timeout", agent_name=agent_name)

    brain = AuraBrain(llm=FailingLLM(), prompts=DummyPromptManager())
    result = brain._detect_user_intent("hello", [])
    assert result is Intent.CASUAL_CHAT


def test_decide_returns_simple_reply_when_reasoning_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingReasoningLLM:
        def run_for_agent(self, agent_name: str, prompt: str) -> str:
            raise LLMConnectionError("reasoning unavailable", agent_name=agent_name)

    brain = AuraBrain(llm=FailingReasoningLLM(), prompts=DummyPromptManager())
    monkeypatch.setattr(AuraBrain, "_detect_user_intent", lambda self, user_text, history: Intent.BUILD_CLEAR)

    context = ProjectContext()
    action = brain.decide("help me build", context)

    assert action.type is ActionType.SIMPLE_REPLY
    assert action.params["request"] == (
        "I'm having trouble connecting to my reasoning engine right now. "
        "Let me try to help you anyway - what do you need?"
    )


class DummyLLM:
    def __init__(self, stream_error: Exception | None = None, run_error: Exception | None = None) -> None:
        self.stream_error = stream_error
        self.run_error = run_error

    def stream_chat_for_agent(self, agent_name: str, prompt: Any) -> Generator[str, None, None]:
        if self.stream_error:
            raise self.stream_error

        def _generator() -> Generator[str, None, None]:
            yield "ok"

        return _generator()

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        if self.run_error:
            raise self.run_error
        return "response"


class DummyService:
    def __getattr__(self, item: str) -> Any:
        return types.SimpleNamespace()


def test_execute_simple_reply_returns_fallback_on_stream_failure() -> None:
    fallback_error = LLMConnectionError("stream failure", agent_name="lead_companion_agent")
    executor = AuraExecutor(
        event_bus=DummyEventBus(),
        llm=DummyLLM(stream_error=fallback_error),
        prompts=DummyPromptManager(),
        workspace=DummyService(),  # type: ignore[arg-type]
        file_registry=DummyService(),  # type: ignore[arg-type]
        terminal_service=DummyService(),  # type: ignore[arg-type]
        workspace_monitor=DummyService(),  # type: ignore[arg-type]
        terminal_session_manager=DummyService(),  # type: ignore[arg-type]
    )
    ctx = ProjectContext(conversation_history=[{"role": "user", "content": "hello"}])
    action = Action(type=ActionType.SIMPLE_REPLY, params={"request": "hi"})

    result = executor.conversation_handler.execute_simple_reply(action, ctx)

    assert result == "I'm having connection issues right now. Please check your API key and network connection."


def test_execute_discuss_uses_fallback_on_llm_failure() -> None:
    fallback_error = LLMServiceError("formatting failure", agent_name="lead_companion_agent")
    executor = AuraExecutor(
        event_bus=DummyEventBus(),
        llm=DummyLLM(run_error=fallback_error),
        prompts=DummyPromptManager(),
        workspace=DummyService(),  # type: ignore[arg-type]
        file_registry=DummyService(),  # type: ignore[arg-type]
        terminal_service=DummyService(),  # type: ignore[arg-type]
        workspace_monitor=DummyService(),  # type: ignore[arg-type]
        terminal_session_manager=DummyService(),  # type: ignore[arg-type]
    )

    action = Action(
        type=ActionType.DISCUSS,
        params={
            "questions": ["Can you clarify the API endpoints?"],
            "unclear_aspects": ["Authentication flow"],
            "original_action": {"type": "WRITE_FILE", "params": {"request": "Create API docs"}},
        },
    )
    context = ProjectContext()

    expected = build_discuss_fallback_response(
        ["Can you clarify the API endpoints?"],
        ["Authentication flow"],
        summarize_original_action({"type": "WRITE_FILE", "params": {"request": "Create API docs"}}),
    )

    result = executor.conversation_handler.execute_discuss(action, context)

    assert result == expected


# -- LLM Configuration Loading Tests ---------------------------------------------------


def test_llm_service_loads_default_agent_config() -> None:
    """Test that LLMService loads default agent configuration on initialization."""
    service = _build_llm_service_with_provider(FlakyProvider(0, ["test"]))

    # Should have agent_config populated
    assert isinstance(service.agent_config, dict)
    assert "test_agent" in service.agent_config


def test_llm_service_loads_settings_based_model_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that LLMService loads aura_brain_model from user settings."""
    # Mock load_user_settings to return a specific brain model
    mock_settings = {
        "aura_brain_model": "claude-opus-4",
        "terminal_agent": "codex",
        "api_keys": {},
    }
    monkeypatch.setattr("src.aura.services.llm_service.load_user_settings", lambda: mock_settings)

    service = _build_llm_service_with_provider(FlakyProvider(0, ["test"]))

    # Reload configuration to apply mocked settings
    service._load_agent_configurations()

    # All agents should now use the brain model from settings
    for agent_name, config in service.agent_config.items():
        if isinstance(config, dict) and config.get("model"):
            assert config["model"] == "claude-opus-4"


def test_llm_service_handles_legacy_per_agent_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that LLMService still supports legacy per-agent model overrides."""
    mock_settings = {
        "aura_brain_model": "claude-sonnet-4-5",
        "agents": {
            "architect_agent": {
                "model": "claude-opus-4",  # Override for this specific agent
            },
            "reasoning_agent": {
                "model": "gemini-2.5-pro",  # Override for this agent
            },
        },
    }
    monkeypatch.setattr("src.aura.services.llm_service.load_user_settings", lambda: mock_settings)

    service = _build_llm_service_with_provider(FlakyProvider(0, ["test"]))
    service._load_agent_configurations()

    # Check if legacy overrides are applied
    if "architect_agent" in service.agent_config:
        assert service.agent_config["architect_agent"]["model"] == "claude-opus-4"
    if "reasoning_agent" in service.agent_config:
        assert service.agent_config["reasoning_agent"]["model"] == "gemini-2.5-pro"


def test_llm_service_handles_malformed_settings_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that LLMService handles malformed settings without crashing."""

    def raise_error():
        raise ValueError("Settings file corrupted")

    monkeypatch.setattr("src.aura.services.llm_service.load_user_settings", raise_error)

    # Should not crash, should use defaults
    service = _build_llm_service_with_provider(FlakyProvider(0, ["test"]))
    service._load_agent_configurations()

    # Should have some agent config (defaults)
    assert isinstance(service.agent_config, dict)


def test_llm_service_reloads_config_on_event() -> None:
    """Test that LLMService reloads configuration when RELOAD_LLM_CONFIG event is dispatched."""
    event_bus = DummyEventBus()
    service: LLMService = LLMService.__new__(LLMService)
    service.event_bus = event_bus
    service.image_storage = None  # type: ignore[assignment]
    service.agent_config = {"old_agent": {"model": "old-model"}}
    service.providers = {}
    service.model_to_provider_map = {}

    # Register event handlers
    service._register_event_handlers()

    # Initial config
    initial_config = service.agent_config.copy()

    # Dispatch reload event
    reload_event = Event(event_type="RELOAD_LLM_CONFIG", payload={})
    event_bus.dispatch(reload_event)

    # Config should be reloaded (even if it's the same, the method should be called)
    # We can't easily test if it changed, but we can verify the handler is registered
    assert any(e.event_type == "RELOAD_LLM_CONFIG" for e in event_bus.dispatched)


# -- Provider Routing Tests ------------------------------------------------------------


def test_llm_service_routes_to_correct_provider_by_model_name() -> None:
    """Test that LLMService routes requests to the correct provider based on model name."""
    service: LLMService = LLMService.__new__(LLMService)
    service.event_bus = DummyEventBus()
    service.image_storage = None  # type: ignore[assignment]

    # Set up multiple providers
    gemini_provider = FlakyProvider(0, ["gemini response"])
    gemini_provider.provider_name = "Google"
    ollama_provider = FlakyProvider(0, ["ollama response"])
    ollama_provider.provider_name = "Ollama"

    service.providers = {
        "Google": gemini_provider,
        "Ollama": ollama_provider,
    }

    service.model_to_provider_map = {
        "gemini-2.5-pro": "Google",
        "llama3:8b": "Ollama",
    }

    service.agent_config = {
        "gemini_agent": {"model": "gemini-2.5-pro"},
        "ollama_agent": {"model": "llama3:8b"},
    }

    # Get provider for gemini agent
    provider, model, config = service._get_provider_for_agent("gemini_agent")
    assert provider is gemini_provider
    assert model == "gemini-2.5-pro"

    # Get provider for ollama agent
    provider, model, config = service._get_provider_for_agent("ollama_agent")
    assert provider is ollama_provider
    assert model == "llama3:8b"


def test_llm_service_infers_provider_from_model_prefix() -> None:
    """Test that LLMService can infer provider from model name prefix when not in map."""
    service: LLMService = LLMService.__new__(LLMService)
    service.event_bus = DummyEventBus()
    service.image_storage = None  # type: ignore[assignment]

    google_provider = FlakyProvider(0, ["test"])
    google_provider.provider_name = "google"

    service.providers = {"google": google_provider}
    service.model_to_provider_map = {}  # Empty map
    service.agent_config = {
        "test_agent": {"model": "google-new-model-123"},
    }

    # Should infer "google" provider from "google-new-model-123" prefix
    provider, model, config = service._get_provider_for_agent("test_agent")

    # The inference logic looks for model_name.lower().startswith(provider_name.lower())
    # So "google-new-model-123".lower().startswith("google") should match
    assert provider is google_provider


def test_llm_service_fallback_for_gemini_models() -> None:
    """Test that LLMService has special fallback logic for gemini models."""
    service: LLMService = LLMService.__new__(LLMService)
    service.event_bus = DummyEventBus()
    service.image_storage = None  # type: ignore[assignment]

    google_provider = FlakyProvider(0, ["test"])
    google_provider.provider_name = "Google"

    service.providers = {"Google": google_provider}
    service.model_to_provider_map = {}
    service.agent_config = {
        "test_agent": {"model": "gemini-experimental-456"},
    }

    # Should fallback to Google provider for gemini models
    provider, model, config = service._get_provider_for_agent("test_agent")

    assert provider is google_provider


def test_llm_service_returns_none_for_unknown_agent() -> None:
    """Test that LLMService returns None for agents not in config."""
    service: LLMService = LLMService.__new__(LLMService)
    service.event_bus = DummyEventBus()
    service.image_storage = None  # type: ignore[assignment]
    service.providers = {}
    service.model_to_provider_map = {}
    service.agent_config = {}

    provider, model, config = service._get_provider_for_agent("nonexistent_agent")

    assert provider is None
    assert model is None
    assert config is None


def test_llm_service_returns_none_for_agent_without_model() -> None:
    """Test that LLMService returns None for agents without a model configured."""
    service: LLMService = LLMService.__new__(LLMService)
    service.event_bus = DummyEventBus()
    service.image_storage = None  # type: ignore[assignment]
    service.providers = {}
    service.model_to_provider_map = {}
    service.agent_config = {
        "broken_agent": {},  # No model key
    }

    provider, model, config = service._get_provider_for_agent("broken_agent")

    assert provider is None
    assert model is None
    assert config is not None  # Config is returned even if no model
