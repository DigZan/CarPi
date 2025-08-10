from __future__ import annotations

import logging
from typing import Optional

try:
    from gpiozero import PWMOutputDevice
except Exception:  # pragma: no cover - on non-Pi hosts
    PWMOutputDevice = None  # type: ignore

logger = logging.getLogger(__name__)


class FanController:
    def __init__(self, pwm_pin_bcm: int, default_duty: int = 0) -> None:
        self._pwm_pin = pwm_pin_bcm
        self._device: Optional[PWMOutputDevice] = None
        self._default_duty = max(0, min(100, default_duty))

    def start(self) -> None:
        if PWMOutputDevice is None:
            logger.warning("gpiozero not available; fan disabled")
            return
        self._device = PWMOutputDevice(self._pwm_pin, frequency=25000)
        self.set_duty_percent(self._default_duty)
        logger.info("Fan controller initialized on BCM %s", self._pwm_pin)

    def stop(self) -> None:
        if self._device:
            self._device.close()
            self._device = None

    def set_duty_percent(self, duty: int) -> None:
        duty = max(0, min(100, duty))
        if not self._device:
            return
        self._device.value = duty / 100.0
        logger.info("Fan duty set to %d%%", duty)





