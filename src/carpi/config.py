from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class AppConfig:
    log_dir: str
    db_path: str
    bme280_interval_s: float
    icm20948_interval_s: float
    gps_serial_port: str
    gps_baud: int
    fan_pwm_pin: int
    fan_default_duty: int


def load_config() -> AppConfig:
    load_dotenv(os.getenv("ENV_FILE", "/opt/carpi/.env"), override=False)

    log_dir = os.getenv("CARPI_LOG_DIR", "/var/log/carpi")
    db_path = os.getenv("CARPI_DB_PATH", "/opt/carpi/data/carpi.sqlite")
    bme280_interval_s = float(os.getenv("BME280_INTERVAL", "1.0"))
    icm20948_interval_s = float(os.getenv("ICM20948_INTERVAL", "0.0"))
    gps_serial_port = os.getenv("GPS_SERIAL_PORT", "/dev/ttyS0")
    gps_baud = int(os.getenv("GPS_BAUD", "9600"))
    fan_pwm_pin = int(os.getenv("FAN_PWM_PIN", "18"))
    fan_default_duty = int(os.getenv("FAN_DEFAULT_DUTY", "0"))

    return AppConfig(
        log_dir=log_dir,
        db_path=db_path,
        bme280_interval_s=bme280_interval_s,
        icm20948_interval_s=icm20948_interval_s,
        gps_serial_port=gps_serial_port,
        gps_baud=gps_baud,
        fan_pwm_pin=fan_pwm_pin,
        fan_default_duty=fan_default_duty,
    )



