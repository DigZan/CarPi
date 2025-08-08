from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional

import serial
import pynmea2

from ...event_bus import EventBus
from ...storage.db import Database

logger = logging.getLogger(__name__)


class GPSReader:
    def __init__(self, serial_port: str, baud: int, db: Database, bus_events: EventBus) -> None:
        self._port = serial_port
        self._baud = baud
        self._db = db
        self._events = bus_events
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="gps-reader")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _read_loop(self) -> None:
        try:
            with serial.Serial(self._port, self._baud, timeout=1) as ser:
                logger.info("GPS opened on %s @ %s", self._port, self._baud)
                while True:
                    line = ser.readline().decode(errors="ignore").strip()
                    if not line:
                        continue
                    try:
                        msg = pynmea2.parse(line, check=True)
                    except Exception:
                        continue
                    ts = dt.datetime.utcnow().isoformat()
                    data = {"sentence": msg.sentence_type, "raw": line}
                    loop = asyncio.get_running_loop()
                    asyncio.run_coroutine_threadsafe(self._db.insert_sensor_reading("gps", ts, data), loop)
                    asyncio.run_coroutine_threadsafe(self._events.publish("sensor.gps", {"ts": ts, **data}), loop)
        except Exception as exc:
            logger.warning("GPS reader error: %s", exc)

    async def _run(self) -> None:
        # Run blocking reader in a thread
        loop = asyncio.get_running_loop()
        while True:
            await loop.run_in_executor(None, self._read_loop)
            await asyncio.sleep(1)


