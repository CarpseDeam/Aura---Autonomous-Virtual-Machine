"""
Iteration Controller for Aura agent.

Tracks progress, reflects on actions, detects loops, and determines when to stop iterating.
"""

import logging
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime
from ..models.action import Action, ActionType
from ..models.iteration_models import (
    Task,
    TaskStatus,
    IterationState,
    IterationReflection,
    IterationConfig,
    ReflectionResult,
    StoppingCondition,
    LoopDetectionState
)

logger = logging.getLogger(__name__)


class IterationController:
    """
    Controls the agent's iteration loop with intelligent stopping conditions.

    Key responsibilities:
    - Track progress toward task completion
    - Reflect on each action's effectiveness
    - Detect loops (repeated actions without progress)
    - Determine when to stop iterating
    - Support different stopping logic for BOOTSTRAP vs ITERATE modes
    """

    # Action types that signal completion
    FINAL_ACTIONS = {
        ActionType.SIMPLE_REPLY,
        ActionType.RESEARCH,
        ActionType.DESIGN_BLUEPRINT
    }

    def __init__(
        self,
        config: Optional[IterationConfig] = None,
        llm_service=None,
        event_bus=None
    ):
        """
        Initialize the Iteration Controller.

        Args:
            config: Optional configuration (uses defaults if not provided)
            llm_service: Optional LLM service for reflection
            event_bus: Optional event bus for observability
        """
        self.config = config or IterationConfig()
        self.llm_service = llm_service
        self.event_bus = event_bus

        logger.info("Initialized IterationController")

    def initialize_state(
        self,
        user_request: str,
        mode: str = "iterate"
    ) -> IterationState:
        """
        Initialize iteration state for a new task.

        Args:
            user_request: The user's request
            mode: 'bootstrap' or 'iterate'

        Returns:
            Fresh IterationState
        """
        # Set max iterations based on mode
        max_iters = (
            self.config.bootstrap_max_iterations
            if mode == "bootstrap"
            else self.config.iterate_max_iterations
        )

        state = IterationState(
            mode=mode,
            user_request=user_request,
            max_iterations=max_iters,
            tasks=[],
            reflections=[],
            loop_state=LoopDetectionState(),
            current_iteration=0,
            should_stop=False,
            metadata={"initialized_at": datetime.now().isoformat()}
        )

        # Generate initial task breakdown if LLM is available
        if self.llm_service and self.config.use_llm_reflection:
            self._generate_initial_tasks(state)

        self._dispatch_event("ITERATION_INITIALIZED", {
            "mode": mode,
            "max_iterations": max_iters,
            "request": user_request
        })

        return state

    def should_continue_iteration(
        self,
        state: IterationState,
        last_action: Optional[Action] = None,
        tool_output: Optional[str] = None
    ) -> bool:
        """
        Determine whether the agent should continue iterating.

        This is the main decision point called from agent.should_continue().

        Args:
            state: Current iteration state
            last_action: The action that was just executed
            tool_output: Output from the last tool execution

        Returns:
            True if should continue, False if should stop
        """
        # Update iteration count
        state.current_iteration += 1

        logger.debug(
            f"Evaluating continuation: iteration {state.current_iteration}/{state.max_iterations}"
        )

        # Check max iterations
        if state.current_iteration >= state.max_iterations:
            logger.info("Max iterations reached")
            return self._stop_iteration(state, StoppingCondition.MAX_ITERATIONS)

        # If no action was taken, stop
        if last_action is None:
            logger.info("No action taken, stopping")
            return self._stop_iteration(state, StoppingCondition.TASK_COMPLETE)

        # Check if it's a final action type
        if last_action.type in self.FINAL_ACTIONS:
            logger.info(f"Final action type: {last_action.type}")
            return self._stop_iteration(state, StoppingCondition.FINAL_ACTION)

        # Reflect on the action
        reflection = self._reflect_on_action(state, last_action, tool_output)
        state.reflections.append(reflection)

        # Update loop detection
        self._update_loop_detection(state, last_action)

        # Check for loops
        if state.loop_state.loop_detected:
            logger.warning(
                f"Loop detected: {state.loop_state.loop_action_type} "
                f"repeated {state.loop_state.consecutive_same_action} times"
            )
            return self._stop_iteration(state, StoppingCondition.LOOP_DETECTED)

        # Check if task is complete
        if reflection.result == ReflectionResult.TASK_COMPLETE:
            logger.info("Task assessed as complete")
            return self._stop_iteration(state, StoppingCondition.TASK_COMPLETE)

        # Check for sustained lack of progress
        if not state.has_made_recent_progress:
            no_progress_count = self._count_consecutive_no_progress(state)
            if no_progress_count >= self.config.no_progress_threshold:
                logger.warning(
                    f"No progress for {no_progress_count} iterations"
                )
                return self._stop_iteration(state, StoppingCondition.NO_PROGRESS)

        # Dispatch progress event
        self._dispatch_event("ITERATION_PROGRESS", {
            "iteration": state.current_iteration,
            "max_iterations": state.max_iterations,
            "reflection_result": reflection.result.value,
            "goal_distance": reflection.goal_distance
        })

        # Continue iterating
        return True

    def _reflect_on_action(
        self,
        state: IterationState,
        action: Action,
        tool_output: Optional[str]
    ) -> IterationReflection:
        """
        Reflect on whether an action made progress toward the goal.

        Args:
            state: Current iteration state
            action: The action that was taken
            tool_output: Output from the tool

        Returns:
            IterationReflection with assessment
        """
        # Use LLM for reflection if available
        if self.llm_service and self.config.use_llm_reflection:
            return self._llm_reflection(state, action, tool_output)
        else:
            return self._heuristic_reflection(state, action, tool_output)

    def _llm_reflection(
        self,
        state: IterationState,
        action: Action,
        tool_output: Optional[str]
    ) -> IterationReflection:
        """
        Use LLM to reflect on action effectiveness.

        Args:
            state: Current iteration state
            action: Action taken
            tool_output: Tool output

        Returns:
            IterationReflection
        """
        try:
            # Build reflection prompt
            prompt = self._build_reflection_prompt(state, action, tool_output)

            # Query LLM
            response = self.llm_service.generate(
                prompt,
                max_tokens=200,
                temperature=0.3
            )

            # Parse response (expecting JSON with result, reasoning, goal_distance)
            result = self._parse_reflection_response(response)

            return IterationReflection(
                iteration_number=state.current_iteration,
                action_type=action.type.value,
                action_params=action.params,
                result=result.get("result", ReflectionResult.UNCLEAR),
                reasoning=result.get("reasoning", ""),
                goal_distance=result.get("goal_distance", 0.5)
            )

        except Exception as e:
            logger.warning(f"LLM reflection failed: {e}. Using heuristic.")
            return self._heuristic_reflection(state, action, tool_output)

    def _heuristic_reflection(
        self,
        state: IterationState,
        action: Action,
        tool_output: Optional[str]
    ) -> IterationReflection:
        """
        Use heuristics to reflect on action effectiveness.

        Args:
            state: Current iteration state
            action: Action taken
            tool_output: Tool output

        Returns:
            IterationReflection
        """
        result = ReflectionResult.PROGRESS_MADE
        reasoning = "Action executed"
        goal_distance = 0.5

        # Heuristic: tool actions that succeed indicate progress
        if action.type in [ActionType.WRITE_FILE, ActionType.READ_FILE, ActionType.LIST_FILES]:
            if tool_output and "error" not in tool_output.lower():
                result = ReflectionResult.PROGRESS_MADE
                reasoning = "Tool action executed successfully"
                goal_distance = max(0.0, goal_distance - 0.1)
            else:
                result = ReflectionResult.NO_PROGRESS
                reasoning = "Tool action may have failed"

        # Final actions indicate completion
        elif action.type in self.FINAL_ACTIONS:
            result = ReflectionResult.TASK_COMPLETE
            reasoning = "Final action type reached"
            goal_distance = 0.0

        return IterationReflection(
            iteration_number=state.current_iteration,
            action_type=action.type.value,
            action_params=action.params,
            result=result,
            reasoning=reasoning,
            goal_distance=goal_distance
        )

    def _update_loop_detection(
        self,
        state: IterationState,
        action: Action
    ) -> None:
        """
        Update loop detection state based on the latest action.

        Args:
            state: Current iteration state
            action: Latest action
        """
        action_type = action.type.value

        # Add to history
        state.loop_state.action_history.append(action_type)

        # Update count
        if action_type not in state.loop_state.repeated_action_counts:
            state.loop_state.repeated_action_counts[action_type] = 0
        state.loop_state.repeated_action_counts[action_type] += 1

        # Check for consecutive same action
        if len(state.loop_state.action_history) >= 2:
            if state.loop_state.action_history[-1] == state.loop_state.action_history[-2]:
                state.loop_state.consecutive_same_action += 1
            else:
                state.loop_state.consecutive_same_action = 1

        # Detect loop
        if state.loop_state.consecutive_same_action >= self.config.loop_detection_threshold:
            state.loop_state.loop_detected = True
            state.loop_state.loop_action_type = action_type

    def _count_consecutive_no_progress(self, state: IterationState) -> int:
        """
        Count consecutive reflections with no progress.

        Args:
            state: Current iteration state

        Returns:
            Count of consecutive no-progress iterations
        """
        count = 0
        for reflection in reversed(state.reflections):
            if reflection.result in [ReflectionResult.NO_PROGRESS, ReflectionResult.UNCLEAR]:
                count += 1
            else:
                break
        return count

    def _stop_iteration(
        self,
        state: IterationState,
        condition: StoppingCondition
    ) -> bool:
        """
        Mark state as stopped with the given condition.

        Args:
            state: Current iteration state
            condition: Reason for stopping

        Returns:
            False (to signal stop)
        """
        state.should_stop = True
        state.stopping_condition = condition

        self._dispatch_event("ITERATION_STOPPED", {
            "condition": condition.value,
            "iterations": state.current_iteration,
            "mode": state.mode
        })

        logger.info(
            f"Iteration stopped: {condition.value} "
            f"after {state.current_iteration} iterations"
        )

        return False

    def _generate_initial_tasks(self, state: IterationState) -> None:
        """
        Generate initial task breakdown using LLM.

        Args:
            state: Iteration state to populate with tasks
        """
        try:
            if not self.llm_service:
                return

            # Build prompt for task breakdown
            prompt = f"""Break down the following request into specific, actionable tasks:

Request: {state.user_request}
Mode: {state.mode}

Provide 3-5 concrete tasks in JSON format:
{{"tasks": ["task 1", "task 2", ...]}}"""

            response = self.llm_service.generate(
                prompt,
                max_tokens=300,
                temperature=0.5
            )

            # Parse and create tasks
            import json
            data = json.loads(response)
            for task_desc in data.get("tasks", []):
                state.tasks.append(Task(
                    id=str(uuid.uuid4()),
                    description=task_desc,
                    status=TaskStatus.PENDING
                ))

            logger.info(f"Generated {len(state.tasks)} initial tasks")

        except Exception as e:
            logger.warning(f"Failed to generate initial tasks: {e}")

    def _build_reflection_prompt(
        self,
        state: IterationState,
        action: Action,
        tool_output: Optional[str]
    ) -> str:
        """Build prompt for LLM reflection."""
        return f"""Reflect on this agent action:

Original Request: {state.user_request}
Iteration: {state.current_iteration}/{state.max_iterations}
Action Taken: {action.type.value}
Parameters: {action.params}
Output: {tool_output[:500] if tool_output else 'N/A'}

Assess the action's effectiveness. Respond in JSON:
{{
  "result": "progress_made|no_progress|task_complete|regression|unclear",
  "reasoning": "brief explanation",
  "goal_distance": 0.0-1.0
}}"""

    def _parse_reflection_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM reflection response."""
        import json
        try:
            data = json.loads(response)
            # Map string result to enum
            result_str = data.get("result", "unclear")
            result_map = {
                "progress_made": ReflectionResult.PROGRESS_MADE,
                "no_progress": ReflectionResult.NO_PROGRESS,
                "task_complete": ReflectionResult.TASK_COMPLETE,
                "regression": ReflectionResult.REGRESSION,
                "unclear": ReflectionResult.UNCLEAR
            }
            data["result"] = result_map.get(result_str, ReflectionResult.UNCLEAR)
            return data
        except Exception as e:
            logger.warning(f"Failed to parse reflection response: {e}")
            return {
                "result": ReflectionResult.UNCLEAR,
                "reasoning": "Failed to parse",
                "goal_distance": 0.5
            }

    def update_task_status(
        self,
        state: IterationState,
        task_id: str,
        new_status: TaskStatus
    ) -> None:
        """
        Update a task's status.

        Args:
            state: Current iteration state
            task_id: ID of the task to update
            new_status: New status
        """
        for task in state.tasks:
            if task.id == task_id:
                task.status = new_status
                if new_status == TaskStatus.COMPLETED:
                    task.completed_at = datetime.now()
                logger.debug(f"Task {task_id} status updated to {new_status.value}")
                return

        logger.warning(f"Task {task_id} not found")

    def add_task(
        self,
        state: IterationState,
        description: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Task:
        """
        Add a new task to the state.

        Args:
            state: Current iteration state
            description: Task description
            metadata: Optional metadata

        Returns:
            Created Task
        """
        task = Task(
            id=str(uuid.uuid4()),
            description=description,
            status=TaskStatus.PENDING,
            metadata=metadata or {}
        )
        state.tasks.append(task)
        logger.debug(f"Added task: {description}")
        return task

    def get_progress_summary(self, state: IterationState) -> Dict[str, Any]:
        """
        Get a summary of current progress.

        Args:
            state: Current iteration state

        Returns:
            Progress summary dict
        """
        metrics = state.metrics

        return {
            "iteration": state.current_iteration,
            "max_iterations": state.max_iterations,
            "completion_percentage": metrics.completion_percentage,
            "total_tasks": metrics.total_tasks,
            "completed_tasks": metrics.completed_tasks,
            "in_progress_tasks": metrics.in_progress_tasks,
            "failed_tasks": metrics.failed_tasks,
            "blocked_tasks": metrics.blocked_tasks,
            "loop_detected": state.loop_state.loop_detected,
            "should_stop": state.should_stop,
            "stopping_condition": state.stopping_condition.value if state.stopping_condition else None
        }

    def _dispatch_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """
        Dispatch an event to the event bus.

        Args:
            event_type: Event type
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
