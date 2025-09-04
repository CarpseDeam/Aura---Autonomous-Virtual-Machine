import uuid
from enum import Enum
from pydantic import BaseModel, Field

class TaskStatus(str, Enum):
    """Enumeration for the status of a task."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

class Task(BaseModel):
    """
    Data contract for a single task in the Mission Control log.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    status: TaskStatus = TaskStatus.PENDING