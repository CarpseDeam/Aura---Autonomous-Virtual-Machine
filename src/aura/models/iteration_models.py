"""
Pydantic models for Iteration Controller.

These models track agent progress, reflection, and task completion.
"""

from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Status of an individual task."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class Task(BaseModel):
    """Represents a single task in the task checklist."""

    id: str = Field(..., description="Unique task identifier")
    description: str = Field(..., description="Human-readable task description")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Current status")
    created_at: datetime = Field(default_factory=datetime.now, description="Task creation time")
    completed_at: Optional[datetime] = Field(default=None, description="Task completion time")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional task metadata"
    )


class ProgressMetrics(BaseModel):
    """Metrics tracking overall progress."""

    total_tasks: int = Field(default=0, ge=0, description="Total number of tasks")
    completed_tasks: int = Field(default=0, ge=0, description="Number of completed tasks")
    failed_tasks: int = Field(default=0, ge=0, description="Number of failed tasks")
    blocked_tasks: int = Field(default=0, ge=0, description="Number of blocked tasks")

    @property
    def completion_percentage(self) -> float:
        """Calculate completion percentage."""
        if self.total_tasks == 0:
            return 0.0
        return (self.completed_tasks / self.total_tasks) * 100.0

    @property
    def in_progress_tasks(self) -> int:
        """Calculate number of in-progress tasks."""
        return self.total_tasks - self.completed_tasks - self.failed_tasks - self.blocked_tasks


class ReflectionResult(str, Enum):
    """Result of reflection on an action."""
    PROGRESS_MADE = "progress_made"  # Action moved us closer to goal
    NO_PROGRESS = "no_progress"      # Action didn't help
    REGRESSION = "regression"         # Action made things worse
    TASK_COMPLETE = "task_complete"   # Task is fully complete
    UNCLEAR = "unclear"               # Can't determine impact


class IterationReflection(BaseModel):
    """
    Reflection on a single iteration/action.

    After each action, the controller reflects on whether it helped.
    """

    iteration_number: int = Field(..., ge=0, description="Which iteration this reflects on")
    action_type: str = Field(..., description="Type of action that was taken")
    action_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters of the action"
    )
    result: ReflectionResult = Field(..., description="Assessment of the action's impact")
    reasoning: str = Field(
        default="",
        description="Why we assessed the action this way"
    )
    goal_distance: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Estimated distance to goal (0=complete, 1=not started)"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When this reflection occurred"
    )


class LoopDetectionState(BaseModel):
    """State for detecting repeated actions (loops)."""

    action_history: List[str] = Field(
        default_factory=list,
        description="History of action types in order"
    )
    repeated_action_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Count of each action type"
    )
    loop_detected: bool = Field(
        default=False,
        description="Whether a loop has been detected"
    )
    loop_action_type: Optional[str] = Field(
        default=None,
        description="Which action type is looping"
    )
    consecutive_same_action: int = Field(
        default=0,
        ge=0,
        description="Count of consecutive identical actions"
    )


class StoppingCondition(str, Enum):
    """Reason for stopping iteration."""
    TASK_COMPLETE = "task_complete"           # Task successfully completed
    MAX_ITERATIONS = "max_iterations"         # Hit iteration limit
    LOOP_DETECTED = "loop_detected"           # Stuck in a loop
    NO_PROGRESS = "no_progress"               # Not making any progress
    ERROR = "error"                           # Encountered an error
    USER_INTERRUPTED = "user_interrupted"     # User cancelled
    FINAL_ACTION = "final_action"             # Agent chose a final action type


class IterationState(BaseModel):
    """
    Complete state of the iteration controller.

    This tracks everything about the agent's progress toward task completion.
    """

    mode: str = Field(
        ...,
        description="Mode: 'bootstrap' or 'iterate'"
    )
    user_request: str = Field(
        ...,
        description="Original user request"
    )
    tasks: List[Task] = Field(
        default_factory=list,
        description="Task checklist"
    )
    reflections: List[IterationReflection] = Field(
        default_factory=list,
        description="History of reflections"
    )
    loop_state: LoopDetectionState = Field(
        default_factory=LoopDetectionState,
        description="Loop detection state"
    )
    current_iteration: int = Field(
        default=0,
        ge=0,
        description="Current iteration number"
    )
    max_iterations: int = Field(
        default=10,
        gt=0,
        description="Maximum allowed iterations"
    )
    should_stop: bool = Field(
        default=False,
        description="Whether iteration should stop"
    )
    stopping_condition: Optional[StoppingCondition] = Field(
        default=None,
        description="Reason for stopping"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )

    @property
    def metrics(self) -> ProgressMetrics:
        """Compute current progress metrics."""
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self.tasks if t.status == TaskStatus.FAILED)
        blocked = sum(1 for t in self.tasks if t.status == TaskStatus.BLOCKED)

        return ProgressMetrics(
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            blocked_tasks=blocked
        )

    @property
    def latest_reflection(self) -> Optional[IterationReflection]:
        """Get the most recent reflection."""
        if not self.reflections:
            return None
        return self.reflections[-1]

    @property
    def has_made_recent_progress(self) -> bool:
        """Check if recent iterations have made progress."""
        if len(self.reflections) < 1:
            return True  # Benefit of the doubt

        # Check last 2 reflections
        recent = self.reflections[-2:]
        progress_count = sum(
            1 for r in recent
            if r.result in [ReflectionResult.PROGRESS_MADE, ReflectionResult.TASK_COMPLETE]
        )

        return progress_count > 0


class IterationConfig(BaseModel):
    """Configuration for Iteration Controller."""

    max_iterations: int = Field(
        default=10,
        gt=0,
        description="Maximum iterations before forcing stop"
    )
    loop_detection_threshold: int = Field(
        default=3,
        gt=0,
        description="Number of consecutive same actions before flagging as loop"
    )
    no_progress_threshold: int = Field(
        default=3,
        gt=0,
        description="Number of consecutive no-progress iterations before stopping"
    )
    use_llm_reflection: bool = Field(
        default=True,
        description="Whether to use LLM for reflection (vs heuristics)"
    )
    bootstrap_max_iterations: int = Field(
        default=15,
        gt=0,
        description="Max iterations for BOOTSTRAP mode (typically needs more)"
    )
    iterate_max_iterations: int = Field(
        default=8,
        gt=0,
        description="Max iterations for ITERATE mode (typically needs less)"
    )
