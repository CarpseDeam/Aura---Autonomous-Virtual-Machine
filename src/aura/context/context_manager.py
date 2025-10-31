"""
Smart Context Manager.

Intelligently loads relevant files based on the user request, using semantic similarity,
dependency analysis, and token budget management. Supports both BOOTSTRAP and ITERATE modes.
"""

import logging
import fnmatch
from pathlib import Path
from typing import List, Optional, Dict, Set, Generator
from ..models.project_context import ProjectContext
from ..models.context_models import (
    ContextMode,
    ContextWindow,
    FileRelevance,
    ContextConfig
)
from .relevance_scorer import RelevanceScorer
from .dependency_analyzer import DependencyAnalyzer

logger = logging.getLogger(__name__)


class ContextManager:
    """
    Intelligently manages context loading for the Aura agent.

    Key responsibilities:
    - Select relevant files based on semantic similarity to the user request
    - Include dependencies of relevant files
    - Respect token budgets to avoid overwhelming the LLM
    - Support BOOTSTRAP mode (new projects) and ITERATE mode (existing projects)
    """

    def __init__(
        self,
        project_root: str,
        config: Optional[ContextConfig] = None,
        event_bus=None
    ):
        """
        Initialize the Context Manager.

        Args:
            project_root: Absolute path to the project root
            config: Optional configuration (uses defaults if not provided)
            event_bus: Optional event bus for observability
        """
        self.project_root = Path(project_root).resolve()
        self.config = config or ContextConfig()
        self.event_bus = event_bus

        # Initialize sub-components
        self.relevance_scorer = RelevanceScorer(use_cache=True)
        self.dependency_analyzer = DependencyAnalyzer(str(self.project_root))

        logger.info(f"Initialized ContextManager for {self.project_root}")

    def load_context(
        self,
        user_request: str,
        project_context: ProjectContext,
        mode: Optional[ContextMode] = None
    ) -> ContextWindow:
        """
        Load intelligent context based on the user request.

        Args:
            user_request: The user's task description
            project_context: Current project state
            mode: BOOTSTRAP or ITERATE (auto-detected if not provided)

        Returns:
            ContextWindow with loaded files and metadata
        """
        # Auto-detect mode if not provided
        if mode is None:
            mode = self._detect_mode(project_context)

        logger.info(f"Loading context in {mode.value} mode")

        # Dispatch event
        self._dispatch_event("CONTEXT_LOADING_STARTED", {
            "mode": mode.value,
            "request": user_request
        })

        # Get available files
        available_files = self._get_available_files(project_context)

        if not available_files:
            logger.warning("No files available to load")
            return ContextWindow(
                mode=mode,
                user_request=user_request,
                loaded_files=[],
                max_tokens=self.config.max_tokens
            )

        # Score files for relevance
        scored_files = self._score_and_filter_files(
            user_request,
            available_files,
            mode
        )

        # Expand with dependencies if enabled
        if self.config.include_dependencies:
            scored_files = self._expand_with_dependencies(scored_files)

        # Apply token budget and build final context
        context_window = self._build_context_window(
            user_request,
            scored_files,
            mode
        )

        # Dispatch completion event
        self._dispatch_event("CONTEXT_LOADING_COMPLETED", {
            "mode": mode.value,
            "files_loaded": len(context_window.loaded_files),
            "total_tokens": context_window.total_tokens,
            "truncated": context_window.truncated
        })

        logger.info(
            f"Loaded {len(context_window.loaded_files)} files "
            f"({context_window.total_tokens} tokens, "
            f"{context_window.token_utilization:.1%} utilization)"
        )

        return context_window

    def _detect_mode(self, project_context: ProjectContext) -> ContextMode:
        """
        Detect whether we're in BOOTSTRAP or ITERATE mode.

        Args:
            project_context: Current project state

        Returns:
            Detected ContextMode
        """
        # If there's an active project with files, we're iterating
        if (project_context.active_project and
            project_context.active_files and
            len(project_context.active_files) > 0):
            return ContextMode.ITERATE

        # Otherwise, we're bootstrapping
        return ContextMode.BOOTSTRAP

    def _get_available_files(self, project_context: ProjectContext) -> List[str]:
        """
        Get list of available files from project context.

        Args:
            project_context: Current project state

        Returns:
            List of file paths
        """
        # Use active_files if available
        if project_context.active_files:
            return project_context.active_files

        # Otherwise, try to get files from workspace extras
        if 'file_tree' in project_context.extras:
            return self._extract_files_from_tree(project_context.extras['file_tree'])

        # Fall back to scanning project directory
        return self._scan_project_files()

    def _extract_files_from_tree(self, file_tree: Dict) -> List[str]:
        """
        Extract file paths from a file tree structure.

        Args:
            file_tree: Nested dict representing file tree

        Returns:
            List of file paths
        """
        files = []

        def _traverse(node: Dict, current_path: str = ""):
            if isinstance(node, dict):
                for key, value in node.items():
                    new_path = f"{current_path}/{key}" if current_path else key
                    if isinstance(value, dict):
                        _traverse(value, new_path)
                    else:
                        # It's a file
                        files.append(new_path)

        _traverse(file_tree)
        return files

    def _scan_project_files(self) -> List[str]:
        """
        Scan project directory for Python files.

        Returns:
            List of file paths
        """
        try:
            python_files = []
            for path in self.project_root.rglob("*.py"):
                # Skip __pycache__ and other common excludes
                if "__pycache__" in path.parts or ".git" in path.parts:
                    continue
                python_files.append(str(path.relative_to(self.project_root)))
            return python_files
        except Exception as e:
            logger.error(f"Error scanning project files: {e}")
            return []

    def _score_and_filter_files(
        self,
        user_request: str,
        files: List[str],
        mode: ContextMode
    ) -> List[FileRelevance]:
        """
        Score files for relevance and apply filtering.

        Args:
            user_request: User's task description
            files: Available files
            mode: Context mode

        Returns:
            Filtered and scored FileRelevance list
        """
        # Boost priority files based on mode
        priority_files = self._get_priority_files(files, mode)

        # Score all files
        scored = self.relevance_scorer.score_files(
            user_request,
            files,
            file_contents=None  # Let scorer read files as needed
        )

        # Boost scores for priority files
        for file_rel in scored:
            if file_rel.file_path in priority_files:
                file_rel.relevance_score = min(1.0, file_rel.relevance_score + 0.2)
                file_rel.relevance_reason += " (priority file)"

        # Re-sort after boosting
        scored.sort(reverse=True)

        # Filter by minimum threshold
        filtered = [
            f for f in scored
            if f.relevance_score >= self.config.min_relevance_threshold
        ]

        # Limit to max_files
        return filtered[:self.config.max_files]

    def _get_priority_files(
        self,
        files: List[str],
        mode: ContextMode
    ) -> Set[str]:
        """
        Get priority files based on mode.

        Args:
            files: Available files
            mode: Context mode

        Returns:
            Set of priority file paths
        """
        priority = set()

        if mode == ContextMode.BOOTSTRAP:
            # Prioritize structure/config files
            patterns = self.config.bootstrap_focus
        else:
            # Prioritize test files and related patterns
            patterns = self.config.iterate_focus

        for file in files:
            for pattern in patterns:
                if fnmatch.fnmatch(file, pattern):
                    priority.add(file)
                    break

        return priority

    def _expand_with_dependencies(
        self,
        scored_files: List[FileRelevance]
    ) -> List[FileRelevance]:
        """
        Expand the file list with dependencies.

        Args:
            scored_files: Initially selected files

        Returns:
            Expanded file list with dependencies
        """
        # Collect all file paths already included
        included_files = {f.file_path for f in scored_files}
        dependency_files = []

        # For each high-relevance file, get its dependencies
        for file_rel in scored_files:
            # Only expand dependencies for Python files
            if not file_rel.file_path.endswith('.py'):
                continue

            # Get recursive dependencies
            try:
                for dep_file in self.dependency_analyzer.get_dependencies_recursive(
                    file_rel.file_path,
                    max_depth=self.config.dependency_depth
                ):
                    if dep_file not in included_files:
                        # Score the dependency
                        dep_score = self.relevance_scorer.score_files(
                            f"dependency of {file_rel.file_path}",
                            [dep_file],
                            file_contents=None
                        )
                        if dep_score:
                            dep_rel = dep_score[0]
                            # Dependencies get a moderate relevance score
                            dep_rel.relevance_score = min(0.7, dep_rel.relevance_score + 0.3)
                            dep_rel.relevance_reason = f"dependency of {Path(file_rel.file_path).name}"
                            dependency_files.append(dep_rel)
                            included_files.add(dep_file)
            except Exception as e:
                logger.warning(f"Error expanding dependencies for {file_rel.file_path}: {e}")

        # Combine and re-sort
        all_files = scored_files + dependency_files
        all_files.sort(reverse=True)

        return all_files

    def _build_context_window(
        self,
        user_request: str,
        scored_files: List[FileRelevance],
        mode: ContextMode
    ) -> ContextWindow:
        """
        Build the final context window, respecting token budget.

        Args:
            user_request: User's task description
            scored_files: Scored and sorted files
            mode: Context mode

        Returns:
            ContextWindow with loaded files
        """
        loaded_files = []
        total_tokens = 0
        truncated = False

        # Greedily add files until we hit token budget
        for file_rel in scored_files:
            # Check if adding this file would exceed budget
            if total_tokens + file_rel.estimated_tokens > self.config.max_tokens:
                # Budget exceeded - stop here
                truncated = True
                logger.info(
                    f"Token budget reached: {total_tokens}/{self.config.max_tokens}. "
                    f"Loaded {len(loaded_files)} files."
                )
                break

            loaded_files.append(file_rel)
            total_tokens += file_rel.estimated_tokens

        return ContextWindow(
            mode=mode,
            user_request=user_request,
            loaded_files=loaded_files,
            total_tokens=total_tokens,
            max_tokens=self.config.max_tokens,
            truncated=truncated,
            metadata={
                "total_available": len(scored_files),
                "loaded_count": len(loaded_files)
            }
        )

    def update_config(self, config: ContextConfig) -> None:
        """
        Update the configuration.

        Args:
            config: New configuration
        """
        self.config = config
        logger.info("Context configuration updated")

    def clear_caches(self) -> None:
        """Clear all internal caches."""
        self.relevance_scorer.clear_cache()
        self.dependency_analyzer.clear_cache()
        logger.info("All caches cleared")

    def _dispatch_event(self, event_type: str, payload: Dict) -> None:
        """
        Dispatch an event to the event bus.

        Args:
            event_type: Type of event
            payload: Event payload
        """
        if self.event_bus is None:
            return

        try:
            from ..models.events import Event
            self.event_bus.dispatch(Event(
                event_type=event_type,
                payload=payload
            ))
        except Exception as e:
            logger.debug(f"Error dispatching event {event_type}: {e}")
