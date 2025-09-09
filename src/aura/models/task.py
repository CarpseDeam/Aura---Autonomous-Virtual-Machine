import uuid
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

class TaskStatus(str, Enum):
    """Enumeration for the status of a task."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    VALIDATING = "VALIDATING"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    COMPLETED = "COMPLETED"

class Task(BaseModel):
    """
    Data contract for a single task in the Mission Control log.
    Enhanced for Phoenix Initiative with validation states and specifications.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    status: TaskStatus = TaskStatus.PENDING
    spec: Optional[Dict[str, Any]] = None  # Phoenix Initiative: Granular specification from architect
    dependencies: Optional[List[str]] = None  # Build order dependencies (file paths)
    validation_error: Optional[str] = None  # Error message if validation fails
