import logging
from typing import List, Optional
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)


class TaskManagementService:
    """
    Manages the state of the task list for Mission Control.
    Handles sequential dispatching of tasks.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.tasks: List[Task] = []
        self._register_event_handlers()
        logger.info("TaskManagementService initialized.")

    def _register_event_handlers(self):
        """Subscribe to events that modify the task list."""
        self.event_bus.subscribe("ADD_TASK", self.handle_add_task)
        self.event_bus.subscribe("DISPATCH_ALL_TASKS", self.handle_dispatch_all_tasks)
        # Phoenix Initiative: Subscribe to validation events
        self.event_bus.subscribe("VALIDATE_CODE", self.handle_validation_started)
        self.event_bus.subscribe("VALIDATION_SUCCESSFUL", self.handle_validation_successful)
        self.event_bus.subscribe("VALIDATION_FAILED", self.handle_validation_failed)
        # Legacy: Subscribe to direct completion for non-spec tasks
        self.event_bus.subscribe("CODE_GENERATED", self.handle_task_completed)

    def get_task_by_id(self, task_id: str) -> Optional[Task]:
        """
        Retrieves a task from the list by its unique ID.
        """
        for task in self.tasks:
            if task.id == task_id:
                return task
        logger.warning(f"Task with ID '{task_id}' not found.")
        return None

    def handle_add_task(self, event: Event):
        """
        Handles the ADD_TASK event by creating a new task.
        Phoenix Initiative: Enhanced to include specification data.
        """
        description = event.payload.get("description")
        if not description:
            logger.warning("ADD_TASK event received with no description.")
            return

        # Phoenix Initiative: Check for specification data
        spec = event.payload.get("spec")
        new_task = Task(description=description, spec=spec)
        self.tasks.append(new_task)
        
        if spec:
            logger.info(f"Phoenix Task added with spec: '{description}'")
        else:
            logger.info(f"Legacy Task added: '{description}'")
        
        self._dispatch_task_list_update()

    def handle_dispatch_all_tasks(self, event: Event):
        """
        Kicks off the sequential dispatching of all pending tasks.
        """
        logger.info("Handling DISPATCH_ALL_TASKS event. Starting sequential dispatch.")
        self._dispatch_next_task()

    def handle_task_completed(self, event: Event):
        """
        Handles the completion of a task and triggers the next one.
        """
        logger.info("Handling CODE_GENERATED event, signifying task completion.")
        
        # Find the task that was in progress
        in_progress_task = next((task for task in self.tasks if task.status == TaskStatus.IN_PROGRESS), None)

        if in_progress_task:
            logger.info(f"Task '{in_progress_task.id}' completed.")
            # Instead of removing the task, update its status to COMPLETED.
            in_progress_task.status = TaskStatus.COMPLETED
            self._dispatch_task_list_update()
            
            # Dispatch the next task in the sequence
            self._dispatch_next_task()
        else:
            logger.warning("CODE_GENERATED event received, but no task was in progress.")

    def _dispatch_next_task(self):
        """
        Finds the next pending task, marks it as in-progress, and dispatches it.
        If no tasks are left, the process is complete.
        """
        # Find the next task that is still pending
        next_task_to_dispatch = next((task for task in self.tasks if task.status == TaskStatus.PENDING), None)

        if next_task_to_dispatch:
            logger.info(f"Dispatching next task: '{next_task_to_dispatch.description}'")
            next_task_to_dispatch.status = TaskStatus.IN_PROGRESS
            
            # Update the UI to show the task is in progress
            self._dispatch_task_list_update()

            # Dispatch the task for an agent to execute
            self.event_bus.dispatch(Event(
                event_type="DISPATCH_TASK",
                payload={"task_id": next_task_to_dispatch.id}
            ))
        else:
            # No pending tasks are left, the sequence is complete.
            logger.info("All tasks have been dispatched and completed.")
            # The task list should be empty at this point, but we dispatch an update for safety.
            self._dispatch_task_list_update()

    def _dispatch_task_list_update(self):
        """
        Dispatches the current list of tasks to the event bus.
        """
        tasks_payload = [task.model_dump() for task in self.tasks]
        update_event = Event(
            event_type="TASK_LIST_UPDATED",
            payload={"tasks": tasks_payload}
        )
        self.event_bus.dispatch(update_event)

    def handle_validation_started(self, event: Event):
        """
        Phoenix Initiative: Handle VALIDATE_CODE event by updating task status to VALIDATING.
        """
        task_id = event.payload.get("task_id")
        if not task_id:
            return
            
        task = self.get_task_by_id(task_id)
        if task:
            task.status = TaskStatus.VALIDATING
            logger.info(f"Task {task_id} moved to VALIDATING state")
            self._dispatch_task_list_update()

    def handle_validation_successful(self, event: Event):
        """
        Phoenix Initiative: Handle VALIDATION_SUCCESSFUL event - task passed quality gate.
        """
        task_id = event.payload.get("task_id")
        if not task_id:
            return
            
        task = self.get_task_by_id(task_id)
        if task:
            task.status = TaskStatus.VALIDATION_PASSED
            task.validation_error = None  # Clear any previous errors
            logger.info(f"Task {task_id} PASSED validation")
            self._dispatch_task_list_update()
            
            # Move to next task in the sequence
            self._dispatch_next_task()

    def handle_validation_failed(self, event: Event):
        """
        Phoenix Initiative: Handle VALIDATION_FAILED event - task failed quality gate.
        """
        task_id = event.payload.get("task_id")
        error_message = event.payload.get("error_message", "Unknown validation error")
        
        if not task_id:
            return
            
        task = self.get_task_by_id(task_id)
        if task:
            task.status = TaskStatus.VALIDATION_FAILED
            task.validation_error = error_message
            logger.warning(f"Task {task_id} FAILED validation: {error_message}")
            self._dispatch_task_list_update()
            
            # Continue with next task even if this one failed
            # TODO: In future, might want different behavior (retry, stop, etc.)
            self._dispatch_next_task()

    def add_temporary_task(self, task: Task):
        """
        Adds a single, direct-dispatch task to the list.
        Used for fast-lane refinement tasks that bypass the full planning process.
        """
        self.tasks.append(task)
        logger.info(f"Temporary task added for direct dispatch: '{task.description}'")
