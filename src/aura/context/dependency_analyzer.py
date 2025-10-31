"""
Dependency Analyzer for Python imports.

Parses Python files to extract import statements and resolve local dependencies.
"""

import ast
import logging
import os
from pathlib import Path
from typing import List, Set, Optional, Dict, Generator
from ..models.context_models import DependencyInfo

logger = logging.getLogger(__name__)


class DependencyAnalyzer:
    """
    Analyzes Python files to extract import dependencies.

    Uses Python's AST module to parse imports and resolve local files.
    Follows single responsibility: only concerned with dependency extraction.
    """

    def __init__(self, project_root: str):
        """
        Initialize the dependency analyzer.

        Args:
            project_root: Absolute path to the project root directory
        """
        self.project_root = Path(project_root).resolve()
        self._import_cache: Dict[str, DependencyInfo] = {}

    def analyze_file(self, file_path: str) -> Optional[DependencyInfo]:
        """
        Analyze a single Python file for its dependencies.

        Args:
            file_path: Path to the Python file (absolute or relative)

        Returns:
            DependencyInfo object or None if analysis fails
        """
        try:
            file_path_obj = Path(file_path)
            if not file_path_obj.is_absolute():
                file_path_obj = self.project_root / file_path

            # Check cache first
            cache_key = str(file_path_obj.resolve())
            if cache_key in self._import_cache:
                logger.debug(f"Cache hit for {file_path}")
                return self._import_cache[cache_key]

            # Read and parse the file
            with open(file_path_obj, 'r', encoding='utf-8') as f:
                source_code = f.read()

            tree = ast.parse(source_code, filename=str(file_path_obj))

            # Extract imports
            imported_modules = []
            imported_files = []
            external_dependencies = []

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module_name = alias.name
                        imported_modules.append(module_name)
                        self._categorize_import(
                            module_name,
                            file_path_obj,
                            imported_files,
                            external_dependencies
                        )

                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        module_name = node.module
                        # Handle relative imports
                        if node.level > 0:
                            module_name = self._resolve_relative_import(
                                file_path_obj,
                                node.module or "",
                                node.level
                            )
                        imported_modules.append(module_name)
                        self._categorize_import(
                            module_name,
                            file_path_obj,
                            imported_files,
                            external_dependencies
                        )

            dependency_info = DependencyInfo(
                source_file=str(file_path_obj),
                imported_modules=imported_modules,
                imported_files=imported_files,
                external_dependencies=external_dependencies
            )

            # Cache the result
            self._import_cache[cache_key] = dependency_info

            return dependency_info

        except FileNotFoundError:
            logger.warning(f"File not found: {file_path}")
            return None
        except SyntaxError as e:
            logger.warning(f"Syntax error in {file_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error analyzing {file_path}: {e}", exc_info=True)
            return None

    def get_dependencies_recursive(
        self,
        file_path: str,
        max_depth: int = 2,
        visited: Optional[Set[str]] = None
    ) -> Generator[str, None, None]:
        """
        Recursively get all dependencies of a file.

        Args:
            file_path: Path to the starting file
            max_depth: Maximum recursion depth
            visited: Set of already visited files (for cycle detection)

        Yields:
            File paths of dependencies
        """
        if visited is None:
            visited = set()

        file_path_obj = Path(file_path)
        if not file_path_obj.is_absolute():
            file_path_obj = self.project_root / file_path

        file_path_str = str(file_path_obj.resolve())

        # Avoid cycles
        if file_path_str in visited or max_depth <= 0:
            return

        visited.add(file_path_str)

        # Analyze current file
        dep_info = self.analyze_file(file_path_str)
        if not dep_info:
            return

        # Yield direct dependencies
        for imported_file in dep_info.imported_files:
            if imported_file not in visited:
                yield imported_file

                # Recurse into dependencies
                if max_depth > 1:
                    yield from self.get_dependencies_recursive(
                        imported_file,
                        max_depth - 1,
                        visited
                    )

    def analyze_multiple_files(
        self,
        file_paths: List[str]
    ) -> List[DependencyInfo]:
        """
        Analyze multiple files in batch.

        Args:
            file_paths: List of file paths to analyze

        Returns:
            List of DependencyInfo objects (excluding failed analyses)
        """
        results = []
        for file_path in file_paths:
            dep_info = self.analyze_file(file_path)
            if dep_info:
                results.append(dep_info)
        return results

    def clear_cache(self) -> None:
        """Clear the import cache."""
        self._import_cache.clear()
        logger.debug("Dependency cache cleared")

    def _resolve_relative_import(
        self,
        source_file: Path,
        module_name: str,
        level: int
    ) -> str:
        """
        Resolve relative import to absolute module name.

        Args:
            source_file: Path to the file containing the import
            module_name: Module name from import statement
            level: Number of dots in relative import

        Returns:
            Resolved module name
        """
        # Go up 'level' directories
        current_dir = source_file.parent
        for _ in range(level - 1):
            current_dir = current_dir.parent

        # Build module path relative to project root
        try:
            relative_path = current_dir.relative_to(self.project_root)
            parts = list(relative_path.parts)
            if module_name:
                parts.append(module_name)
            return ".".join(parts)
        except ValueError:
            # current_dir is not relative to project_root
            return module_name

    def _categorize_import(
        self,
        module_name: str,
        source_file: Path,
        imported_files: List[str],
        external_dependencies: List[str]
    ) -> None:
        """
        Categorize an import as local file or external dependency.

        Args:
            module_name: Name of the imported module
            source_file: Path to the file containing the import
            imported_files: List to append local file paths to
            external_dependencies: List to append external packages to
        """
        # Check if it's a local import
        resolved_path = self._resolve_module_to_file(module_name, source_file)

        if resolved_path and resolved_path.exists():
            imported_files.append(str(resolved_path))
        else:
            # It's an external dependency
            # Extract root package name
            root_package = module_name.split('.')[0]
            if root_package not in external_dependencies:
                external_dependencies.append(root_package)

    def _resolve_module_to_file(
        self,
        module_name: str,
        source_file: Path
    ) -> Optional[Path]:
        """
        Attempt to resolve a module name to a local file path.

        Args:
            module_name: Dotted module name
            source_file: Path to the file containing the import

        Returns:
            Resolved file path or None if not a local module
        """
        # Convert module name to file path
        module_parts = module_name.split('.')

        # Strategy 1: Resolve relative to project root
        file_path = self.project_root / Path(*module_parts)

        # Check for .py file
        if file_path.with_suffix('.py').exists():
            return file_path.with_suffix('.py')

        # Check for package (__init__.py)
        if (file_path / '__init__.py').exists():
            return file_path / '__init__.py'

        # Strategy 2: Resolve relative to source file directory
        source_dir = source_file.parent
        file_path = source_dir / Path(*module_parts)

        if file_path.with_suffix('.py').exists():
            return file_path.with_suffix('.py')

        if (file_path / '__init__.py').exists():
            return file_path / '__init__.py'

        # Could not resolve to local file
        return None

    def get_external_dependencies(self, file_paths: List[str]) -> Set[str]:
        """
        Get all external dependencies from a list of files.

        Args:
            file_paths: List of Python file paths

        Returns:
            Set of external package names
        """
        external_deps = set()
        for file_path in file_paths:
            dep_info = self.analyze_file(file_path)
            if dep_info:
                external_deps.update(dep_info.external_dependencies)
        return external_deps
