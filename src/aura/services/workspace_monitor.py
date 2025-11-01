from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceChanges:
    """Represents filesystem deltas between snapshots."""

    created: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)

    def has_changes(self) -> bool:
        return bool(self.created or self.modified or self.deleted)


class WorkspaceChangeMonitor:
    """
    Lightweight monitor that detects file changes in the workspace.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root)
        self._last_snapshot: Dict[str, float] = {}
        logger.info("WorkspaceChangeMonitor initialized for %s", self.workspace_root)

    def snapshot(self) -> WorkspaceChanges:
        current_snapshot: Dict[str, float] = {}
        created: List[str] = []
        modified: List[str] = []

        if not self.workspace_root.exists():
            logger.warning("Workspace root %s does not exist when snapshotting", self.workspace_root)
            deleted = list(self._last_snapshot.keys())
            self._last_snapshot = {}
            return WorkspaceChanges(created=[], modified=[], deleted=deleted)

        ignored_dirs = {".aura", "__pycache__"}
        for path in self.workspace_root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_dirs for part in path.parts):
                continue
            rel_path = str(path.relative_to(self.workspace_root))
            mtime = path.stat().st_mtime
            current_snapshot[rel_path] = mtime

            previous_mtime = self._last_snapshot.get(rel_path)
            if previous_mtime is None:
                created.append(rel_path)
            elif mtime > previous_mtime + 1e-6:
                modified.append(rel_path)

        deleted = [
            rel_path
            for rel_path in self._last_snapshot.keys()
            if rel_path not in current_snapshot and not any(part in ignored_dirs for part in Path(rel_path).parts)
        ]

        self._last_snapshot = current_snapshot
        changes = WorkspaceChanges(created=created, modified=modified, deleted=deleted)
        if changes.has_changes():
            logger.debug(
                "Workspace changes detected: %d created, %d modified, %d deleted",
                len(changes.created),
                len(changes.modified),
                len(changes.deleted),
            )
        return changes
