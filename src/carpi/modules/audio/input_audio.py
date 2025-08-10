from __future__ import annotations

import asyncio
import logging
from typing import Optional

import numpy as np

try:
    import alsaaudio  # type: ignore
except Exception:  # pragma: no cover
    alsaaudio = None  # type: ignore

from ...event_bus import EventBus

logger = logging.getLogger(__name__)


class InputAudio:
    def __init__(self, events: EventBus, device: str = "default", channels: int = 2, rate: int = 44100, period_size: int = 1024, topic: str = "audio.input"):
        self._events = events
        self._device = device
        self._channels = channels
        self._rate = rate
        self._period_size = period_size
        self._topic = topic
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="input-audio")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _capture_loop(self) -> None:
        if alsaaudio is None:
            logger.warning("pyalsaaudio not available; input audio disabled")
            return
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NONBLOCK, device=self._device)
        inp.setchannels(self._channels)
        inp.setrate(self._rate)
        inp.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        inp.setperiodsize(self._period_size)
        logger.info("InputAudio capturing from '%s' %dch @%dHz", self._device, self._channels, self._rate)
        loop = asyncio.get_running_loop()
        try:
            while True:
                length, data = inp.read()
                if length > 0:
                    frame = np.frombuffer(data, dtype=np.int16)
                    asyncio.run_coroutine_threadsafe(
                        self._events.publish(self._topic, {
                            "tags": {"source": "phone" if self._topic == "audio.phone" else "input"},
                            "rate": self._rate,
                            "channels": self._channels,
                            "pcm_s16le": data,
                        }),
                        loop,
                    )
                else:
                    # Sleep briefly to avoid busy loop
                    asyncio.run_coroutine_threadsafe(asyncio.sleep(0.01), loop)
        finally:
            try:
                inp.close()
            except Exception:
                pass

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await loop.run_in_executor(None, self._capture_loop)
            await asyncio.sleep(1)



