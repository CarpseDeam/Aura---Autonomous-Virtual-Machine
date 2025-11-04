from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TerminalOutputMessage(BaseModel):
    """Terminal output message displayed in chat interface."""

    message_id: str = Field(description="Unique ID for this terminal session display")
    task_id: str = Field(description="Agent task identifier")
    command: str = Field(description="Command being executed")
    output: str = Field(default="", description="Accumulated terminal output")
    status: Literal["running", "completed", "failed"] = Field(default="running")
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    duration_seconds: Optional[float] = None
