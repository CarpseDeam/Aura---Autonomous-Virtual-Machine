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
from src.aura.services.workspace_service import WorkspaceService
from src.aura.services.llm_service import LLMService
from src.aura.services.research_service import ResearchService


logger = logging.getLogger(__name__)


class AuraExecutor:
    """Execution layer: performs work without deciding what to do.

    Responsibilities:
    - Execute a given Action using prompts, AST/context services, and LLM providers.
    - Stream intermediate results to the UI via EventBus.
    - Route generated code through the validation pipeline where applicable.
    - Never decides which Action to run; that’s the Brain’s job.
    """

    _MAX_EXISTING_FILES_FOR_PROMPT = 200
    _DEFAULT_PROJECT_NAME = "default_project"

    def __init__(
        self,
        event_bus: EventBus,
        llm: LLMService,
        prompts: PromptManager,
        ast: ASTService,
        context: ContextRetrievalService,
        workspace: WorkspaceService,
    ) -> None:
        self.event_bus = event_bus
        self.llm = llm
        self.prompts = prompts
        self.ast = ast
        self.context = context
        self.workspace = workspace
        self.research_service = ResearchService()
        self._current_generation_mode: str = "create"
        self._current_project_name: Optional[str] = None
        self._current_project_files: List[str] = []
        self._tools = {
            ActionType.DESIGN_BLUEPRINT: self.execute_design_blueprint,
            ActionType.REFINE_CODE: self.execute_refine_code,
            ActionType.SIMPLE_REPLY: self.execute_simple_reply,
            ActionType.RESEARCH: self.execute_research,
            ActionType.LIST_FILES: self.execute_list_files,
            ActionType.READ_FILE: self.execute_read_file,
            ActionType.WRITE_FILE: self.execute_write_file,
        }

    # --------------- Public API ---------------
    def execute(self, action: Action, project_context: ProjectContext) -> Any:
        tool = self._tools.get(action.type)
        if not tool:
            logger.warning("Unsupported action type requested: %s", action.type)
            return Result(ok=False, kind="unknown", error="Unsupported action type", data={})
        return tool(action, project_context)

    # --------------- Workflows ---------------
    def execute_research(self, action: Action, ctx: ProjectContext) -> Dict[str, Any]:
        topic = action.get_param("topic") or action.get_param("subject") or action.get_param("request", "")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("Missing 'topic' parameter for research action")
        return self.research_service.research(topic.strip())

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

        # Detect any images attached to the latest user message
        latest_user_message = next(
            (msg for msg in reversed(recent_history or []) if (msg or {}).get("role") == "user"),
            None,
        )
        attachments = []
        if latest_user_message:
            attachments = list((latest_user_message or {}).get("images") or [])

        prompt_payload = {"text": prompt, "images": attachments} if attachments else prompt

        try:
            stream = self.llm.stream_chat_for_agent("lead_companion_agent", prompt_payload)
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
        generation_context = self._determine_generation_context(user_text, ctx)
        prompt = self.prompts.render(
            "architect.jinja2",
            user_text=user_text,
            generation_mode=generation_context["mode"],
            target_project=generation_context.get("project_name"),
            existing_files=generation_context.get("existing_files", []),
        )
        if not prompt:
            raise RuntimeError("Failed to render architect prompt")

        try:
            self.event_bus.dispatch(Event(
                event_type="GENERATION_PROGRESS",
                payload={"message": "Planning file structure...", "category": "SYSTEM"},
            ))
        except Exception:
            logger.debug("Failed to dispatch planning progress event.", exc_info=True)

        response = self.llm.run_for_agent("architect_agent", prompt)
        data = self._parse_json_safely(response)
        if isinstance(data, dict):
            data["_aura_mode"] = generation_context["mode"]
            if generation_context["mode"] == "edit" and generation_context.get("project_name"):
                target_name = generation_context["project_name"]
                data.setdefault("project_name", target_name)
                data.setdefault("project_slug", target_name)
        self._activate_project_from_blueprint(data if isinstance(data, dict) else {}, generation_context, user_text)
        if not self._blueprint_has_files(data):
            raise RuntimeError("Architect returned no files in blueprint")

        files = self._files_from_blueprint(data)
        try:
            self.event_bus.dispatch(Event(
                event_type="GENERATION_PROGRESS",
                payload={"message": f"Blueprint ready: {len(files)} file{'s' if len(files) != 1 else ''}", "category": "SYSTEM"},
            ))
        except Exception:
            logger.debug("Failed to dispatch blueprint progress event.", exc_info=True)

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

        project_files = []
        try:
            project_files = self.workspace.get_project_files()[: self._MAX_EXISTING_FILES_FOR_PROMPT]
        except Exception:
            project_files = []

        prompt = self.prompts.render(
            "engineer.jinja2",
            file_path=file_path,
            user_request=request_text,
            source_code=source_code,
            spec=None,
            context_files=[],
            parent_class_name=None,
            parent_class_source=None,
            generation_mode="edit",
            existing_project=getattr(self.workspace, "active_project", None),
            file_already_exists=bool(source_code),
            project_file_index=project_files,
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
        file_already_exists = self.workspace.file_exists(file_path)

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

        workspace_source = self.workspace.get_file_content(file_path)
        current_source = workspace_source if workspace_source is not None else self.context._read_file_content(file_path) or ""
        prompt = self.prompts.render(
            "engineer.jinja2",
            file_path=file_path,
            user_request=description,
            source_code=current_source,
            spec=spec,
            context_files=context_data,
            parent_class_name=parent_class_name,
            parent_class_source=parent_class_source,
            generation_mode=self._current_generation_mode,
            existing_project=self._current_project_name,
            file_already_exists=file_already_exists,
            project_file_index=self._current_project_files,
        )
        if not prompt:
            self._handle_error("Failed to render engineer prompt for spec.")
            return {"file_path": file_path, "status": "prompt_error"}
        # Enforce validation-first pipeline for blueprint-driven tasks
        self._stream_and_finalize(prompt, "engineer_agent", file_path, validate_with_spec=spec)
        return {"file_path": file_path}

    def execute_list_files(self, action: Action, ctx: ProjectContext) -> List[str]:
        project_path = getattr(self.workspace, "active_project_path", None)
        if not project_path:
            raise RuntimeError("No active project set")

        files: List[str] = []
        try:
            for path in project_path.rglob("*"):
                if path.is_file():
                    files.append(str(path.relative_to(project_path)))
        except Exception as exc:
            logger.error("Failed to list files for project %s: %s", project_path, exc, exc_info=True)
            raise RuntimeError("Failed to list files for active project") from exc
        return files

    def execute_read_file(self, action: Action, ctx: ProjectContext) -> str:
        file_path = action.get_param("file_path")
        if not file_path:
            raise ValueError("Missing 'file_path' parameter for read_file action")

        project_path = getattr(self.workspace, "active_project_path", None)
        if not project_path:
            raise RuntimeError("No active project set")

        project_root = project_path.resolve()
        target = (project_path / file_path).resolve()
        try:
            target.relative_to(project_root)
        except ValueError:
            raise RuntimeError("Attempted to read outside the active project") from None
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            return target.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to read file %s: %s", target, exc, exc_info=True)
            raise RuntimeError(f"Failed to read file: {file_path}") from exc

    def execute_write_file(self, action: Action, ctx: ProjectContext) -> Dict[str, Any]:
        file_path = action.get_param("file_path")
        content = action.get_param("content", "")

        if not file_path:
            raise ValueError("Missing 'file_path' parameter for write_file action")

        try:
            self.workspace.save_code_to_project(file_path, content)
        except Exception as exc:
            logger.error("Failed to write file %s: %s", file_path, exc, exc_info=True)
            raise

        return {"file_path": file_path, "status": "written"}

    def _determine_generation_context(self, user_text: str, ctx: ProjectContext) -> Dict[str, Any]:
        projects = self.workspace.list_workspace_projects()
        matched_project = self._match_project_name(user_text, projects)

        if not matched_project:
            matched_project = self._match_project_from_context(ctx, projects, user_text)

        if matched_project:
            self._ensure_active_project(matched_project)
            try:
                available_files = self.workspace.get_project_files()
            except Exception:
                available_files = []

            limited_files = available_files[: self._MAX_EXISTING_FILES_FOR_PROMPT]
            self._current_generation_mode = "edit"
            self._current_project_name = matched_project
            self._current_project_files = limited_files
            return {
                "mode": "edit",
                "project_name": matched_project,
                "existing_files": limited_files,
            }

        self._current_generation_mode = "create"
        self._current_project_name = None
        self._current_project_files = []
        return {
            "mode": "create",
            "project_name": None,
            "existing_files": [],
        }

    def _match_project_from_context(
        self,
        ctx: ProjectContext,
        projects: List[Dict[str, Any]],
        user_text: str,
    ) -> Optional[str]:
        if not ctx:
            return None
        active_project = (ctx.active_project or "").strip()
        if not active_project:
            return None
        if not self._project_exists(active_project, projects):
            return None
        if active_project == self._DEFAULT_PROJECT_NAME:
            return None

        if self._looks_like_edit_request(user_text):
            return active_project

        non_default_projects = [
            (project or {}).get("name")
            for project in projects
            if (project or {}).get("name") and (project or {}).get("name") != self._DEFAULT_PROJECT_NAME
        ]
        if len(non_default_projects) == 1 and non_default_projects[0] == active_project:
            if not self._looks_like_creation_request(user_text):
                return active_project
        return None

    def _match_project_name(self, user_text: str, projects: List[Dict[str, Any]]) -> Optional[str]:
        if not user_text:
            return None

        raw_lower = user_text.lower()
        normalized_request = self._normalize_for_match(user_text)
        collapsed_request = normalized_request.replace(" ", "")

        for entry in projects:
            name = (entry or {}).get("name")
            if not name:
                continue
            slug = name.lower()
            if slug and slug in raw_lower:
                return name

            normalized_name = self._normalize_for_match(name)
            if normalized_name and normalized_name in normalized_request:
                return name

            collapsed_name = normalized_name.replace(" ", "") if normalized_name else ""
            if collapsed_name and collapsed_name in collapsed_request:
                return name

        return None

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        return " ".join(tokens)

    @staticmethod
    def _looks_like_edit_request(user_text: str) -> bool:
        tokens = set(AuraExecutor._normalize_for_match(user_text).split())
        edit_keywords = {
            "add",
            "update",
            "modify",
            "change",
            "enhance",
            "extend",
            "fix",
            "improve",
            "refine",
            "refactor",
            "patch",
        }
        return bool(tokens.intersection(edit_keywords))

    @staticmethod
    def _looks_like_creation_request(user_text: str) -> bool:
        tokens = set(AuraExecutor._normalize_for_match(user_text).split())
        creation_keywords = {
            "create",
            "build",
            "generate",
            "start",
            "scaffold",
            "bootstrap",
            "make",
            "init",
            "initialize",
            "launch",
            "new",
        }
        return bool(tokens.intersection(creation_keywords))

    @staticmethod
    def _project_exists(project_name: str, projects: List[Dict[str, Any]]) -> bool:
        for entry in projects:
            if (entry or {}).get("name") == project_name:
                return True
        return False

    def _ensure_active_project(self, project_name: str) -> None:
        if not project_name:
            return
        if getattr(self.workspace, "active_project", None) == project_name:
            return
        try:
            self.workspace.set_active_project(project_name)
        except Exception as exc:
            logger.error("Failed to activate project '%s': %s", project_name, exc, exc_info=True)

    def _activate_project_from_blueprint(
        self,
        blueprint: Dict[str, Any],
        generation_context: Dict[str, Any],
        user_text: str,
    ) -> None:
        if generation_context["mode"] == "edit":
            return

        project_label: Optional[str] = None
        if isinstance(blueprint, dict):
            for key in ("project_slug", "project_name", "slug", "name"):
                value = blueprint.get(key)
                if isinstance(value, str) and value.strip():
                    project_label = value.strip()
                    break

        if not project_label:
            project_label = user_text.strip()

        project_slug = self._to_project_slug(project_label)
        project_slug = self._ensure_unique_slug(project_slug)

        try:
            self.workspace.set_active_project(project_slug)
        except Exception as exc:
            logger.error("Failed to activate project '%s' from blueprint: %s", project_slug, exc, exc_info=True)
            return

        self._current_project_name = project_slug
        self._current_project_files = []

        if isinstance(blueprint, dict):
            blueprint.setdefault("project_slug", project_slug)
            blueprint.setdefault("project_name", project_label)

    def _ensure_unique_slug(self, slug: str) -> str:
        existing_names = {
            (entry or {}).get("name")
            for entry in self.workspace.list_workspace_projects()
        }
        existing_names.discard(None)

        if slug not in existing_names:
            return slug

        counter = 2
        candidate = f"{slug}-{counter}"
        while candidate in existing_names:
            counter += 1
            candidate = f"{slug}-{counter}"
        return candidate

    @staticmethod
    def _to_project_slug(text: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        slug = "-".join(tokens)
        return slug or "project"

    def _stream_and_finalize(self, prompt: str, agent_name: str, file_path: str, validate_with_spec: Optional[Dict[str, Any]]):
        def run():
            try:
                logger.info(f"Streaming generation for {file_path} via {agent_name}")
                try:
                    self.event_bus.dispatch(Event(
                        event_type="GENERATION_PROGRESS",
                        payload={"message": f"Generating {file_path}...", "category": "SYSTEM"},
                    ))
                except Exception:
                    logger.debug("Failed to dispatch generation start progress event.", exc_info=True)

                stream = self.llm.stream_chat_for_agent(agent_name, prompt)
                full_parts: List[str] = []
                for chunk in stream:
                    if chunk:
                        full_parts.append(chunk)

                full_text = "".join(full_parts)
                if full_text.startswith("ERROR:"):
                    self._handle_error(full_text)
                    return

                code = self._sanitize_code(full_text)
                line_count = len(code.splitlines()) if code else 0

                try:
                    self.event_bus.dispatch(Event(
                        event_type="GENERATION_PROGRESS",
                        payload={"message": f"Drafted {file_path} ({line_count} lines)", "category": "SUCCESS"},
                    ))
                except Exception:
                    logger.debug("Failed to dispatch generation completion progress event.", exc_info=True)

                if validate_with_spec:
                    try:
                        self.event_bus.dispatch(Event(
                            event_type="GENERATION_PROGRESS",
                            payload={"message": f"Validating {file_path}...", "category": "SYSTEM"},
                        ))
                    except Exception:
                        logger.debug("Failed to dispatch validation progress event.", exc_info=True)

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
                    self.event_bus.dispatch(Event(
                        event_type="CODE_GENERATED",
                        payload={"file_path": file_path, "code": code},
                    ))
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
        if not isinstance(blueprint_data, dict):
            return files

        project_metadata: Dict[str, Any] = {}
        for key in ("project_slug", "project_name", "slug", "name"):
            value = blueprint_data.get(key)
            if isinstance(value, str) and value.strip():
                project_metadata[key] = value.strip()

        def _augment_spec(raw_spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if not isinstance(raw_spec, dict):
                return None
            spec_copy: Dict[str, Any] = dict(raw_spec)
            if "file_path" not in spec_copy or not isinstance(spec_copy["file_path"], str):
                return None
            for meta_key, meta_value in project_metadata.items():
                spec_copy.setdefault(meta_key, meta_value)
            return spec_copy

        file_entries = blueprint_data.get("files")
        if isinstance(file_entries, list):
            for entry in file_entries:
                spec_copy = _augment_spec(entry)
                if spec_copy:
                    files.append(spec_copy)
            return files

        blueprint_entries = blueprint_data.get("blueprint")
        if isinstance(blueprint_entries, dict):
            for file_path, spec in blueprint_entries.items():
                if not isinstance(file_path, str):
                    continue
                spec_copy = dict(spec) if isinstance(spec, dict) else {}
                spec_copy["file_path"] = file_path
                augmented = _augment_spec(spec_copy)
                if augmented:
                    files.append(augmented)
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

