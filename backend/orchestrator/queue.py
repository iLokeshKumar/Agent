import heapq
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class QueueItem:
    priority: int
    sequence: int
    task_id: Any = field(compare=False)


class PriorityTaskQueue:

    def __init__(self):
        self._heap: list[QueueItem] = []
        self._sequence = 0
        self._lock = threading.Lock()

    def put(self, task_id, priority: int):
        with self._lock:
            item = QueueItem(
                priority=priority,
                sequence=self._sequence,
                task_id=task_id,
            )

            self._sequence += 1

            heapq.heappush(
                self._heap,
                item,
            )

    def get(self):
        with self._lock:
            if not self._heap:
                return None

            return heapq.heappop(self._heap)

    def size(self) -> int:
        with self._lock:
            return len(self._heap)


task_queue = PriorityTaskQueue()