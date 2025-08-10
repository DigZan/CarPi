from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from ...event_bus import EventBus

logger = logging.getLogger(__name__)


class AudioMixer:
    def __init__(self, events: EventBus) -> None:
        self._events = events
        self._task: asyncio.Task | None = None

        # Simple state to implement priority/ducking
        self._phone_block_until: float = 0.0
        self._nav_duck_until: float = 0.0

        # Tunables
        self._nav_duck_volume: float = 0.3  # 30%
        self._nav_duck_hold_seconds: float = 1.5
        self._phone_block_hold_seconds: float = 0.25

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

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _is_phone_active(self) -> bool:
        return self._now() < self._phone_block_until

    def _is_nav_active(self) -> bool:
        return self._now() < self._nav_duck_until

    def _scale_pcm_s16le(self, pcm: bytes, volume: float) -> bytes:
        if volume >= 0.999:
            return pcm
        try:
            samples = np.frombuffer(pcm, dtype=np.int16)
            # Use float32 for scaling then clip and cast back
            scaled = np.clip(samples.astype(np.float32) * volume, -32768, 32767).astype(np.int16)
            return scaled.tobytes()
        except Exception:
            # If anything goes wrong, fall back to original
            return pcm

    async def _run(self) -> None:
        logger.info("Audio mixer started (priority + ducking)")
        # Subscribe to known sources
        input_stream = self._events.subscribe("audio.input")
        phone_stream = self._events.subscribe("audio.phone")
        music_stream = self._events.subscribe("audio.music")
        nav_stream = self._events.subscribe("audio.nav")

        streams: list[tuple[str, Any]] = [
            ("input", input_stream),
            ("phone", phone_stream),
            ("music", music_stream),
            ("nav", nav_stream),
        ]

        async def consume(stream_source: str, stream: Any) -> None:
            async for frame in stream:
                tags = frame.get("tags", {})
                source = (tags.get("source") or stream_source)

                # Update activity windows
                if stream_source == "phone":
                    self._phone_block_until = self._now() + self._phone_block_hold_seconds
                if stream_source == "nav":
                    self._nav_duck_until = self._now() + self._nav_duck_hold_seconds

                # Enforce policy:
                # - Phone present: drop music
                # - Nav always outputs
                # - If nav active, duck music to 30%

                if source == "music":
                    if self._is_phone_active():
                        # Drop music entirely while phone audio is active
                        continue
                    if self._is_nav_active():
                        pcm = frame.get("pcm_s16le")
                        if isinstance(pcm, (bytes, bytearray)):
                            frame = dict(frame)
                            frame["pcm_s16le"] = self._scale_pcm_s16le(bytes(pcm), self._nav_duck_volume)
                # Always forward nav and phone and any other sources
                await self._events.publish("audio.output", frame)

        await asyncio.gather(*(consume(name, s) for name, s in streams))





