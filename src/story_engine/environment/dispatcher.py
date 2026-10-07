from typing import List, Callable, Any

class Dispatcher:
    def __init__(self):
        self.subscribers: List[Callable[[Any], None]] = []
        self.events: List[Any] = []
        self._transaction_buffer: List[Any] | None = None

    def subscribe(self, callback: Callable[[Any], None]):
        self.subscribers.append(callback)

    def publish(self, event: Any):
        if self._transaction_buffer is not None:
            self._transaction_buffer.append(event)
            return
        self.events.append(event)
        failure = None
        for subscriber in self.subscribers:
            try:
                subscriber(event)
            except Exception as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    def get_events(self) -> List[Any]:
        return self.events

    def clear_events(self):
        self.events = []

    def begin_transaction(self) -> None:
        if self._transaction_buffer is not None:
            raise RuntimeError("dispatcher transaction is already active")
        self._transaction_buffer = []

    def commit_transaction(self) -> None:
        if self._transaction_buffer is None:
            return
        buffered = self._transaction_buffer
        self._transaction_buffer = None
        failure = None
        for event in buffered:
            try:
                self.publish(event)
            except Exception as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    def rollback_transaction(self) -> None:
        self._transaction_buffer = None
