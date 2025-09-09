import logging
from typing import List, Optional, Set
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)


class TaskManagementService:
    """
    Manages the state of the task list for Mission Control.
    Handles dependency-aware dispatching of tasks.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.tasks: List[Task] = []
        # Set of file paths that have completed all their tasks
        self.completed_files: Set[str] = set()
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
        dependencies = event.payload.get("dependencies") or []
        if not isinstance(dependencies, list):
            dependencies = []
        new_task = Task(description=description, spec=spec, dependencies=dependencies)
        self.tasks.append(new_task)
        
        if spec:
            logger.info(f"Phoenix Task added with spec: '{description}'")
        else:
            logger.info(f"Legacy Task added: '{description}'")
        
        self._dispatch_task_list_update()

    def handle_dispatch_all_tasks(self, event: Event):
        """
        Start dependency-aware dispatching of all eligible tasks.
        """
        logger.info("Handling DISPATCH_ALL_TASKS event. Starting dependency-aware dispatch.")
        # Exit early if there are no pending tasks to prevent idle loops
        if not any(t.status == TaskStatus.PENDING for t in self.tasks):
            logger.info("No pending tasks to dispatch; exiting cleanly.")
            self._dispatch_task_list_update()
            return
        self._dispatch_next_task()

    

    def _dispatch_next_task(self):
        """
        Scan all pending tasks and dispatch those whose file dependencies have completed.
        Dispatches all eligible tasks in parallel (marking them IN_PROGRESS).
        """
        # Determine eligible tasks
        eligible: List[Task] = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            deps = task.dependencies or []
            if all(dep in self.completed_files for dep in deps):
                eligible.append(task)

        if not eligible:
            # No eligible tasks; either waiting on dependencies or done
            if all(t.status in {TaskStatus.COMPLETED, TaskStatus.VALIDATION_PASSED} for t in self.tasks):
                logger.info("All tasks have been dispatched and completed (dependency-aware).")
            return

        for t in eligible:
            t.status = TaskStatus.IN_PROGRESS
            logger.info(f"Dispatching task: '{t.description}'")
            self.event_bus.dispatch(Event(event_type="DISPATCH_TASK", payload={"task_id": t.id}))

        # Update UI after marking tasks
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
            self._check_file_completion_and_mark(task)
            self._dispatch_task_list_update()
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
            # Try dispatching other tasks that are not blocked by this file
            self._dispatch_next_task()

    def _check_file_completion_and_mark(self, task: Task):
        """
        If all tasks for a file are completed/passed, add the file to completed_files.
        """
        file_path = (task.spec or {}).get("file_path")
        if not file_path:
            return
        related = [t for t in self.tasks if (t.spec or {}).get("file_path") == file_path]
        if related and all(t.status in {TaskStatus.COMPLETED, TaskStatus.VALIDATION_PASSED} for t in related):
            if file_path not in self.completed_files:
                self.completed_files.add(file_path)
                logger.info(f"File completed: {file_path}")

    def add_temporary_task(self, task: Task):
        """
        Adds a single, direct-dispatch task to the list.
        Used for fast-lane refinement tasks that bypass the full planning process.
        """
        self.tasks.append(task)
        logger.info(f"Temporary task added for direct dispatch: '{task.description}'")
