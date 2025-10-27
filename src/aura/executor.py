import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.action import Action, ActionType
from src.aura.models.project_context import ProjectContext
from src.aura.models.result import Result
from src.aura.models.events import Event
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.ast_service import ASTService
from src.aura.services.context_retrieval_service import ContextRetrievalService
from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class AuraExecutor:
    """Execution layer: performs work without deciding what to do.

    Responsibilities:
    - Execute a given Action using prompts, AST/context services, and LLM providers.
    - Stream intermediate results to the UI via EventBus.
    - Route generated code through the validation pipeline where applicable.
    - Never decides which Action to run; that’s the Brain’s job.
    """

    def __init__(
        self,
        event_bus: EventBus,
        llm: LLMService,
        prompts: PromptManager,
        ast: ASTService,
        context: ContextRetrievalService,
    ) -> None:
        self.event_bus = event_bus
        self.llm = llm
        self.prompts = prompts
        self.ast = ast
        self.context = context
        self._tools = {
            ActionType.DESIGN_BLUEPRINT: self.execute_design_blueprint,
            ActionType.REFINE_CODE: self.execute_refine_code,
            ActionType.SIMPLE_REPLY: self.execute_simple_reply,
        }

    # --------------- Public API ---------------
    def execute(self, action: Action, project_context: ProjectContext) -> Any:
        tool = self._tools.get(action.type)
        if not tool:
            logger.warning("Unsupported action type requested: %s", action.type)
            return Result(ok=False, kind="unknown", error="Unsupported action type", data={})
        return tool(action, project_context)

    # --------------- Workflows ---------------
    def execute_simple_reply(self, action: Action, ctx: ProjectContext) -> str:
        user_text = action.get_param("request", "")
        history = ctx.conversation_history or []
        recent_history = history[-6:] if history else []

        prompt = self.prompts.render(
            "chitchat_reply.jinja2",
            user_text=user_text,
            conversation_history=recent_history,
        )
        if not prompt:
            raise RuntimeError("Failed to render chitchat prompt")

        try:
            stream = self.llm.stream_chat_for_agent("lead_companion_agent", prompt)
        except Exception as exc:
            logger.error("Streaming chitchat reply failed: %s", exc, exc_info=True)
            raise RuntimeError("Failed to stream conversational reply.") from exc

        chunks: List[str] = []
        try:
            for chunk in stream:
                if chunk:
                    chunks.append(chunk)
        except Exception as exc:
            logger.error("Error while gathering chitchat stream: %s", exc, exc_info=True)
            raise RuntimeError("Failed to gather conversational reply stream.") from exc

        reply_text = self._strip_code_fences("".join(chunks))
        if not reply_text:
            logger.warning("Chitchat model returned an empty reply.")
            raise RuntimeError("Chitchat model returned empty reply")
        return reply_text

    def execute_design_blueprint(self, action: Action, ctx: ProjectContext) -> Dict[str, Any]:
        user_text = action.get_param("request", "")
        prompt = self.prompts.render("architect.jinja2", user_text=user_text)
        if not prompt:
            raise RuntimeError("Failed to render architect prompt")

        response = self.llm.run_for_agent("architect_agent", prompt)
        data = self._parse_json_safely(response)
        if not self._blueprint_has_files(data):
            raise RuntimeError("Architect returned no files in blueprint")

        return data

    def execute_refine_code(self, action: Action, ctx: ProjectContext) -> Result:
        file_path = action.get_param("file_path", "workspace/generated.py")
        request_text = action.get_param("request", "")

        # Read current source if available
        source_code = ""
        full_path = self._resolve_to_project_path(file_path)
        try:
            if full_path:
                with open(full_path, "r", encoding="utf-8") as f:
                    source_code = f.read()
        except Exception:
            source_code = ""

        prompt = self.prompts.render(
            "engineer.jinja2",
            file_path=file_path,
            user_request=request_text,
            source_code=source_code,
            spec=None,
            context_files=[],
            parent_class_name=None,
            parent_class_source=None,
        )
        if not prompt:
            return Result(ok=False, kind="code", error="Failed to render engineer prompt", data={})

        # Stream and final dispatch without validation (legacy fast-lane)
        self._stream_and_finalize(prompt, "engineer_agent", file_path, validate_with_spec=None)
        return Result(ok=True, kind="code", data={"file_path": file_path})

    # --------------- Internals ---------------
    def execute_generate_code_for_spec(self, spec: Dict[str, Any], user_request: str) -> Dict[str, Any]:
        file_path = spec.get("file_path") or "workspace/generated.py"
        description = spec.get("description") or user_request or f"Implement the file {file_path}."

        try:
            self.event_bus.dispatch(Event(
                event_type="DISPATCH_TASK",
                payload={
                    "task_id": None,
                    "task_description": description,
                },
            ))
        except Exception:
            logger.debug("Failed to dispatch DISPATCH_TASK event for %s", file_path, exc_info=True)

        # Gather AST/RAG context
        context_data = self.context.get_context_for_task(description, file_path)

        # Parent class lookup if applicable
        parent_class_name = None
        parent_class_source = None
        if isinstance(spec, dict):
            parent_class_name = (
                spec.get("parent_class")
                or spec.get("base_class")
                or spec.get("inherits_from")
                or spec.get("extends")
            )
        if parent_class_name:
            try:
                parent_path = self.ast.find_class_file_path(parent_class_name) if hasattr(self.ast, "find_class_file_path") else None
                if parent_path:
                    # Reuse private util on context service to read
                    parent_class_source = self.context._read_file_content(parent_path) or None
            except Exception:
                parent_class_source = None

        current_source = self.context._read_file_content(file_path) or ""
        prompt = self.prompts.render(
            "engineer.jinja2",
            file_path=file_path,
            user_request=description,
            source_code=current_source,
            spec=spec,
            context_files=context_data,
            parent_class_name=parent_class_name,
            parent_class_source=parent_class_source,
        )
        if not prompt:
            self._handle_error("Failed to render engineer prompt for spec.")
            return {"file_path": file_path, "status": "prompt_error"}
        # Enforce validation-first pipeline for blueprint-driven tasks
        self._stream_and_finalize(prompt, "engineer_agent", file_path, validate_with_spec=spec)
        return {"file_path": file_path}

    def _stream_and_finalize(self, prompt: str, agent_name: str, file_path: str, validate_with_spec: Optional[Dict[str, Any]]):
        def run():
            try:
                logger.info(f"Streaming generation for {file_path} via {agent_name}")
                stream = self.llm.stream_chat_for_agent(agent_name, prompt)
                full_parts: List[str] = []
                for chunk in stream:
                    try:
                        self.event_bus.dispatch(Event(
                            event_type="CODE_CHUNK_GENERATED",
                            payload={"file_path": file_path, "chunk": chunk or ""},
                        ))
                    except Exception:
                        logger.warning("Failed to dispatch CODE_CHUNK_GENERATED; continuing.", exc_info=True)
                    if chunk:
                        full_parts.append(chunk)

                full_text = "".join(full_parts)
                if full_text.startswith("ERROR:"):
                    self._handle_error(full_text)
                    return

                code = self._sanitize_code(full_text)
                if validate_with_spec:
                    # Validation-first pipeline
                    self.event_bus.dispatch(Event(
                        event_type="VALIDATE_CODE",
                        payload={
                            "task_id": None,
                            "file_path": file_path,
                            "spec": validate_with_spec,
                            "generated_code": code,
                        },
                    ))
                else:
                    # Legacy fast-lane
                    self.event_bus.dispatch(Event(event_type="CODE_GENERATED", payload={"file_path": file_path, "code": code}))
            except Exception as e:
                logger.error(f"Generation error for {file_path}: {e}", exc_info=True)
                self._handle_error(f"A critical error occurred while generating code for {file_path}.")

        t = threading.Thread(target=run, daemon=True)
        t.start()

    def _parse_json_safely(self, text: str) -> Dict[str, Any]:
        try:
            return json.loads(self._strip_code_fences(text))
        except Exception:
            return {}

    @staticmethod
    def _files_from_blueprint(blueprint_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        if isinstance(blueprint_data.get("files"), list):
            files = [f for f in blueprint_data.get("files", []) if isinstance(f, dict)]
        elif isinstance(blueprint_data.get("blueprint"), dict):
            for file_path, spec in (blueprint_data.get("blueprint") or {}).items():
                if isinstance(spec, dict):
                    item = {"file_path": file_path}
                    item.update(spec)
                    files.append(item)
        return files

    @staticmethod
    def _blueprint_has_files(blueprint_data: Any) -> bool:
        if not isinstance(blueprint_data, dict):
            return False
        files = blueprint_data.get("files")
        if isinstance(files, list) and len([f for f in files if isinstance(f, dict)]) > 0:
            return True
        bp = blueprint_data.get("blueprint")
        if isinstance(bp, dict) and len(bp.keys()) > 0:
            return True
        return False

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"^```\w*\s*\n?", "", t, flags=re.MULTILINE)
        t = re.sub(r"\n?```\s*$", "", t, flags=re.MULTILINE)
        return t.strip()

    @staticmethod
    def _sanitize_code(code: str) -> str:
        code = re.sub(r"^```\w*\s*\n?", "", code, flags=re.MULTILINE)
        code = re.sub(r"\n?```\s*$", "", code, flags=re.MULTILINE)
        code = code.replace("```", "")
        return code.strip()

    @staticmethod
    def _handle_error(message: str) -> None:
        logger.error(message)

    def _resolve_to_project_path(self, file_path: str) -> Optional[str]:
        try:
            if re.match(r"^[a-zA-Z]:\\", file_path) or file_path.startswith("/"):
                return file_path
            project_root = getattr(self.ast, "project_root", "") or ""
            if not project_root:
                return None
            import os
            return os.path.join(project_root, file_path)
        except Exception:
            return None

