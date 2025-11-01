import ast
import json
import logging
from typing import Any, Dict, Generator, List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, Field, ValidationError

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.services.file_registry import FileRegistry, FileSource

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class ValidateCodePayload(BaseModel):
    """Event payload contract for VALIDATE_CODE events."""

    task_id: Optional[str] = None
    file_path: str
    spec: Dict[str, Any] = Field(default_factory=dict)
    generated_code: str


class ValidationSuccessfulPayload(BaseModel):
    """Event payload contract for VALIDATION_SUCCESSFUL events."""

    task_id: Optional[str] = None
    file_path: str
    validated_code: str
    spec: Dict[str, Any] = Field(default_factory=dict)


class ValidationFailedPayload(BaseModel):
    """Event payload contract for VALIDATION_FAILED events."""

    task_id: Optional[str] = None
    file_path: str
    spec: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class BlueprintValidator:
    """
    The Guardian Protocol: Deterministic validation service for architect blueprints.
    
    This validator ensures that architect-generated blueprints comply with 
    non-negotiable project constraints defined in mission contracts.
    
    Prevents architectural drift by programmatically enforcing rules before
    any tasks are created or code is generated.
    """
    
    def __init__(self, event_bus: EventBus, file_registry: Optional[FileRegistry] = None) -> None:
        """
        Initialize the Blueprint Validator and subscribe to code validation events.
        """
        self.event_bus = event_bus
        self.file_registry = file_registry
        self.event_bus.subscribe("VALIDATE_CODE", self._handle_validate_code)
        logger.info("Guardian Protocol: BlueprintValidator initialized and event handlers registered")
    
    def validate(self, blueprint: Dict[str, Any], contract: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Primary validation method that enforces all contract rules against the blueprint.
        
        Args:
            blueprint: The architect's generated blueprint dictionary
            contract: The mission contract dictionary with validation rules
            
        Returns:
            Tuple of (is_valid: bool, error_messages: List[str])
            - is_valid: True if blueprint passes all contract rules, False otherwise
            - error_messages: List of human-readable validation failure messages
        """
        logger.info("Guardian Protocol: Starting blueprint validation")
        
        errors = []
        
        # Get validation rules from contract
        rules = contract.get('validation_rules', {})
        
        # Execute file_check rules
        if 'file_check' in rules:
            file_errors = self._validate_file_requirements(blueprint, rules['file_check'])
            errors.extend(file_errors)
        
        # Execute dependency_check rules
        if 'dependency_check' in rules:
            dependency_errors = self._validate_dependency_restrictions(blueprint, rules['dependency_check'])
            errors.extend(dependency_errors)
        
        # Determine overall validation result
        is_valid = len(errors) == 0
        
        if is_valid:
            logger.info("Guardian Protocol: Blueprint validation PASSED")
        else:
            logger.warning(f"Guardian Protocol: Blueprint validation FAILED with {len(errors)} violations")
            for error in errors:
                logger.warning(f"Guardian Protocol Violation: {error}")
        
        return is_valid, errors

    def _handle_validate_code(self, event: Event) -> None:
        """
        Process VALIDATE_CODE events, applying static checks to generated code.
        """
        raw_payload = event.payload if isinstance(event.payload, dict) else {}
        try:
            payload = self._model_validate(ValidateCodePayload, raw_payload)
        except ValidationError as exc:
            errors = self._format_pydantic_errors(exc)
            fallback_file_path = raw_payload.get("file_path") if isinstance(raw_payload.get("file_path"), str) else "<unknown>"
            fallback_task_id = raw_payload.get("task_id") if isinstance(raw_payload.get("task_id"), str) else None
            fallback_spec = raw_payload.get("spec") if isinstance(raw_payload.get("spec"), dict) else {}
            self._dispatch_validation_failed(
                file_path=fallback_file_path,
                task_id=fallback_task_id,
                spec=fallback_spec,
                errors=errors,
            )
            logger.warning("VALIDATE_CODE payload failed schema validation: %s", errors)
            return

        issues = list(self._run_generated_code_checks(payload))
        if issues:
            self._dispatch_validation_failed(
                file_path=payload.file_path,
                task_id=payload.task_id,
                spec=payload.spec,
                errors=issues,
            )
            logger.info(
                "Guardian Protocol: Validation failed for %s with %d issue(s)",
                payload.file_path,
                len(issues),
            )
            return

        self._dispatch_validation_success(payload)
        logger.info("Guardian Protocol: Validation passed for %s", payload.file_path)

    def _run_generated_code_checks(self, payload: ValidateCodePayload) -> Generator[str, None, None]:
        """
        Execute static validation checks against generated code.

        Yields:
            A stream of validation error messages.
        """
        code = payload.generated_code
        if not code or not code.strip():
            yield "Generated code is empty."
            return

        spec_language = ""
        if payload.spec:
            language_value = payload.spec.get("language")
            if isinstance(language_value, str):
                spec_language = language_value.lower()
            max_lines = payload.spec.get("max_lines")
            if isinstance(max_lines, int) and max_lines > 0:
                stripped_code = code.rstrip("\n")
                line_count = stripped_code.count("\n") + (1 if stripped_code else 0)
                if line_count > max_lines:
                    yield f"Generated code exceeds maximum allowed lines ({line_count}/{max_lines})."

        file_path_lower = payload.file_path.lower()
        should_validate_python = spec_language == "python" or file_path_lower.endswith(".py")
        if should_validate_python:
            try:
                ast.parse(code)
            except SyntaxError as exc:
                yield f"Python syntax error: {exc.msg} (line {exc.lineno})"

    def _dispatch_validation_success(self, payload: ValidateCodePayload) -> None:
        """
        Emit a VALIDATION_SUCCESSFUL event once all checks pass.
        """
        # Register the actual file in the file registry
        if self.file_registry:
            try:
                self.file_registry.register_actual(
                    planned_identifier=payload.spec.get("description", payload.file_path),
                    actual_path=payload.file_path,
                    code=payload.generated_code,
                    source=FileSource.BLUEPRINT
                )
                logger.debug("Registered actual file in FileRegistry: %s", payload.file_path)
            except Exception as e:
                logger.error("Failed to register actual file in FileRegistry: %s", e, exc_info=True)

        success_payload = ValidationSuccessfulPayload(
            task_id=payload.task_id,
            file_path=payload.file_path,
            validated_code=payload.generated_code,
            spec=payload.spec,
        )
        self.event_bus.dispatch(Event(
            event_type="VALIDATION_SUCCESSFUL",
            payload=self._model_dump(success_payload),
        ))

    def _dispatch_validation_failed(
        self,
        file_path: str,
        task_id: Optional[str],
        spec: Dict[str, Any],
        errors: List[str],
    ) -> None:
        """
        Emit a VALIDATION_FAILED event with collected issues.
        """
        failure_payload = ValidationFailedPayload(
            task_id=task_id,
            file_path=file_path,
            spec=spec,
            errors=errors,
        )
        self.event_bus.dispatch(Event(
            event_type="VALIDATION_FAILED",
            payload=self._model_dump(failure_payload),
        ))

    @staticmethod
    def _format_pydantic_errors(exc: ValidationError) -> List[str]:
        """
        Convert Pydantic validation errors into human-readable messages.
        """
        formatted: List[str] = []
        for err in exc.errors():
            location = ".".join(str(part) for part in err.get("loc", []))
            message = err.get("msg", "Invalid payload.")
            formatted.append(f"{location}: {message}" if location else message)
        return formatted or [str(exc)]

    @staticmethod
    def _model_validate(model: Type[T], data: Dict[str, Any]) -> T:
        """
        Compatibility wrapper supporting Pydantic v1 and v2 payload parsing.
        """
        if hasattr(model, "model_validate"):
            return model.model_validate(data)  # type: ignore[attr-defined]
        return model.parse_obj(data)  # type: ignore[attr-defined]

    @staticmethod
    def _model_dump(model: BaseModel) -> Dict[str, Any]:
        """
        Compatibility wrapper supporting Pydantic v1 and v2 serialization.
        """
        if hasattr(model, "model_dump"):
            return model.model_dump()  # type: ignore[attr-defined]
        return model.dict()
    
    def _validate_file_requirements(self, blueprint: Dict[str, Any], file_check_rules: Dict[str, Any]) -> List[str]:
        """
        Validates that required files are present in the blueprint.
        
        Args:
            blueprint: The architect's generated blueprint
            file_check_rules: Dictionary containing file validation rules
            
        Returns:
            List of error messages for any file requirement violations
        """
        errors = []
        
        # Check for must_exist files
        must_exist = file_check_rules.get('must_exist', [])
        blueprint_files = set(blueprint.keys())
        
        for required_file in must_exist:
            if required_file not in blueprint_files:
                errors.append(f"REQUIRED FILE MISSING: '{required_file}' must be included in the blueprint")
        
        # Check for must_not_exist files
        must_not_exist = file_check_rules.get('must_not_exist', [])
        for forbidden_file in must_not_exist:
            if forbidden_file in blueprint_files:
                errors.append(f"FORBIDDEN FILE PRESENT: '{forbidden_file}' must not be included in the blueprint")
        
        return errors
    
    def _validate_dependency_restrictions(self, blueprint: Dict[str, Any], dependency_check_rules: Dict[str, Any]) -> List[str]:
        """
        Validates that forbidden dependencies are not present anywhere in the blueprint.
        
        This method converts the entire blueprint to a string and searches for
        forbidden keywords, ensuring no prohibited dependencies slip through.
        
        Args:
            blueprint: The architect's generated blueprint
            dependency_check_rules: Dictionary containing dependency validation rules
            
        Returns:
            List of error messages for any dependency restriction violations
        """
        errors = []
        
        # Convert entire blueprint to searchable string
        blueprint_string = json.dumps(blueprint, indent=2).lower()
        
        # Check for forbidden dependencies
        must_not_include = dependency_check_rules.get('must_not_include', [])
        for forbidden_dep in must_not_include:
            if forbidden_dep.lower() in blueprint_string:
                errors.append(f"FORBIDDEN DEPENDENCY DETECTED: '{forbidden_dep}' is not allowed in this project")
        
        # Check for required dependencies
        must_include = dependency_check_rules.get('must_include', [])
        for required_dep in must_include:
            if required_dep.lower() not in blueprint_string:
                errors.append(f"REQUIRED DEPENDENCY MISSING: '{required_dep}' must be present in the blueprint")
        
        return errors
