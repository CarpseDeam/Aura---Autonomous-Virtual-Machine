import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.ast_service import ASTService
from src.aura.services.llm_service import LLMService
from src.aura.services.task_management_service import TaskManagementService


logger = logging.getLogger(__name__)


class _OrchestrationWorker(QObject):
    """
    Worker that executes long-running orchestration logic off the UI thread.

    Emits:
    - event_ready(Event): for events destined to the central EventBus
    - error(str): for recoverable errors to display in the UI
    - finished(): when work is done
    """

    event_ready = Signal(Event)
    error = Signal(str)
    finished = Signal()

    def __init__(
        self,
        user_text: str,
        llm: LLMService,
        ast: ASTService,
        prompts: PromptManager,
    ) -> None:
        super().__init__()
        self.user_text = user_text
        self.llm = llm
        self.ast = ast
        self.prompts = prompts

    @Slot()
    def run(self) -> None:
        """Entry point for the worker thread."""
        try:
            command = self._cognitive_route(self.user_text)
            action = (command or {}).get("action")
            params = (command or {}).get("params") or {}

            if action == "design_blueprint":
                self._run_design_blueprint(self.user_text)
            elif action == "refine_code":
                file_path = params.get("file_path") or "workspace/generated.py"
                request_text = params.get("request") or self.user_text
                self._run_refine_code(file_path, request_text)
            else:
                # Default to refine_code as a safe fallback
                self._run_refine_code("workspace/generated.py", self.user_text)
        except Exception as e:
            logger.error(f"Orchestration worker error: {e}", exc_info=True)
            self.error.emit("A critical error occurred while processing your request.")
        finally:
            self.finished.emit()

    # ---------- Router ----------
    def _cognitive_route(self, user_text: str) -> Dict[str, Any]:
        prompt = self.prompts.render("lead_companion.jinja2", user_text=user_text)
        if not prompt:
            # Fallback: simple heuristic
            return self._fallback_route(user_text)
        raw = self.llm.run_for_agent("cognitive_router", prompt)
        clean = OrchestrationService._strip_code_fences_static(raw)
        try:
            data = json.loads(clean)
            return data if isinstance(data, dict) else self._fallback_route(user_text)
        except Exception:
            return self._fallback_route(user_text)

    def _fallback_route(self, user_text: str) -> Dict[str, Any]:
        if any(kw in user_text.lower() for kw in ["new project", "blueprint", "plan", "design"]):
            return {"action": "design_blueprint", "params": {"request": user_text}}
        return {"action": "refine_code", "params": {"file_path": "workspace/generated.py", "request": user_text}}

    # ---------- Workflows ----------
    def _run_design_blueprint(self, user_text: str) -> None:
        prompt = self.prompts.render("architect.jinja2", user_text=user_text)
        if not prompt:
            self.error.emit("Failed to render architect prompt.")
            return
        response = self.llm.run_for_agent("architect_agent", prompt)
        clean = OrchestrationService._strip_code_fences_static(response)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            self.error.emit("Architect returned invalid JSON. Please clarify and try again.")
            return

        # Guardian Protocol: ensure blueprint is non-empty and contains files to build
        if not OrchestrationService._blueprint_has_files(data):
            self.error.emit("Architect failed to produce a valid plan: no files found in blueprint.")
            return

        # Emit concise blueprint summary
        self.event_ready.emit(Event(event_type="BLUEPRINT_GENERATED", payload=data))

        # Convert blueprint into task events
        for ev in OrchestrationService._events_from_blueprint(data):
            self.event_ready.emit(ev)

        # Auto-approve blueprint for now
        self.event_ready.emit(Event(event_type="BLUEPRINT_APPROVED", payload={"blueprint": data}))

    def _run_refine_code(self, file_path: str, request_text: str) -> None:
        source_code = ""
        full_path = self._resolve_to_project_path(file_path)
        try:
            if full_path and os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    source_code = f.read()
        except Exception:
            source_code = ""

        prompt = self.prompts.render(
            "engineer.jinja2",
            file_path=file_path,
            user_request=request_text,
            source_code=source_code,
        )
        if not prompt:
            self.error.emit("Failed to render engineer prompt.")
            return
        raw = self.llm.run_for_agent("engineer_agent", prompt)
        code = OrchestrationService._sanitize_code_static(raw)
        if not code:
            self.error.emit("Engineer returned empty code.")
            return
        self.event_ready.emit(Event(event_type="CODE_GENERATED", payload={"file_path": file_path, "code": code}))

    def _resolve_to_project_path(self, file_path: str) -> Optional[str]:
        try:
            if os.path.isabs(file_path):
                return file_path
            project_root = getattr(self.ast, "project_root", "") or ""
            if not project_root:
                return None
            return os.path.join(project_root, file_path)
        except Exception:
            return None


class OrchestrationService:
    """
    Non-blocking, thread-based command center for user requests.

    - Spawns a QObject worker on a QThread for each user message.
    - Simple cognitive router selects: design_blueprint or refine_code.
    - Emits events back to the main thread via Qt signals -> EventBus.
    """

    def __init__(
        self,
        event_bus: EventBus,
        llm_service: LLMService,
        ast_service: ASTService,
        prompt_manager: PromptManager,
        task_management_service: Optional[TaskManagementService] = None,
    ) -> None:
        self.event_bus = event_bus
        self.llm = llm_service
        self.ast = ast_service
        self.prompts = prompt_manager
        self.task_management_service = task_management_service

        # Keep references to threads/workers to avoid premature GC
        self._threads: List[QThread] = []
        self._workers: List[_OrchestrationWorker] = []

        self._register_event_handlers()
        logger.info("OrchestrationService initialized and ready.")

    def _register_event_handlers(self) -> None:
        self.event_bus.subscribe("SEND_USER_MESSAGE", self._handle_user_message)

    def _handle_user_message(self, event: Event) -> None:
        """Create a worker, move it to a QThread, and start it (non-blocking)."""
        user_text = (event.payload or {}).get("text", "").strip()
        if not user_text:
            self._handle_error("Empty user request received.")
            return

        thread = QThread()
        worker = _OrchestrationWorker(user_text, self.llm, self.ast, self.prompts)
        worker.moveToThread(thread)

        # Wire signals back to the main thread
        worker.event_ready.connect(self._dispatch_event)
        worker.error.connect(self._handle_error)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_thread(thread, worker))
        thread.started.connect(worker.run)

        # Track and start
        self._threads.append(thread)
        self._workers.append(worker)
        thread.start()

    def _cleanup_thread(self, thread: QThread, worker: _OrchestrationWorker) -> None:
        try:
            if worker in self._workers:
                self._workers.remove(worker)
        except ValueError:
            pass
        try:
            if thread in self._threads:
                self._threads.remove(thread)
        except ValueError:
            pass
        thread.deleteLater()
        worker.deleteLater()

    def _dispatch_event(self, ev: Event) -> None:
        self.event_bus.dispatch(ev)

    # -------- Static helpers used by worker --------
    @staticmethod
    def _events_from_blueprint(blueprint_data: Dict[str, Any]) -> List[Event]:
        """Return a list of ADD_TASK events derived from a blueprint."""
        events: List[Event] = []
        files: List[Dict[str, Any]] = []
        if isinstance(blueprint_data.get("files"), list):
            files = [f for f in blueprint_data.get("files", []) if isinstance(f, dict)]
        elif isinstance(blueprint_data.get("blueprint"), dict):
            for file_path, spec in (blueprint_data.get("blueprint") or {}).items():
                if isinstance(spec, dict):
                    item = {"file_path": file_path}
                    item.update(spec)
                    files.append(item)

        for f in files:
            file_path = f.get("file_path") or "workspace/generated.py"
            imports_required = f.get("imports_required") or []
            # Functions
            for func in (f.get("functions") or []):
                desc = func.get("description") or f"Implement function {func.get('function_name')}"
                payload = {
                    "description": f"{desc} in {file_path}",
                    "spec": {
                        "file_path": file_path,
                        "function_name": func.get("function_name"),
                        "signature": func.get("signature"),
                        "description": desc,
                        "imports_required": func.get("imports_required") or imports_required,
                    },
                    "dependencies": f.get("dependencies") or [],
                }
                events.append(Event(event_type="ADD_TASK", payload=payload))
            # Classes / Methods
            for cls in (f.get("classes") or []):
                class_name = cls.get("class_name") or cls.get("name")
                for method in (cls.get("methods") or []):
                    desc = method.get("description") or f"Implement method {method.get('method_name')}"
                    payload = {
                        "description": f"{desc} in {file_path}::{class_name}",
                        "spec": {
                            "file_path": file_path,
                            "class_name": class_name,
                            "method_name": method.get("method_name"),
                            "signature": method.get("signature"),
                            "description": desc,
                            "imports_required": method.get("imports_required") or imports_required,
                        },
                        "dependencies": f.get("dependencies") or [],
                    }
                    events.append(Event(event_type="ADD_TASK", payload=payload))
        return events

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
    def _strip_code_fences_static(text: str) -> str:
        t = text.strip()
        t = re.sub(r"^```\w*\s*\n?", "", t, flags=re.MULTILINE)
        t = re.sub(r"\n?```\s*$", "", t, flags=re.MULTILINE)
        return t.strip()

    @staticmethod
    def _sanitize_code_static(code: str) -> str:
        code = re.sub(r"^```\w*\s*\n?", "", code, flags=re.MULTILINE)
        code = re.sub(r"\n?```\s*$", "", code, flags=re.MULTILINE)
        code = code.replace("```", "")
        return code.strip()

    def _handle_error(self, message: str) -> None:
        logger.error(message)
        self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": message}))

