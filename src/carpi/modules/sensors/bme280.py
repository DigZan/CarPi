from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from smbus2 import SMBus  # type: ignore[reportMissingImports]
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
        # Read compensated values per BME280 datasheet
        try:
            with SMBus(self._i2c_bus_num) as bus:
                # ctrl_hum = x1 oversampling, ctrl_meas = temp x1, press x1, normal mode
                bus.write_byte_data(self._address, 0xF2, 0x01)
                bus.write_byte_data(self._address, 0xF4, 0x27)

                # Read calibration data
                calib = bus.read_i2c_block_data(self._address, 0x88, 26)
                calib_h1 = bus.read_byte_data(self._address, 0xA1)
                calib2 = bus.read_i2c_block_data(self._address, 0xE1, 7)

                def u16(msb, lsb):
                    return (msb << 8) | lsb

                def s16(msb, lsb):
                    val = (msb << 8) | lsb
                    return val - 65536 if val & 0x8000 else val

                dig_T1 = u16(calib[1], calib[0])
                dig_T2 = s16(calib[3], calib[2])
                dig_T3 = s16(calib[5], calib[4])
                dig_P1 = u16(calib[7], calib[6])
                dig_P2 = s16(calib[9], calib[8])
                dig_P3 = s16(calib[11], calib[10])
                dig_P4 = s16(calib[13], calib[12])
                dig_P5 = s16(calib[15], calib[14])
                dig_P6 = s16(calib[17], calib[16])
                dig_P7 = s16(calib[19], calib[18])
                dig_P8 = s16(calib[21], calib[20])
                dig_P9 = s16(calib[23], calib[22])
                dig_H1 = calib_h1
                dig_H2 = s16(calib2[1], calib2[0])
                dig_H3 = calib2[2]
                e4 = calib2[3]
                e5 = calib2[4]
                e6 = calib2[5]
                dig_H4 = (e4 << 4) | (e5 & 0x0F)
                dig_H5 = (e6 << 4) | (e5 >> 4)
                if dig_H4 & 0x800:  # sign extend 12-bit
                    dig_H4 -= 1 << 12
                if dig_H5 & 0x800:
                    dig_H5 -= 1 << 12
                dig_H6 = calib2[6]
                if dig_H6 & 0x80:
                    dig_H6 -= 256

                # Read raw measurements
                data = bus.read_i2c_block_data(self._address, 0xF7, 8)
                adc_p = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
                adc_t = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
                adc_h = (data[6] << 8) | data[7]

                # Temperature compensation
                var1 = ((adc_t / 16384.0) - (dig_T1 / 1024.0)) * dig_T2
                var2 = (((adc_t / 131072.0) - (dig_T1 / 8192.0)) * ((adc_t / 131072.0) - (dig_T1 / 8192.0))) * dig_T3
                t_fine = var1 + var2
                temperature_c = t_fine / 5120.0

                # Pressure compensation
                var1p = (t_fine / 2.0) - 64000.0
                var2p = var1p * var1p * (dig_P6 / 32768.0)
                var2p = var2p + var1p * dig_P5 * 2.0
                var2p = (var2p / 4.0) + (dig_P4 * 65536.0)
                var1p = (dig_P3 * var1p * var1p / 524288.0 + dig_P2 * var1p) / 524288.0
                var1p = (1.0 + var1p / 32768.0) * dig_P1
                if var1p == 0:
                    pressure_hpa = 0.0
                else:
                    p = 1048576.0 - adc_p
                    p = (p - (var2p / 4096.0)) * 6250.0 / var1p
                    var1pp = dig_P9 * p * p / 2147483648.0
                    var2pp = p * dig_P8 / 32768.0
                    pressure_pa = p + (var1pp + var2pp + dig_P7) / 16.0
                    pressure_hpa = pressure_pa / 100.0

                # Humidity compensation
                h = t_fine - 76800.0
                if h == 0:
                    humidity_rh = 0.0
                else:
                    hum = (adc_h - (dig_H4 * 64.0 + dig_H5 / 16384.0 * h)) * (dig_H2 / 65536.0 * (1.0 + dig_H6 / 67108864.0 * h * (1.0 + dig_H3 / 67108864.0 * h)))
                    hum = hum * (1.0 - dig_H1 * hum / 524288.0)
                    humidity_rh = max(0.0, min(100.0, hum))

                return {"temperature_c": float(temperature_c), "pressure_hpa": float(pressure_hpa), "humidity_rh": float(humidity_rh)}
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


