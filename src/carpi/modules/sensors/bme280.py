from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from smbus2 import SMBus
import time

from ...event_bus import EventBus
from ...storage.db import Database

logger = logging.getLogger(__name__)


class BME280Reader:
    def __init__(self, bus: int, address: int, interval_s: float, db: Database, bus_events: EventBus) -> None:
        self._i2c_bus_num = bus
        self._address = address
        self._interval_s = max(0.1, interval_s)
        self._db = db
        self._events = bus_events
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="bme280-reader")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _read_raw(self) -> dict[str, float] | None:
        # Minimal BME280 reading via I2C. For robust compensation use a dedicated library.
        try:
            with SMBus(self._i2c_bus_num) as bus:
                # Soft reset and force mode with oversampling (quick-and-dirty)
                bus.write_byte_data(self._address, 0xE0, 0xB6)
                time.sleep(0.005)
                bus.write_byte_data(self._address, 0xF2, 0x01)  # humidity oversampling x1
                bus.write_byte_data(self._address, 0xF4, 0x27)  # temp/press oversampling x1, normal mode
                # Read raw data registers (uncompensated). Real values need calib compensation.
                data = bus.read_i2c_block_data(self._address, 0xF7, 8)
                # Placeholders: return raw bytes as integers for now
                pressure_raw = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
                temp_raw = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
                hum_raw = (data[6] << 8) | data[7]
                return {"temp_raw": float(temp_raw), "pressure_raw": float(pressure_raw), "humidity_raw": float(hum_raw)}
        except Exception as exc:
            logger.debug("BME280 read failed: %s", exc)
            return None

    async def _run(self) -> None:
        logger.info("BME280 reader started (bus=%s addr=0x%02X interval=%.2fs)", self._i2c_bus_num, self._address, self._interval_s)
        while True:
            ts = dt.datetime.utcnow().isoformat()
            values = await asyncio.to_thread(self._read_raw)
            if values is not None:
                await self._db.insert_sensor_reading("bme280", ts, values)
                await self._events.publish("sensor.bme280", {"ts": ts, **values})
            await asyncio.sleep(self._interval_s)


