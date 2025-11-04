from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, PrivateAttr, ConfigDict


class AgentSpecification(BaseModel):
    """Specification document provided to an external coding agent."""

    task_id: str
    request: str
    project_name: Optional[str] = None
    blueprint: Dict[str, Any] = Field(default_factory=dict)
    prompt: str
    files_to_watch: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TerminalSession(BaseModel):
    """Represents a spawned terminal agent session."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    command: List[str]
    spec_path: str
    process_id: Optional[int] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    child: Optional[Any] = Field(default=None, exclude=True)
    log_path: Optional[str] = None
    monitor_thread: Optional[threading.Thread] = Field(default=None, exclude=True)
    write_lock: threading.RLock = Field(default_factory=threading.RLock, exclude=True)
    answer_lock: threading.Lock = Field(default_factory=threading.Lock, exclude=True)
    answered_questions: Set[str] = Field(default_factory=set, exclude=True)

    _exit_code: Optional[int] = PrivateAttr(default=None)

    def is_alive(self) -> bool:
        """Return True if the underlying PTY child process is still running."""
        child = self.child
        if child is None:
            return False
        try:
            # Check if child is a Popen object (has poll method)
            if hasattr(child, 'poll'):
                return child.poll() is None
            # Otherwise it's a pexpect object (has isalive method)
            return bool(child.isalive())
        except Exception:
            return False

    def poll(self) -> Optional[int]:
        """Return the exit code if the child process has terminated."""
        if self.is_alive():
            return None
        return self._capture_exit_code()

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """Block until the child process exits or timeout expires."""
        child = self.child
        if child is None:
            return self._exit_code
        if timeout is None:
            try:
                # Check if child is a Popen object (has wait method with different signature)
                if hasattr(child, 'poll'):
                    child.wait()
                else:
                    # pexpect object
                    child.wait()
            except Exception:
                pass
            return self._capture_exit_code()
        deadline = time.monotonic() + timeout
        while self.is_alive():
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)
        return self._capture_exit_code()

    def mark_exit(self, exit_code: Optional[int]) -> None:
        """Persist the final exit code once the child terminates."""
        self._exit_code = exit_code

    def _capture_exit_code(self) -> Optional[int]:
        child = self.child
        if child is None:
            return self._exit_code
        # Check if child is a Popen object
        if hasattr(child, 'poll'):
            exit_code = child.poll()
        else:
            # pexpect object
            exit_code = getattr(child, "exitstatus", None)
            if exit_code is None:
                exit_code = getattr(child, "status", None)
        if exit_code is not None:
            self._exit_code = exit_code
        return self._exit_code


class TaskSummary(BaseModel):
    """Structured summary written by terminal agents upon completion.

    Fields mirror the expected `.aura/{task_id}.summary.json` structure.
    """

    status: str = Field(description="completed | failed | partial")
    files_created: List[str] = Field(default_factory=list)
    files_modified: List[str] = Field(default_factory=list)
    files_deleted: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    execution_time_seconds: Optional[float] = Field(default=None)
    suggestions: List[str] = Field(default_factory=list)

    def short_outcome(self) -> str:
        """Return a compact human-readable outcome label."""
        created = len(self.files_created)
        modified = len(self.files_modified)
        deleted = len(self.files_deleted)
        parts: List[str] = []
        if created:
            parts.append(f"created {created}")
        if modified:
            parts.append(f"modified {modified}")
        if deleted:
            parts.append(f"deleted {deleted}")
        files_part = ", ".join(parts) if parts else "no file changes"
        return f"{self.status}: {files_part}"
