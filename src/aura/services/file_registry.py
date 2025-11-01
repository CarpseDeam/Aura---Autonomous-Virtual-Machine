"""
File Registry Service - Tracks planned vs actual file generation

This service is the foundation of Aura's production-ready code generation.
It bridges the gap between blueprint planning and actual code generation,
enabling import validation, auto-fixing, and future test generation.

Architecture:
- PlannedFile: What the blueprint intends to create
- ActualFile: What was actually generated
- FileMapping: Links planned to actual, tracks exports/imports
- FileRegistry: Central registry managing the full lifecycle

Thread Safety: Not thread-safe. Expected to be used from main thread only.
"""

import logging
import ast
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Any, Tuple
from enum import Enum
from pydantic import BaseModel, Field

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    """Status of file validation"""
    PENDING = "pending"
    SYNTAX_VALID = "syntax_valid"
    IMPORTS_VALID = "imports_valid"
    FULLY_VALID = "fully_valid"
    FAILED = "failed"
    AUTO_FIXED = "auto_fixed"


class FileSource(str, Enum):
    """Source of file generation"""
    BLUEPRINT = "blueprint"
    REFINE = "refine"
    MANUAL = "manual"
    AUTO_FIX = "auto_fix"


class PlannedFile(BaseModel):
    """Represents a file planned by the blueprint"""
    identifier: str  # Description from blueprint (e.g., "password hasher interface")
    planned_path: str  # Intended path (e.g., "src/auth/password_hasher.py")
    purpose: str  # What this file is supposed to do
    spec: Dict[str, Any] = Field(default_factory=dict)  # Full blueprint spec
    registered_at: datetime = Field(default_factory=datetime.now)


class ExportInfo(BaseModel):
    """Information about an export from a file"""
    name: str  # Class/function/variable name
    type: str  # "class", "function", "variable", "constant"
    line_number: int
    is_public: bool = True  # Not prefixed with _


class ImportInfo(BaseModel):
    """Information about an import in a file"""
    module: str  # Module being imported (e.g., "src.auth.password_hasher")
    names: List[str]  # Names imported (e.g., ["PasswordHasher"])
    is_relative: bool  # Whether it's a relative import
    line_number: int
    import_level: int = 0  # For relative imports (e.g., .. = 2)


class ActualFile(BaseModel):
    """Represents a file that was actually created"""
    actual_path: str  # Where it actually ended up
    actual_filename: str  # Actual filename (e.g., "i_password_hasher.py")
    exports: List[ExportInfo] = Field(default_factory=list)  # Classes/functions defined
    imports: List[ImportInfo] = Field(default_factory=list)  # Import statements
    source: FileSource = FileSource.BLUEPRINT
    code_hash: str = ""  # SHA-256 of the code
    created_at: datetime = Field(default_factory=datetime.now)
    modified_at: datetime = Field(default_factory=datetime.now)


class FileMapping(BaseModel):
    """Links planned file to actual file with metadata"""
    planned: PlannedFile
    actual: Optional[ActualFile] = None
    validation_status: ValidationStatus = ValidationStatus.PENDING
    validation_errors: List[str] = Field(default_factory=list)
    auto_fixes_applied: List[str] = Field(default_factory=list)  # Descriptions of fixes
    dependents: Set[str] = Field(default_factory=set)  # Files that import this file
    dependencies: Set[str] = Field(default_factory=set)  # Files this file imports


class ValidationResult(BaseModel):
    """Result of validating a file or set of files"""
    success: bool
    files_validated: int = 0
    files_with_errors: int = 0
    files_auto_fixed: int = 0
    errors: List[Dict[str, Any]] = Field(default_factory=list)  # [{file, error, line}]
    auto_fixes: List[Dict[str, Any]] = Field(default_factory=list)  # [{file, fix, line}]
    warnings: List[Dict[str, Any]] = Field(default_factory=list)  # [{file, warning}]

    def add_error(self, file_path: str, error: str, line: Optional[int] = None) -> None:
        """Add an error to the result"""
        self.errors.append({"file": file_path, "error": error, "line": line})

    def add_auto_fix(self, file_path: str, fix: str, line: Optional[int] = None) -> None:
        """Add an auto-fix to the result"""
        self.auto_fixes.append({"file": file_path, "fix": fix, "line": line})

    def add_warning(self, file_path: str, warning: str) -> None:
        """Add a warning to the result"""
        self.warnings.append({"file": file_path, "warning": warning})


class FileRegistry:
    """
    Central registry tracking planned vs actual files.

    This is the brain of Aura's code generation validation system.
    It knows what was planned, what was created, and how to reconcile differences.

    Responsibilities:
    - Track planned files from blueprint
    - Record actual files created
    - Map planned references to actual implementations
    - Provide lookup for import resolution
    - Track dependencies between files
    - Enable validation and auto-fixing

    Usage:
        registry = FileRegistry(event_bus, workspace_root)

        # Blueprint phase
        registry.register_planned("password hasher", "src/auth/password_hasher.py", "...")

        # Generation phase
        registry.register_actual(
            planned_identifier="password hasher",
            actual_path="src/auth/i_password_hasher.py",
            code=generated_code,
            source=FileSource.BLUEPRINT
        )

        # Validation phase
        result = registry.get_validation_summary()
    """

    def __init__(self, event_bus: EventBus, workspace_root: Path):
        """
        Initialize the file registry.

        Args:
            event_bus: Event bus for dispatching events
            workspace_root: Root directory of the workspace
        """
        self.event_bus = event_bus
        self.workspace_root = Path(workspace_root)

        # Core data structures
        self._mappings: Dict[str, FileMapping] = {}  # planned_path -> FileMapping
        self._path_index: Dict[str, str] = {}  # actual_path -> planned_path
        self._export_index: Dict[str, List[str]] = {}  # export_name -> [file_paths]

        # Generation session tracking
        self._current_session_files: List[str] = []  # Files in current generation
        self._session_start: Optional[datetime] = None

        logger.info("FileRegistry initialized with workspace: %s", workspace_root)

    def start_generation_session(self) -> None:
        """Start a new code generation session"""
        self._current_session_files = []
        self._session_start = datetime.now()
        logger.info("Started new generation session")

    def end_generation_session(self) -> List[str]:
        """
        End the current generation session.

        Returns:
            List of file paths generated in this session
        """
        files = self._current_session_files.copy()
        logger.info(
            "Ended generation session: %d files generated in %.2f seconds",
            len(files),
            (datetime.now() - self._session_start).total_seconds() if self._session_start else 0
        )
        return files

    def register_planned(
        self,
        identifier: str,
        planned_path: str,
        purpose: str,
        spec: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Register a file planned by the blueprint.

        Args:
            identifier: Description/identifier for this file
            planned_path: Intended file path
            purpose: What this file is supposed to do
            spec: Full blueprint spec for this file
        """
        planned_path = self._normalize_path(planned_path)

        planned = PlannedFile(
            identifier=identifier,
            planned_path=planned_path,
            purpose=purpose,
            spec=spec or {}
        )

        mapping = FileMapping(planned=planned)
        self._mappings[planned_path] = mapping

        logger.debug("Registered planned file: %s -> %s", identifier, planned_path)

        # Dispatch event
        self.event_bus.dispatch(Event(
            event_type="FILE_PLANNED",
            payload={
                "identifier": identifier,
                "planned_path": planned_path,
                "purpose": purpose
            }
        ))

    def register_actual(
        self,
        planned_identifier: str,
        actual_path: str,
        code: str,
        source: FileSource = FileSource.BLUEPRINT
    ) -> None:
        """
        Register a file that was actually created.

        Args:
            planned_identifier: Identifier or path from the planned file
            actual_path: Actual path where file was created
            code: Generated code
            source: Source of generation
        """
        actual_path = self._normalize_path(actual_path)

        # Find the planned file (match by identifier or path)
        mapping = self._find_planned_mapping(planned_identifier)

        if not mapping:
            # File was created but not planned - register as unplanned
            logger.warning("File created without plan: %s", actual_path)
            planned = PlannedFile(
                identifier=f"unplanned:{actual_path}",
                planned_path=actual_path,
                purpose="Generated without blueprint"
            )
            mapping = FileMapping(planned=planned)
            self._mappings[actual_path] = mapping

        # Parse code to extract exports and imports
        exports = self._extract_exports(code, actual_path)
        imports = self._extract_imports(code, actual_path)

        # Create actual file record
        actual = ActualFile(
            actual_path=actual_path,
            actual_filename=Path(actual_path).name,
            exports=exports,
            imports=imports,
            source=source,
            code_hash=self._compute_hash(code)
        )

        # Update mapping
        mapping.actual = actual
        self._path_index[actual_path] = mapping.planned.planned_path

        # Update export index
        for export in exports:
            if export.name not in self._export_index:
                self._export_index[export.name] = []
            self._export_index[export.name].append(actual_path)

        # Track in current session
        self._current_session_files.append(actual_path)

        logger.info(
            "Registered actual file: %s (exports: %d, imports: %d)",
            actual_path,
            len(exports),
            len(imports)
        )

        # Dispatch event
        self.event_bus.dispatch(Event(
            event_type="FILE_REGISTERED",
            payload={
                "planned_path": mapping.planned.planned_path,
                "actual_path": actual_path,
                "exports": [e.name for e in exports],
                "imports": [f"{i.module}.{','.join(i.names)}" for i in imports],
                "source": source.value
            }
        ))

    def update_validation_status(
        self,
        file_path: str,
        status: ValidationStatus,
        errors: Optional[List[str]] = None,
        auto_fixes: Optional[List[str]] = None
    ) -> None:
        """
        Update validation status for a file.

        Args:
            file_path: Path to the file
            status: New validation status
            errors: List of validation errors
            auto_fixes: List of auto-fixes applied
        """
        file_path = self._normalize_path(file_path)
        mapping = self._get_mapping_by_actual_path(file_path)

        if not mapping:
            logger.warning("Cannot update validation status for unknown file: %s", file_path)
            return

        mapping.validation_status = status
        if errors:
            mapping.validation_errors = errors
        if auto_fixes:
            mapping.auto_fixes_applied = auto_fixes

        logger.info("Updated validation status for %s: %s", file_path, status.value)

    def find_export(self, export_name: str) -> List[str]:
        """
        Find which files export a given class/function.

        Args:
            export_name: Name of class/function to find

        Returns:
            List of file paths that export this name
        """
        return self._export_index.get(export_name, [])

    def get_file_exports(self, file_path: str) -> List[ExportInfo]:
        """
        Get all exports from a file.

        Args:
            file_path: Path to the file

        Returns:
            List of exports from this file
        """
        mapping = self._get_mapping_by_actual_path(self._normalize_path(file_path))
        if mapping and mapping.actual:
            return mapping.actual.exports
        return []

    def get_file_imports(self, file_path: str) -> List[ImportInfo]:
        """
        Get all imports from a file.

        Args:
            file_path: Path to the file

        Returns:
            List of imports in this file
        """
        mapping = self._get_mapping_by_actual_path(self._normalize_path(file_path))
        if mapping and mapping.actual:
            return mapping.actual.imports
        return []

    def get_validation_summary(self) -> ValidationResult:
        """
        Get validation summary for all registered files.

        Returns:
            Validation result with statistics
        """
        result = ValidationResult(success=True)

        for mapping in self._mappings.values():
            if not mapping.actual:
                continue  # Skip files not yet generated

            result.files_validated += 1

            if mapping.validation_status == ValidationStatus.FAILED:
                result.files_with_errors += 1
                result.success = False
                for error in mapping.validation_errors:
                    result.add_error(mapping.actual.actual_path, error)

            if mapping.validation_status == ValidationStatus.AUTO_FIXED:
                result.files_auto_fixed += 1
                for fix in mapping.auto_fixes_applied:
                    result.add_auto_fix(mapping.actual.actual_path, fix)

            # Check for unresolved imports
            for imp in mapping.actual.imports:
                if not self._is_import_resolvable(imp, mapping.actual.actual_path):
                    result.add_warning(
                        mapping.actual.actual_path,
                        f"Potentially unresolvable import: {imp.module}"
                    )

        return result

    def get_all_files(self) -> List[str]:
        """
        Get list of all registered actual file paths.

        Returns:
            List of actual file paths
        """
        return [
            mapping.actual.actual_path
            for mapping in self._mappings.values()
            if mapping.actual
        ]

    def clear(self) -> None:
        """Clear all registry data (for testing or new session)"""
        self._mappings.clear()
        self._path_index.clear()
        self._export_index.clear()
        self._current_session_files.clear()
        self._session_start = None
        logger.info("FileRegistry cleared")

    # Private helper methods

    def _normalize_path(self, path: str) -> str:
        """Normalize file path to forward slashes"""
        return path.replace("\\", "/")

    def _to_workspace_relative(self, path: str) -> str:
        """
        Convert a path to workspace-relative form.

        Args:
            path: Path to normalize

        Returns:
            Workspace-relative path using forward slashes
        """
        normalized = self._normalize_path(path)
        root = self._normalize_path(str(self.workspace_root))

        if normalized.startswith(root):
            remainder = normalized[len(root):].lstrip("/")
            return remainder

        return normalized.lstrip("/")

    def _construct_module_candidates(self, parts: List[str]) -> List[str]:
        """
        Build candidate module file paths for a module reference.

        Args:
            parts: Module path broken into parts (e.g., ["src", "auth", "user"])

        Returns:
            Candidate file paths that could satisfy the module
        """
        if not parts:
            return []

        base = "/".join(parts)
        candidates = [f"{base}.py", f"{base}/__init__.py"]
        return list(dict.fromkeys(candidates))

    def _construct_package_candidates(self, parts: List[str]) -> List[str]:
        """
        Build candidate package __init__ files for a directory reference.

        Args:
            parts: Directory parts representing a package

        Returns:
            Candidate __init__.py locations
        """
        base = "/".join(parts)
        if base:
            return [f"{base}/__init__.py"]
        return ["__init__.py"]

    def _get_mapping_for_candidate(self, candidate: str) -> Optional[FileMapping]:
        """
        Look up a file mapping for a candidate path.

        Args:
            candidate: Candidate file path relative to the workspace

        Returns:
            FileMapping if the candidate exists and has an actual file registered
        """
        normalized_candidate = self._normalize_path(candidate)

        mapping = self._get_mapping_by_actual_path(normalized_candidate)
        if mapping and mapping.actual:
            return mapping

        candidate_path = Path(normalized_candidate)
        if candidate_path.is_absolute():
            return None

        absolute_candidate = self._normalize_path(str(self.workspace_root.joinpath(*candidate_path.parts)))
        mapping = self._get_mapping_by_actual_path(absolute_candidate)
        if mapping and mapping.actual:
            return mapping

        return None

    def _resolve_relative_import_context(
        self,
        imp: ImportInfo,
        file_path: str
    ) -> Optional[Tuple[List[str], List[str], List[FileMapping]]]:
        """
        Resolve the base directory and module information for a relative import.

        Args:
            imp: Import information
            file_path: File containing the import

        Returns:
            Tuple of (base directory parts, module parts, candidate mappings) or None if invalid
        """
        relative_path = self._to_workspace_relative(file_path)
        path_parts = [part for part in relative_path.split("/") if part]

        if len(path_parts) < 2:
            logger.debug("Relative import in %s cannot be resolved (no package context)", file_path)
            return None

        current_dir_parts = path_parts[:-1]

        levels_up = max(0, imp.import_level - 1)
        if levels_up > len(current_dir_parts):
            logger.debug(
                "Relative import in %s goes above workspace root: level=%s",
                file_path,
                imp.import_level
            )
            return None

        slice_index = len(current_dir_parts) - levels_up
        base_dir_parts = list(current_dir_parts[:slice_index]) if slice_index > 0 else []
        module_parts = [part for part in imp.module.split(".") if part]
        base_module_parts = base_dir_parts + module_parts

        if module_parts:
            base_candidates = self._construct_module_candidates(base_module_parts)
        else:
            base_candidates = self._construct_package_candidates(base_dir_parts)

        base_mappings: List[FileMapping] = []
        seen_paths: Set[str] = set()
        for candidate in base_candidates:
            mapping = self._get_mapping_for_candidate(candidate)
            if mapping and mapping.actual:
                actual_path = mapping.actual.actual_path
                if actual_path not in seen_paths:
                    base_mappings.append(mapping)
                    seen_paths.add(actual_path)

        return base_dir_parts, module_parts, base_mappings

    def _relative_submodule_exists(
        self,
        base_dir_parts: List[str],
        module_parts: List[str],
        name: str
    ) -> bool:
        """
        Check whether a submodule exists for a relative import name.

        Args:
            base_dir_parts: Base directory parts after resolving the relative prefix
            module_parts: Module parts specified in the import (if any)
            name: Imported name to validate

        Returns:
            True if a matching module file exists in the registry
        """
        if not name or name == "*":
            return False

        submodule_parts = base_dir_parts + module_parts + [name]
        candidates = self._construct_module_candidates(submodule_parts)

        for candidate in candidates:
            mapping = self._get_mapping_for_candidate(candidate)
            if mapping and mapping.actual:
                return True

        return False

    def _find_planned_mapping(self, identifier: str) -> Optional[FileMapping]:
        """Find planned mapping by identifier or path"""
        # Try exact match on planned path
        if identifier in self._mappings:
            return self._mappings[identifier]

        # Try matching by identifier
        for mapping in self._mappings.values():
            if mapping.planned.identifier == identifier:
                return mapping

        # Try fuzzy match on path
        identifier_norm = self._normalize_path(identifier)
        for mapping in self._mappings.values():
            if mapping.planned.planned_path.endswith(identifier_norm):
                return mapping

        return None

    def _get_mapping_by_actual_path(self, actual_path: str) -> Optional[FileMapping]:
        """Get mapping by actual file path"""
        planned_path = self._path_index.get(actual_path)
        if planned_path:
            return self._mappings.get(planned_path)

        # Fallback: direct lookup if actual == planned
        return self._mappings.get(actual_path)

    def _extract_exports(self, code: str, file_path: str) -> List[ExportInfo]:
        """
        Extract exports (classes, functions) from code using AST.

        Args:
            code: Python source code
            file_path: Path to file (for logging)

        Returns:
            List of exports found
        """
        exports = []

        try:
            tree = ast.parse(code)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    exports.append(ExportInfo(
                        name=node.name,
                        type="class",
                        line_number=node.lineno,
                        is_public=not node.name.startswith("_")
                    ))
                elif isinstance(node, ast.FunctionDef):
                    # Only top-level functions
                    if any(isinstance(parent, ast.Module) for parent in ast.walk(tree)):
                        exports.append(ExportInfo(
                            name=node.name,
                            type="function",
                            line_number=node.lineno,
                            is_public=not node.name.startswith("_")
                        ))
                elif isinstance(node, ast.Assign):
                    # Top-level variables
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            # Check if it's a constant (all caps)
                            is_constant = target.id.isupper()
                            exports.append(ExportInfo(
                                name=target.id,
                                type="constant" if is_constant else "variable",
                                line_number=node.lineno,
                                is_public=not target.id.startswith("_")
                            ))

        except SyntaxError as e:
            logger.error("Syntax error parsing %s: %s", file_path, e)

        return exports

    def _extract_imports(self, code: str, file_path: str) -> List[ImportInfo]:
        """
        Extract import statements from code using AST.

        Args:
            code: Python source code
            file_path: Path to file (for logging)

        Returns:
            List of imports found
        """
        imports = []

        try:
            tree = ast.parse(code)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(ImportInfo(
                            module=alias.name,
                            names=[alias.asname or alias.name],
                            is_relative=False,
                            line_number=node.lineno
                        ))

                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = [alias.name for alias in node.names]

                    imports.append(ImportInfo(
                        module=module,
                        names=names,
                        is_relative=node.level > 0,
                        line_number=node.lineno,
                        import_level=node.level
                    ))

        except SyntaxError as e:
            logger.error("Syntax error parsing imports in %s: %s", file_path, e)

        return imports

    def _compute_hash(self, code: str) -> str:
        """Compute SHA-256 hash of code"""
        import hashlib
        return hashlib.sha256(code.encode()).hexdigest()

    def _is_import_resolvable(self, imp: ImportInfo, file_path: str) -> bool:
        """
        Check if an import can be resolved.

        Args:
            imp: Import information
            file_path: File containing the import

        Returns:
            True if import appears resolvable
        """
        # Standard library imports are always OK
        stdlib_modules = {
            "os", "sys", "pathlib", "typing", "datetime", "logging",
            "json", "ast", "re", "collections", "itertools", "functools"
        }
        if imp.module.split(".")[0] in stdlib_modules:
            return True

        # Check if any imported name exists in our export index
        for name in imp.names:
            if name in self._export_index:
                return True

        # Relative imports within the project
        if imp.is_relative:
            resolution = self._resolve_relative_import_context(imp, file_path)
            if not resolution:
                return False

            base_dir_parts, module_parts, base_mappings = resolution

            if module_parts and not base_mappings:
                logger.debug(
                    "Relative import in %s could not locate module '%s'",
                    file_path,
                    imp.module or "."
                )
                return False

            if any(name == "*" for name in imp.names):
                return bool(base_mappings)

            for name in imp.names:
                if any(
                    export.name == name
                    for mapping in base_mappings
                    if mapping.actual
                    for export in mapping.actual.exports
                ):
                    continue

                if self._relative_submodule_exists(base_dir_parts, module_parts, name):
                    continue

                logger.debug(
                    "Relative import in %s could not resolve name '%s' from module '%s'",
                    file_path,
                    name,
                    imp.module or "."
                )
                return False

            return True

        return False
