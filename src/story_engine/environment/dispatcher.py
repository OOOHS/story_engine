from typing import List, Callable, Any

class Dispatcher:
    def __init__(self):
        self.subscribers: List[Callable[[Any], None]] = []
        self.events: List[Any] = []

    def subscribe(self, callback: Callable[[Any], None]):
        self.subscribers.append(callback)

    def publish(self, event: Any):
        self.events.append(event)
        for subscriber in self.subscribers:
            subscriber(event)

    def get_events(self) -> List[Any]:
        return self.events

    def clear_events(self):
        self.events = []
