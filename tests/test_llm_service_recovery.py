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
        ast=DummyService(),  # type: ignore[arg-type]
        context=DummyService(),  # type: ignore[arg-type]
        workspace=DummyService(),  # type: ignore[arg-type]
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
        ast=DummyService(),  # type: ignore[arg-type]
        context=DummyService(),  # type: ignore[arg-type]
        workspace=DummyService(),  # type: ignore[arg-type]
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

    result = executor.execute_discuss(action, context)

    assert result == expected
