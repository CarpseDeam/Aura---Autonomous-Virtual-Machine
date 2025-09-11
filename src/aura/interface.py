import logging
import json
import os
import re
from typing import Any, Dict, List

from PySide6.QtCore import QThreadPool

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext
from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.services.ast_service import ASTService
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.worker import BrainExecutorWorker


logger = logging.getLogger(__name__)


class AuraInterface:
    """Interface layer: the clean boundary for UI and events.

    Responsibilities:
    - Receive user input and build a ProjectContext snapshot.
    - Ask the Brain for a decision (Action).
    - Pass the Action to the Executor and let it stream/update via events.
    - Keep flow linear: Input → Brain → Executor → UI.
    """

    def __init__(
        self,
        event_bus: EventBus,
        brain: AuraBrain,
        executor: AuraExecutor,
        ast: ASTService,
        conversations: ConversationManagementService,
        workspace: WorkspaceService,
        thread_pool: QThreadPool,
    ) -> None:
        self.event_bus = event_bus
        self.brain = brain
        self.executor = executor
        self.ast = ast
        self.conversations = conversations
        self.workspace = workspace
        self.thread_pool = thread_pool
        # Default language until detection signals otherwise
        self.current_language: str = "python"

        self._register_event_handlers()

    def _register_event_handlers(self) -> None:
        self.event_bus.subscribe("SEND_USER_MESSAGE", self._handle_user_message)
        # Passive listener for detected language updates
        self.event_bus.subscribe("PROJECT_LANGUAGE_DETECTED", self._handle_project_language_detected)

    def _handle_project_language_detected(self, event: Event) -> None:
        """Update current language from WorkspaceService detection events."""
        try:
            lang = (event.payload or {}).get("language")
            if isinstance(lang, str) and lang:
                self.current_language = lang
                logger.info(f"Interface updated language -> {self.current_language}")
        except Exception:
            logger.debug("Failed to process PROJECT_LANGUAGE_DETECTED event; ignoring.")

    def _build_context(self) -> ProjectContext:
        active_project = self.workspace.active_project
        active_files = list(self.ast.project_index.keys()) if getattr(self.ast, "project_index", None) else []
        conversation_history = self.conversations.get_history()
        return ProjectContext(
            active_project=active_project,
            active_files=active_files,
            conversation_history=conversation_history,
        )

    def _handle_user_message(self, event: Event) -> None:
        user_text = (event.payload or {}).get("text", "").strip()
        if not user_text:
            self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Empty user request received."}))
            return

        # Run heavy logic on a background thread
        worker = BrainExecutorWorker(self, user_text)
        worker.signals.error.connect(lambda msg: self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": msg})))
        # finished signal available for potential UI hooks; no-op here
        worker.signals.finished.connect(lambda: None)
        self.thread_pool.start(worker)

    def _handle_user_message_logic(self, user_text: str) -> None:
        # Record conversation
        try:
            self.conversations.add_message("user", user_text)
        except Exception:
            logger.debug("Failed to append to conversation history; continuing.")

        # Smart Context Assembler pipeline
        try:
            ctx = self._build_context()

            # 1) Extract Intent (keywords) via cognitive_router
            extractor_prompt = (
                "Extract 3-8 short keywords or filenames from the user request. "
                "Return ONLY a comma-separated list with no extra text.\n\n"
                f"User request: {user_text}"
            )
            try:
                raw_keywords = self.brain.llm.run_for_agent("cognitive_router", extractor_prompt)  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning(f"Keyword extraction failed: {e}")
                raw_keywords = ""

            # Parse keywords
            cleaned = re.sub(r"```[\s\S]*?```", "", (raw_keywords or "")).strip()
            if "," in cleaned:
                kw_list = [k.strip() for k in cleaned.split(",") if k.strip()]
            else:
                kw_list = [k.strip() for k in re.split(r"[^A-Za-z0-9_./\\-]+", cleaned) if k.strip()]
            # Always include a fallback on the user text to seed semantic search
            query_text = ", ".join(kw_list[:8]) if kw_list else user_text[:256]

            # 2) Query Long-Term Memory (semantic AST)
            relevant_paths: List[str] = []
            try:
                relevant_paths = self.ast.search_semantic_context(query_text, k=6) or []
            except Exception as e:
                logger.warning(f"AST semantic search failed: {e}")
                relevant_paths = []

            # 3) Assemble the Context: read relevant files
            relevant_files: List[Dict[str, Any]] = []
            project_root = getattr(self.ast, "project_root", "") or ""
            for rel_path in relevant_paths:
                try:
                    abs_path = rel_path
                    if not (os.path.isabs(rel_path)) and project_root:
                        abs_path = os.path.join(project_root, rel_path)
                    if not os.path.exists(abs_path):
                        continue
                    with open(abs_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # Normalize to project-relative for the agent
                    rel_for_agent = rel_path if not os.path.isabs(rel_path) else os.path.relpath(rel_path, project_root) if project_root else rel_path
                    relevant_files.append({
                        "path": rel_for_agent.replace("\\", "/"),
                        "content": content,
                    })
                except Exception as e:
                    logger.debug(f"Failed reading {rel_path}: {e}")

            # Build project summary from stats and active project
            try:
                stats = self.ast.get_project_stats()
            except Exception:
                stats = {}
            project_summary = {
                "active_project": ctx.active_project,
                "stats": stats,
            }

            conversation_recent = (ctx.conversation_history or [])[-8:]
            jit_context = {
                "project_summary": project_summary,
                "relevant_files": relevant_files,
                "conversation_recent": conversation_recent,
                "user_request": user_text,
            }

            # 4) Call the Companion with rendered prompt
            try:
                companion_prompt = self.brain.prompts.render(
                    "companion.jinja2",
                    language=self.current_language,
                    jit=jit_context,
                )  # type: ignore[attr-defined]
            except Exception as e:
                logger.error(f"Failed to render companion prompt: {e}")
                self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Failed to prepare companion prompt."}))
                return

            try:
                companion_raw = self.brain.llm.run_for_agent("lead_companion_agent", companion_prompt)  # type: ignore[attr-defined]
            except Exception as e:
                logger.error(f"LLM call failed for Companion: {e}")
                self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Companion model call failed."}))
                return

            # 5) Process the Actions
            def _strip_fences(t: str) -> str:
                t = (t or "").strip()
                t = re.sub(r"^```\w*\s*\n?", "", t, flags=re.MULTILINE)
                t = re.sub(r"\n?```\s*$", "", t, flags=re.MULTILINE)
                return t.strip()

            data: Dict[str, Any]
            try:
                cleaned_json = _strip_fences(companion_raw)
                # Heuristic: find outermost JSON object if spurious text exists
                start = cleaned_json.find("{")
                end = cleaned_json.rfind("}")
                if start != -1 and end != -1 and end > start:
                    cleaned_json = cleaned_json[start:end+1]
                data = json.loads(cleaned_json)
            except Exception as e:
                logger.error(f"Failed to parse Companion JSON: {e}. Raw: {companion_raw[:2000]}")
                self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Companion returned invalid JSON actions."}))
                return

            actions: List[Dict[str, Any]] = []
            if isinstance(data, dict):
                actions = data.get("actions") or []
            if not isinstance(actions, list):
                self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Companion actions were not a list."}))
                return

            # Execute actions in order
            final_reply: str = ""
            for act in actions:
                if not isinstance(act, dict):
                    continue
                name = act.get("action")
                params = act.get("params") or {}
                if name == "write_code":
                    file_path = (params.get("file_path") or "").strip()
                    content = params.get("content") or ""
                    if not file_path:
                        continue
                    # Dispatch CODE_GENERATED so WorkspaceService saves and UI updates via VALIDATED_CODE_SAVED
                    try:
                        self.event_bus.dispatch(Event(event_type="CODE_GENERATED", payload={
                            "file_path": file_path,
                            "code": str(content),
                        }))
                    except Exception:
                        logger.warning("Failed to dispatch CODE_GENERATED; continuing.")
                elif name == "reply":
                    final_reply = str(params.get("text") or "")
                else:
                    # Ignore unknown actions gracefully
                    continue

            # Stream reply to UI and record assistant message
            if final_reply:
                try:
                    self.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": final_reply}))
                    self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED", payload={}))
                except Exception:
                    logger.debug("UI stream dispatch failed; continuing.")
                try:
                    self.conversations.add_message("assistant", final_reply)
                except Exception:
                    logger.debug("Failed to append assistant message to conversation history.")

        except Exception as e:
            logger.error(f"Smart Context Assembler pipeline failed: {e}", exc_info=True)
            self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Internal error during request handling."}))
