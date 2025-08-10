from __future__ import annotations

import asyncio
import logging
import signal

from carpi.config import load_config
from carpi.logging_setup import setup_logging
from carpi.event_bus import EventBus
from carpi.storage.db import Database

from carpi.modules.sensors.bme280 import BME280Reader
from carpi.modules.sensors.icm20948 import ICM20948Reader
from carpi.modules.sensors.gps import GPSReader
from carpi.modules.sensors.fan import FanController

from carpi.modules.audio.mixer import AudioMixer
from carpi.modules.audio.input_audio import InputAudio
from carpi.modules.bluetooth.bt import BluetoothManager
from carpi.modules.navigation.nav import Navigation
from carpi.modules.music.music import MusicPlayer
from carpi.modules.storage.ssd import SSDManager
from carpi.modules.web.server import WebServer


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
    # System/local input
    input_audio = InputAudio(events, device="default", topic="audio.input")
    input_audio.start()
    # Phone A2DP/HFP capture (if routed to an ALSA capture device)
    phone_audio = InputAudio(events, device=cfg.phone_alsa_device, topic="audio.phone")
    phone_audio.start()

    bt = BluetoothManager(events, db, alias=cfg.bt_alias, make_discoverable=True, make_pairable=True)
    bt.start()

    nav = Navigation(events)
    nav.start()

    music = MusicPlayer(events)
    music.start()

    ssd = SSDManager(events)
    ssd.start()

    webserver = WebServer(events, db)
    webserver.start()

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
    await input_audio.stop()
    await phone_audio.stop()
    await bt.stop()
    await nav.stop()
    await music.stop()
    await ssd.stop()
    await webserver.stop()
    fan.stop()
    await db.stop()
    logger.info("CarPi shut down")


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass





