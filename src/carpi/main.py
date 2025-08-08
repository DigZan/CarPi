from __future__ import annotations

import asyncio
import logging
import signal

from .config import load_config
from .logging_setup import setup_logging
from .event_bus import EventBus
from .storage.db import Database

from .modules.sensors.bme280 import BME280Reader
from .modules.sensors.icm20948 import ICM20948Reader
from .modules.sensors.gps import GPSReader
from .modules.sensors.fan import FanController

from .modules.audio.mixer import AudioMixer
from .modules.bluetooth.bt import BluetoothManager
from .modules.navigation.nav import Navigation
from .modules.music.music import MusicPlayer


async def main_async() -> None:
    cfg = load_config()
    setup_logging(cfg.log_dir)
    logger = logging.getLogger("carpi")
    logger.info("CarPi starting up")

    events = EventBus()
    db = Database(cfg.db_path)
    await db.start()

    # Initialize modules
    fan = FanController(cfg.fan_pwm_pin, cfg.fan_default_duty)
    fan.start()

    bme = BME280Reader(bus=1, address=0x76, interval_s=cfg.bme280_interval_s, db=db, bus_events=events)
    bme.start()

    icm = ICM20948Reader(bus=1, address=0x68, interval_s=cfg.icm20948_interval_s, db=db, bus_events=events)
    icm.start()

    gps = GPSReader(cfg.gps_serial_port, cfg.gps_baud, db, events)
    gps.start()

    mixer = AudioMixer(events)
    mixer.start()

    bt = BluetoothManager(events)
    bt.start()

    nav = Navigation(events)
    nav.start()

    music = MusicPlayer(events)
    music.start()

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows fallback
            pass

    await stop_event.wait()

    # Graceful shutdown
    await icm.stop()
    await bme.stop()
    await gps.stop()
    await mixer.stop()
    await bt.stop()
    await nav.stop()
    await music.stop()
    fan.stop()
    await db.stop()
    logger.info("CarPi shut down")


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass




