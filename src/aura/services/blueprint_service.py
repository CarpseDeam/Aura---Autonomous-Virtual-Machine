import logging
from typing import Dict

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.services.task_management_service import TaskManagementService


logger = logging.getLogger(__name__)


class BlueprintService:
    """
    Handles processing of a completed architect's blueprint.

    Responsibilities:
    - Listen for BLUEPRINT_GENERATED events containing the raw blueprint JSON.
    - Validate the blueprint against any requested technologies.
    - Publish granular tasks to Mission Control (ADD_TASK events).
    - Dispatch BLUEPRINT_APPROVED when ready for the build phase.
    """

    def __init__(
        self,
        event_bus: EventBus,
        task_management_service: TaskManagementService,
    ):
        self.event_bus = event_bus
        self.task_management_service = task_management_service

        self._register_event_handlers()
        logger.info("BlueprintService initialized and listening for BLUEPRINT_GENERATED events.")

    # ------------------- Event Wiring -------------------
    def _register_event_handlers(self):
        self.event_bus.subscribe("BLUEPRINT_GENERATED", self._handle_blueprint_generated)

    # ------------------- Public/Event Handlers -------------------
    def _handle_blueprint_generated(self, event: Event):
        """
        Entry point for processing a generated blueprint.
        """
        try:
            blueprint_data = event.payload or {}
            # Validate technologies if any were requested; user request text is unavailable here,
            # so pass an empty string to indicate no explicit constraints.
            is_valid, error_msg = self._validate_blueprint_technologies(blueprint_data, "")
            if not is_valid:
                self._handle_error(f"Blueprint validation failed: {error_msg}")
                return

            # Publish tasks to Mission Control
            self._publish_tasks_from_blueprint(blueprint_data)

            # Notify the build system
            self.event_bus.dispatch(Event(
                event_type="BLUEPRINT_APPROVED",
                payload={"blueprint": blueprint_data}
            ))
        except Exception as e:
            logger.error(f"BlueprintService: error while processing blueprint: {e}", exc_info=True)
            self._handle_error("A critical error occurred during blueprint processing.")

    # ------------------- Helpers -------------------
    def _publish_tasks_from_blueprint(self, blueprint_data: Dict):
        """Translates a blueprint into granular ADD_TASK events for Mission Control."""
        project_name = blueprint_data.get("project_name") or "Project"
        technologies = blueprint_data.get("technologies_used") or []
        logger.info(f"Blueprint validated. Project: {project_name}; Technologies: {technologies}")

        blueprint_files = blueprint_data.get("files") or []
        for file_spec in blueprint_files:
            if not isinstance(file_spec, dict):
                continue

            file_path = file_spec.get("file_path") or "workspace/generated.py"
            imports_required = file_spec.get("imports_required", [])

            # Classes and their methods
            for class_spec in file_spec.get("classes", []) or []:
                class_name = class_spec.get("name", "UnknownClass")
                for method_spec in class_spec.get("methods", []) or []:
                    method_name = method_spec.get("name", "unknown")
                    method_signature = method_spec.get("signature", f"def {method_name}():")
                    method_description = method_spec.get("description", "Implement method.")

                    task_description = (
                        f"In the file `{file_path}`, implement the `{class_name}.{method_name}` method. "
                        f"Method signature: `{method_signature}`. "
                        f"Functionality: {method_description}. "
                        f"Required imports: {', '.join(imports_required)}"
                    )

                    self.event_bus.dispatch(Event(event_type="ADD_TASK", payload={
                        "description": task_description,
                        "spec": {
                            "file_path": file_path,
                            "class_name": class_name,
                            "method_name": method_name,
                            "signature": method_signature,
                            "return_type": method_spec.get("return_type", "None"),
                            "description": method_description,
                            "imports_required": imports_required
                        }
                    }))

            # Standalone functions
            for function_spec in file_spec.get("functions", []) or []:
                function_name = function_spec.get("name", "unknown")
                function_signature = function_spec.get("signature", f"def {function_name}():")
                function_description = function_spec.get("description", "Implement function.")

                task_description = (
                    f"In the file `{file_path}`, implement the `{function_name}` function. "
                    f"Function signature: `{function_signature}`. "
                    f"Functionality: {function_description}. "
                    f"Required imports: {', '.join(imports_required)}"
                )

                self.event_bus.dispatch(Event(event_type="ADD_TASK", payload={
                    "description": task_description,
                    "spec": {
                        "file_path": file_path,
                        "function_name": function_name,
                        "signature": function_signature,
                        "return_type": function_spec.get("return_type", "None"),
                        "description": function_description,
                        "imports_required": imports_required
                    }
                }))

    def _handle_error(self, message: str):
        logger.error(message)
        self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": message}))

    # ------------------- Validation -------------------
    def _validate_blueprint_technologies(self, blueprint_data: dict, user_request: str) -> tuple[bool, str]:
        """
        Validate the Master Blueprint against technologies explicitly mentioned in the user request.

        Behavior:
        - Extract requested technologies from `user_request` (e.g., "Pygame").
        - Extract used technologies from each file's `imports_required` in the blueprint.
        - Compare with simple alias matching; fail fast with concise details if mismatched.
        """
        # 1) Extract requested technologies
        request_text = (user_request or "").lower()
        import re
        tech_patterns = {
            "pygame": r"\bpygame\b",
            "flask": r"\bflask\b",
            "django": r"\bdjango\b",
            "fastapi": r"\bfast\s*api\b|\bfastapi\b",
            "pandas": r"\bpandas\b",
            "numpy": r"\bnumpy\b|\bnp\b",
            "matplotlib": r"\bmatplotlib\b",
            "scikit-learn": r"\bscikit\s*-?learn\b|\bsklearn\b",
            "torch": r"\btorch\b|\bpytorch\b",
            "tensorflow": r"\btensorflow\b",
            "qt": r"\bqt\b|\bpyside6\b|\bpyqt\b",
            "faiss": r"\bfaiss\b",
            "sentence-transformers": r"\bsentence\s*-?transformers\b",
            "google-generativeai": r"\bgoogle\s*-?generativeai\b|\bgoogle\s*generative\s*ai\b",
        }
        requested_techs: set[str] = set()
        for tech, pattern in tech_patterns.items():
            if re.search(pattern, request_text):
                requested_techs.add(tech)

        # 2) Extract used technologies from blueprint imports
        def normalize_module(name: str) -> str:
            return (name or "").strip().lower().split(".")[0]

        def canonical_tech_from_module(mod: str) -> str | None:
            mapping = {
                "pygame": "pygame",
                "flask": "flask",
                "django": "django",
                "fastapi": "fastapi",
                "pandas": "pandas",
                "numpy": "numpy",
                "matplotlib": "matplotlib",
                "sklearn": "scikit-learn",
                "torch": "torch",
                "pytorch": "torch",
                "tensorflow": "tensorflow",
                "pyside6": "qt",
                "pyqt": "qt",
                "qt": "qt",
                "faiss": "faiss",
                "sentence-transformers": "sentence-transformers",
                "google-generativeai": "google-generativeai",
            }
            return mapping.get(mod)

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

