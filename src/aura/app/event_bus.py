from typing import Callable, List, Dict

from src.aura.models.events import Event


class EventBus:
    """
    A simple event bus for decoupled communication between components.
    """
    def __init__(self):
        """Initializes the EventBus."""
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable):
        """
        Subscribe a callback function to a specific event type.

        Args:
            event_type: The type of event to subscribe to.
            callback: The function to call when the event is dispatched.
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
        print(f"Subscribed {callback.__name__} to event '{event_type}'")

    def dispatch(self, event: Event):
        """
        Dispatch an event to all subscribed callbacks.

        Args:
            event: The Event object to dispatch.
        """
        event_type = event.event_type
        if event_type in self._subscribers:
            print(f"Dispatching event '{event_type}' with payload: {event.payload}")
            for callback in self._subscribers[event_type]:
                try:
                    callback(event)
                except Exception as e:
                    print(f"Error in callback {callback.__name__} for event '{event_type}': {e}")
        else:
            print(f"No subscribers for event '{event_type}'")