"""
Lightweight workspace index for Aura.

Tracks the files that currently exist in the active project so the
conversation layer can reason about reality without heavy validation logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceSnapshot:
    """Represents a point-in-time view of workspace files."""

    root: Path
    files: List[Path] = field(default_factory=list)

    def contains(self, relative_path: str) -> bool:
        candidate = (self.root / relative_path).resolve()
        return any(file.resolve() == candidate for file in self.files)


class FileRegistry:
    """
    Minimal registry that indexes files under a workspace root.

    Responsibilities:
    - Refresh the on-disk file list on demand.
    - Provide helpers to query known files.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self._snapshot: Optional[WorkspaceSnapshot] = None
        logger.info("FileRegistry initialized for workspace %s", workspace_root)

    def refresh(self) -> WorkspaceSnapshot:
        """Re-scan the workspace and capture the latest file list."""
        files: List[Path] = []
        for path in self._iter_workspace_files():
            files.append(path)
        self._snapshot = WorkspaceSnapshot(root=self.workspace_root, files=files)
        logger.debug("FileRegistry refreshed: %d files indexed", len(files))
        return self._snapshot

    def list_files(self) -> List[str]:
        """Return workspace files as relative paths."""
        snapshot = self._ensure_snapshot()
        return [str(path.relative_to(snapshot.root)) for path in snapshot.files]

    def contains(self, relative_path: str) -> bool:
        """Check whether a relative path exists in the cached snapshot."""
        snapshot = self._ensure_snapshot()
        return snapshot.contains(relative_path)

    def _ensure_snapshot(self) -> WorkspaceSnapshot:
        if self._snapshot is None:
            return self.refresh()
        return self._snapshot

    def _iter_workspace_files(self) -> Iterable[Path]:
        if not self.workspace_root.exists():
            logger.warning("Workspace root %s does not exist", self.workspace_root)
            return []
        ignored_dirs = {".aura", "__pycache__"}
        for path in self.workspace_root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_dirs for part in path.parts):
                continue
            yield path
