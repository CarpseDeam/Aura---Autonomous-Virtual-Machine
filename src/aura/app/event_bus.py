from typing import Callable, List, Dict
from PySide6.QtCore import QObject, Signal

from src.aura.models.events import Event


class EventBusSignaller(QObject):
    """
    A QObject to emit signals on the main (UI) thread.
    """
    signal = Signal(Event)


class EventBus:
    """
    A simple event bus for decoupled communication between components.
    Ensures all event dispatches are handled on the main UI thread.
    """
    def __init__(self):
        """Initializes the EventBus."""
        self._subscribers: Dict[str, List[Callable]] = {}
        self._signaller = EventBusSignaller()
        self._signaller.signal.connect(self._handle_event_on_main_thread)

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
        This method now emits a signal, ensuring the event is processed on the main thread.

        Args:
            event: The Event object to dispatch.
        """
        print(f"Dispatching event '{event.event_type}' with payload: {event.payload}")
        self._signaller.signal.emit(event)

    def _handle_event_on_main_thread(self, event: Event):
        """
        This slot is connected to the signaller's signal and ensures callbacks
        are executed on the main (UI) thread.
        """
        event_type = event.event_type
        if event_type in self._subscribers:
            for callback in self._subscribers[event_type]:
                try:
                    callback(event)
                except Exception as e:
                    print(f"Error in callback {callback.__name__} for event '{event_type}': {e}")
        else:
            print(f"No subscribers for event '{event_type}'")
