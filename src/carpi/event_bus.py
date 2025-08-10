from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator, DefaultDict


class EventBus:
    def __init__(self) -> None:
        self._topic_to_queues: DefaultDict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, topic: str, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._topic_to_queues.get(topic, []))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, topic: str, max_queue_size: int = 100) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
        async with self._lock:
            self._topic_to_queues[topic].append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            async with self._lock:
                if queue in self._topic_to_queues.get(topic, []):
                    self._topic_to_queues[topic].remove(queue)


