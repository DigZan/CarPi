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
                # Wake up, set up basic measurement config and read raw accel/gyro
                # PWR_MGMT_1: clear sleep bit
                bus.write_byte_data(self._address, 0x06, 0x01)
                # Configure accelerometer and gyro to some defaults
                # ACCEL_CONFIG (0x14): +/- 2g (0), GYRO_CONFIG (0x01): 250 dps (0)
                # Some ICM-20948 variants use banked registers; keep minimal for demo
                # Read accel XYZ (high/low)
                def read_word(h_reg: int, l_reg: int) -> int:
                    msb = bus.read_byte_data(self._address, h_reg)
                    lsb = bus.read_byte_data(self._address, l_reg)
                    val = (msb << 8) | lsb
                    return val - 65536 if val & 0x8000 else val

                # Addresses for accel/gyro may vary with bank; these are placeholders for demo
                ax = read_word(0x2D, 0x2E)
                ay = read_word(0x2F, 0x30)
                az = read_word(0x31, 0x32)
                gx = read_word(0x33, 0x34)
                gy = read_word(0x35, 0x36)
                gz = read_word(0x37, 0x38)
                # Convert to units (very rough, for display only)
                ax_g = ax / 16384.0
                ay_g = ay / 16384.0
                az_g = az / 16384.0
                gx_dps = gx / 131.0
                gy_dps = gy / 131.0
                gz_dps = gz / 131.0
                return {
                    "accel_g": {"x": float(ax_g), "y": float(ay_g), "z": float(az_g)},
                    "gyro_dps": {"x": float(gx_dps), "y": float(gy_dps), "z": float(gz_dps)},
                }
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


