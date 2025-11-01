import os
from typing import Any, Dict, List, Optional

import pytest

from src.aura.executor import AuraExecutor
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.prompts.prototype_keywords import (
    PROTOTYPE_KEYWORDS,
    matches_prototype_request,
)
from src.aura.services.image_storage_service import ImageStorageService
from src.aura.services.llm_service import LLMService
from src.aura.models.exceptions import LLMServiceError


class StubEventBus:
    def __init__(self) -> None:
        self.events: List[Any] = []

    def dispatch(self, event: Any) -> None:
        self.events.append(event)

    def subscribe(self, event_type: str, callback: Any) -> None:  # pragma: no cover - compatibility shim
        return None


class StubASTService:
    def find_class_file_path(self, class_name: str) -> Optional[str]:
        return None


class StubContextRetrievalService:
    def get_context_for_task(self, description: str, file_path: str) -> List[Any]:
        return []

    def _read_file_content(self, path: str) -> str:
        return ""


class StubWorkspaceService:
    def __init__(self) -> None:
        self.active_project: Optional[str] = None
        self.saved: Optional[Dict[str, str]] = None

    def get_project_files(self) -> List[str]:
        return []

    def get_file_content(self, path: str) -> str:
        return ""

    def file_exists(self, path: str) -> bool:
        return False

    def list_workspace_projects(self) -> List[Dict[str, Any]]:
        return []

    def save_code_to_project(self, file_path: str, content: str) -> None:
        self.saved = {"file_path": file_path, "content": content}


def _build_executor() -> AuraExecutor:
    prompts = PromptManager()
    return AuraExecutor(
        event_bus=StubEventBus(),
        llm=object(),
        prompts=prompts,
        ast=StubASTService(),
        context=StubContextRetrievalService(),
        workspace=StubWorkspaceService(),
    )


def test_system_prompt_contains_required_sections() -> None:
    prompts = PromptManager()
    rendered = prompts.render("system_prompt.jinja2")
    assert "SECTION 0 - COMPANION FIRST" in rendered
    assert "SECTION 1 - NEVER BREAK PROD" in rendered
    for snippet in [
        "Only generate code when the user explicitly asks",
        "Use the `tenacity` library",
        "Prefer `async def` + `await` for all IO-bound workflows",
        "Never log secrets",
        "Provide pytest examples",
    ]:
        assert snippet in rendered


@pytest.mark.parametrize("keyword", PROTOTYPE_KEYWORDS)
def test_matches_prototype_request_detects_keywords(keyword: str) -> None:
    assert matches_prototype_request(f"Could you {keyword} of this idea?")


def test_matches_prototype_request_defaults_to_false() -> None:
    assert not matches_prototype_request("Please build a production-ready API client.")
    assert not matches_prototype_request(None)  # type: ignore[arg-type]


def test_executor_builds_messages_with_system_prompt_only() -> None:
    executor = _build_executor()
    executor.prompt_builder.update_prototype_mode("Build a durable event processor.")
    messages = executor._build_generation_messages("user prompt")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "SECTION 1 - NEVER BREAK PROD" in messages[0]["content"]
    assert messages[1]["role"] == "user"


def test_executor_builds_messages_with_prototype_override() -> None:
    executor = _build_executor()
    executor.prompt_builder.update_prototype_mode("Can you give me a quick prototype of the workflow?")
    messages = executor._build_generation_messages("user prompt")
    assert len(messages) == 3
    assert "SECTION 1 - NEVER BREAK PROD" in messages[0]["content"]
    assert "PROTOTYPE OVERRIDE - QUICK EXPERIMENTATION" in messages[1]["content"]
    assert messages[-1]["role"] == "user"


def test_execute_generate_code_for_spec_respects_prototype(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _build_executor()
    captured: Dict[str, Any] = {}

    def capture_stream(prompt: str, agent: str, file_path: str, validate_with_spec: Optional[Dict[str, Any]], *, prototype_override: Optional[bool] = None) -> None:
        captured["messages"] = executor._build_generation_messages(
            prompt,
            prototype_override=prototype_override,
        )

    monkeypatch.setattr(executor.code_generator, "_stream_and_finalize", capture_stream)

    spec: Dict[str, Any] = {"file_path": "workspace/api_client.py", "description": "Implements API client"}
    executor.execute_generate_code_for_spec(spec, "quick prototype of an async API fetch helper")

    messages = captured["messages"]
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert "PROTOTYPE OVERRIDE - QUICK EXPERIMENTATION" in messages[1]["content"]


@pytest.mark.integration
def test_integration_generation_includes_error_handling_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    if not os.getenv("AURA_RUN_LLM_INTEGRATION"):
        pytest.skip("Set AURA_RUN_LLM_INTEGRATION=1 to enable this integration test.")

    try:
        image_storage = ImageStorageService()
    except OSError as exc:  # pragma: no cover - depends on host environment
        pytest.skip(f"Image cache unavailable: {exc}")

    event_bus = StubEventBus()
    llm_service = LLMService(event_bus, image_storage)
    executor = AuraExecutor(
        event_bus=event_bus,
        llm=llm_service,
        prompts=PromptManager(),
        ast=StubASTService(),
        context=StubContextRetrievalService(),
        workspace=StubWorkspaceService(),
    )

    captured: Dict[str, str] = {}

    def sync_stream(prompt: str, agent: str, file_path: str, validate_with_spec: Optional[Dict[str, Any]], *, prototype_override: Optional[bool] = None) -> None:
        messages = executor._build_generation_messages(prompt, prototype_override=prototype_override)
        try:
            chunks = list(llm_service.stream_structured_for_agent(agent, messages))
        except LLMServiceError as exc:
            pytest.skip(f"LLM call failed: {exc}")
        code = executor._sanitize_code("".join(chunks))
        captured["code"] = code

    monkeypatch.setattr(executor.code_generator, "_stream_and_finalize", sync_stream)

    spec = {"file_path": "workspace/api_client.py", "description": "HTTP client wrapper"}
    executor.execute_generate_code_for_spec(spec, "Build a function to fetch data from an API")

    generated_code = captured.get("code", "")
    assert generated_code, "Integration LLM did not return code."
    normalized = generated_code.lower()
    assert "try" in normalized
    assert "except" in normalized
