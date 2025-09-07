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
        # Subscribe to the completion event to trigger the next task
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
        """
        description = event.payload.get("description")
        if not description:
            logger.warning("ADD_TASK event received with no description.")
            return

        new_task = Task(description=description)
        self.tasks.append(new_task)
        logger.info(f"New task added: '{description}'")
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
            # Remove the completed task from the list
            self.tasks.remove(in_progress_task)
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

    def add_temporary_task(self, task: Task):
        """
        Adds a single, direct-dispatch task to the list.
        Used for fast-lane refinement tasks that bypass the full planning process.
        """
        self.tasks.append(task)
        logger.info(f"Temporary task added for direct dispatch: '{task.description}'")
