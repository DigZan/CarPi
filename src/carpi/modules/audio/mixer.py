from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...event_bus import EventBus

logger = logging.getLogger(__name__)


class AudioMixer:
    def __init__(self, events: EventBus) -> None:
        self._events = events
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="audio-mixer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        # Placeholder: wire up subs to topics like audio.phone, audio.music, audio.nav
        logger.info("Audio mixer started (stub)")
        async for _ in self._events.subscribe("audio.frame"):
            # Mix and route frames based on tags; publish mixed output, eg. audio.output
            pass




