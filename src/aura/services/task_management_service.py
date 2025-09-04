import logging
from typing import List, Optional
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.task import Task

logger = logging.getLogger(__name__)


class TaskManagementService:
    """
    Manages the state of the task list for Mission Control.
    """
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.tasks: List[Task] = []
        self._register_event_handlers()
        logger.info("TaskManagementService initialized.")

    def _register_event_handlers(self):
        """Subscribe to events that modify the task list."""
        self.event_bus.subscribe("ADD_TASK", self.handle_add_task)

    def get_task_by_id(self, task_id: str) -> Optional[Task]:
        """
        Retrieves a task from the list by its unique ID.

        Args:
            task_id: The ID of the task to retrieve.

        Returns:
            The Task object if found, otherwise None.
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