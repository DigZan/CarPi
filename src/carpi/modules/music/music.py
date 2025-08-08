from __future__ import annotations

import asyncio
import logging

from ...event_bus import EventBus

logger = logging.getLogger(__name__)


class MusicPlayer:
    def __init__(self, events: EventBus) -> None:
        self._events = events
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="music-player")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("Music module started (stub)")
        while True:
            await asyncio.sleep(5)




