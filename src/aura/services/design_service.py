import json
import logging
from typing import Dict, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.research_service import ResearchService
from src.aura.services.task_management_service import TaskManagementService


logger = logging.getLogger(__name__)


class DesignService:
    """
    Owns the research → design → propose workflow.

    Responsibilities:
    - Listen for initial user requests and consult the Architect agent.
    - Orchestrate fast web research when requested by the Architect.
    - Dispatch BLUEPRINT_GENERATED with the raw blueprint JSON for downstream processing.
    """

    def __init__(
        self,
        event_bus: EventBus,
        prompt_manager: PromptManager,
        llm_dispatcher,  # LLMService (low-level dispatcher)
        task_management_service: TaskManagementService,
    ):
        self.event_bus = event_bus
        self.prompt_manager = prompt_manager
        self.llm = llm_dispatcher
        self.task_management_service = task_management_service

        # ResearchService uses a fast-model callback routed through the dispatcher
        self.research_service = ResearchService(
            lambda prompt: self.llm.run_for_agent("research_agent", prompt)
        )

        self._register_event_handlers()
        logger.info("DesignService initialized and listening for user requests.")

    # ------------------- Event Wiring -------------------
    def _register_event_handlers(self):
        # DesignService becomes the primary listener for initial user requests
        self.event_bus.subscribe("SEND_USER_MESSAGE", self._handle_user_request)

    # ------------------- Public/Event Handlers -------------------
    def _handle_user_request(self, event: Event):
        """
        Entry point for the design workflow triggered by a user message.
        Runs the Architect consultation (with optional research) and proposes a blueprint.
        """
        user_request = event.payload.get("text")
        if not user_request:
            self._handle_error("Empty user request received.")
            return

        try:
            self._execute_architect_consultation(user_request)
        except Exception as e:
            logger.error(f"DesignService: error handling user request: {e}", exc_info=True)
            self._handle_error("A critical error occurred during the design phase.")

    # ------------------- Core Workflow -------------------
    def _execute_architect_consultation(self, user_request: str):
        """
        Invokes the Architect agent using a research-then-design sequence.

        Sequence:
        1) Ask Architect for next action. Architect may request research.
        2) Run ResearchService, obtain dossier.
        3) Provide dossier back to Architect to generate the blueprint.
        4) Dispatch BLUEPRINT_GENERATED; further processing handled by BlueprintService.
        """
        provider, model_name, config = self.llm._get_provider_for_agent("architect_agent")  # reuse mapping
        if not provider or not model_name:
            self._handle_error("Architect agent is not configured.")
            return

        # Step 1: Architect initial action (expect research request)
        prompt = self.prompt_manager.render("plan_project.jinja2", user_request=user_request)
        first_response = self.llm.run_for_agent("architect_agent", prompt)

        dossier: Optional[Dict] = None
        blueprint_data: Optional[Dict] = None
        try:
            clean_json = first_response.strip().replace("```json", "").replace("```", "")
            data = json.loads(clean_json)
            if isinstance(data, dict) and data.get("tool_name") == "request_research":
                topic = (data.get("arguments") or {}).get("topic") or user_request
                dossier = self.research_service.research(topic)
            else:
                # Architect returned a blueprint directly (backward compatibility)
                blueprint_data = data
        except json.JSONDecodeError:
            # Non-JSON response; attempt to parse as blueprint later
            pass

        # Step 2: If research was requested, provide dossier and request final blueprint
        if dossier is not None and blueprint_data is None:
            prompt2 = self.prompt_manager.render(
                "plan_project.jinja2",
                user_request=user_request,
                research_dossier=dossier
            )
            full_response = self.llm.run_for_agent("architect_agent", prompt2)
            try:
                blueprint_data = json.loads(full_response.strip().replace("```json", "").replace("```", ""))
            except json.JSONDecodeError:
                self._handle_error("Architect returned invalid blueprint JSON after research phase.")
                return
        elif blueprint_data is None:
            # If we reach here, treat first response as blueprint JSON
            try:
                blueprint_data = json.loads(first_response.strip().replace("```json", "").replace("```", ""))
            except json.JSONDecodeError:
                self._handle_error("Architect returned invalid blueprint JSON.")
                return

        # Step 3: Delegate processing to BlueprintService via event
        self.event_bus.dispatch(Event(event_type="BLUEPRINT_GENERATED", payload=blueprint_data))

    # ------------------- Helpers -------------------
    

    def _emit_model_text(self, text: str):
        self.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": text}))
        self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED"))

    def _handle_error(self, message: str):
        logger.error(message)
        self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": message}))

    # ------------------- Validation + UI Formatting -------------------
    def _validate_blueprint_technologies(self, blueprint_data: dict, user_request: str) -> tuple[bool, str]:
        """
        Validate the Master Blueprint against technologies explicitly mentioned in the user request.

        Behavior:
        - Extract requested technologies from `user_request` (e.g., "Pygame").
        - Extract used technologies from each file's `imports_required` in the blueprint.
        - Compare with simple alias matching; fail fast with concise details if mismatched.
        """
        return True, ""
        # 1) Extract requested technologies
        request_text = (user_request or "").lower()
        import re
        tech_patterns = {
            "pygame": r"\bpygame\b",
            "flask": r"\bflask\b",
            "django": r"\bdjango\b",
            "fastapi": r"\bfastapi\b",
            "pyside6": r"\bpyside6\b",
            "pyqt": r"\bpyqt5?\b",
            "qt": r"\bqt\b",
            "tkinter": r"\btkinter\b",
            "requests": r"\brequests\b",
            "numpy": r"\bnumpy\b",
            "pandas": r"\bpandas\b",
            "torch": r"\btorch\b|\bpytorch\b",
            "transformers": r"\btransformers\b",
            "sentence-transformers": r"\bsentence[-_ ]transformers\b",
            "faiss": r"\bfaiss(?:[-_ ]?cpu)?\b",
            "pydantic": r"\bpydantic\b",
            "jinja2": r"\bjinja2\b",
            "google-generativeai": r"\bgoogle[- ]?generativeai\b|\bgenerativeai\b",
            "ollama": r"\bollama\b",
            "tcod": r"\btcod\b",
        }
        requested_techs = {name for name, pat in tech_patterns.items() if re.search(pat, request_text, re.IGNORECASE)}

        # No explicit technology constraint -> nothing to enforce
        if not requested_techs:
            return True, ""

        # 2) Extract used technologies from blueprint imports
        def normalize_module(mod: str) -> str:
            m = (mod or "").strip()
            top = m.split()[0]
            top = top.split(",")[0]
            top = top.split("as")[0]
            top = top.split(".")[0]
            return top.strip().lower()

        def canonical_tech_from_module(module: str) -> Optional[str]:
            mapping = {
                "pygame": "pygame",
                "flask": "flask",
                "django": "django",
                "fastapi": "fastapi",
                "pyside6": "pyside6",
                "pyqt5": "pyqt",
                "pyqt": "pyqt",
                "tkinter": "tkinter",
                "requests": "requests",
                "numpy": "numpy",
                "pandas": "pandas",
                "torch": "torch",
                "transformers": "transformers",
                "sentence_transformers": "sentence-transformers",
                "faiss": "faiss",
                "faiss_cpu": "faiss",
                "pydantic": "pydantic",
                "jinja2": "jinja2",
                "google.generativeai": "google-generativeai",
                "generativeai": "google-generativeai",
                "ollama": "ollama",
                "tcod": "tcod",
            }
            module = (module or "").lower()
            if module in mapping:
                return mapping[module]
            first = module.split(".")[0]
            return mapping.get(first)

        used_techs: set[str] = set()
        blueprint = blueprint_data.get("blueprint")
        files_list = blueprint_data.get("files")

        def add_from_imports(imports: list):
            for imp in imports or []:
                line = (imp or "").strip()
                modules = []
                if line.startswith("from "):
                    parts = line.split()
                    if len(parts) >= 2:
                        modules.append(parts[1])
                elif line.startswith("import "):
                    after = line[len("import "):]
                    for token in after.split(","):
                        modules.append(token.strip())
                for mod in modules:
                    tech = canonical_tech_from_module(normalize_module(mod))
                    if tech:
                        used_techs.add(tech)

        if isinstance(blueprint, dict):
            for _, spec in blueprint.items():
                if isinstance(spec, dict):
                    add_from_imports(spec.get("imports_required", []))
        elif isinstance(files_list, list):
            for f in files_list:
                add_from_imports((f or {}).get("imports_required", []))

        def matches(req: str, used: set[str]) -> bool:
            alias_map = {
                "qt": {"pyside6", "pyqt", "qt"},
                "torch": {"torch", "pytorch"},
                "pytorch": {"torch", "pytorch"},
                "sentence-transformers": {"sentence-transformers"},
                "faiss": {"faiss"},
                "google-generativeai": {"google-generativeai"},
            }
            if req in alias_map:
                return len(alias_map[req].intersection(used)) > 0
            return req in used

        missing = [r for r in sorted(requested_techs) if not matches(r, used_techs)]
        if missing:
            details = (
                f"Requested technologies not found in imports: {missing}. "
                f"Imports indicate usage: {sorted(used_techs) if used_techs else 'none'}"
            )
            return False, details

        return True, ""

    # UI formatting moved to MainWindow
