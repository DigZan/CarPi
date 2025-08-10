from __future__ import annotations

import asyncio
import datetime as dt
import logging

from smbus2 import SMBus

from ...event_bus import EventBus
from ...storage.db import Database

logger = logging.getLogger(__name__)


class ICM20948Reader:
    def __init__(self, bus: int, address: int, interval_s: float, db: Database, bus_events: EventBus) -> None:
        self._i2c_bus_num = bus
        self._address = address
        self._interval_s = interval_s  # 0 means as fast as possible
        self._db = db
        self._events = bus_events
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="icm20948-reader")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _read_fast_raw(self) -> dict[str, float] | None:
        try:
            with SMBus(self._i2c_bus_num) as bus:
                who = bus.read_byte_data(self._address, 0x00)  # WHO_AM_I
                return {"who_am_i": float(who)}
        except Exception as exc:
            logger.debug("ICM20948 read failed: %s", exc)
            return None

    async def _run(self) -> None:
        logger.info("ICM-20948 reader started (bus=%s addr=0x%02X interval=%s)", self._i2c_bus_num, self._address, self._interval_s)
        sleep_s = self._interval_s if self._interval_s > 0 else 0
        while True:
            ts = dt.datetime.utcnow().isoformat()
            values = await asyncio.to_thread(self._read_fast_raw)
            if values is not None:
                await self._db.insert_sensor_reading("icm20948", ts, values)
                await self._events.publish("sensor.icm20948", {"ts": ts, **values})
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
            else:
                await asyncio.sleep(0)


