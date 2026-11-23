# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {'type': self.type, 'ts': self.ts, **self.data}


Subscriber = Callable[[Event], None]


class EventBus:
    """Synchronous, thread-safe pub/sub. Callbacks run in the emitting thread and must not block.

    Event types:
        room_state         a room's status changed (data: room status dict)
        room_added / room_removed
        recording_started / recording_finished
        log                (data: level, message, room_id)
        settings_changed
        disk_low / disk_ok
        proxy_status
    """

    def __init__(self, log_history: int = 2000):
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()
        self.logs: deque[Event] = deque(maxlen=log_history)

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def emit(self, event_type: str, **data: Any) -> Event:
        event = Event(event_type, data)
        if event_type == 'log':
            self.logs.append(event)
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                pass
        return event
