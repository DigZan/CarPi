from __future__ import annotations

import logging
from typing import Optional

try:
    from gpiozero import PWMOutputDevice, DigitalOutputDevice
    from gpiozero.exc import PinPWMUnsupported
    try:
        from gpiozero.pins.pigpio import PiGPIOFactory  # type: ignore
    except Exception:  # pragma: no cover
        PiGPIOFactory = None  # type: ignore
except Exception:  # pragma: no cover - on non-Pi hosts
    PWMOutputDevice = None  # type: ignore
    DigitalOutputDevice = None  # type: ignore
    PinPWMUnsupported = Exception  # type: ignore
    PiGPIOFactory = None  # type: ignore

logger = logging.getLogger(__name__)


class FanController:
    def __init__(self, pwm_pin_bcm: int, default_duty: int = 0) -> None:
        self._pwm_pin = pwm_pin_bcm
        self._device: Optional[object] = None
        self._default_duty = max(0, min(100, default_duty))
        self._is_pwm: bool = False

    def start(self) -> None:
        if PWMOutputDevice is None:
            logger.warning("gpiozero not available; fan disabled")
            return
        # Try pigpio-based PWM first (recommended on Bookworm)
        if PiGPIOFactory is not None:
            try:
                factory = PiGPIOFactory()
                self._device = PWMOutputDevice(self._pwm_pin, frequency=25000, pin_factory=factory)
                self._is_pwm = True
            except Exception as exc:
                logger.warning("pigpio PWM unavailable (%s); falling back", exc)
                self._device = None
        # Fallback to default pin factory PWM
        if self._device is None:
            try:
                self._device = PWMOutputDevice(self._pwm_pin, frequency=25000)
                self._is_pwm = True
            except Exception as exc:  # includes PinPWMUnsupported
                logger.warning("PWM not supported on BCM %s (%s); using on/off control", self._pwm_pin, exc)
                if 'DigitalOutputDevice' in globals() and DigitalOutputDevice is not None:
                    try:
                        self._device = DigitalOutputDevice(self._pwm_pin, active_high=True, initial_value=False)
                        self._is_pwm = False
                    except Exception as ex2:
                        logger.warning("Failed to initialize digital output for fan: %s", ex2)
                        self._device = None
                else:
                    self._device = None
        if self._device is None:
            logger.warning("Fan disabled; no usable GPIO driver found")
            return
        self.set_duty_percent(self._default_duty)
        logger.info("Fan controller initialized on BCM %s (%s)", self._pwm_pin, "PWM" if self._is_pwm else "ON/OFF")
        # Subscribe to fan.set events for runtime control
        try:
            import asyncio
            from ...event_bus import EventBus  # type: ignore
            # Best-effort subscription when used within app context
            loop = asyncio.get_running_loop()
            loop.create_task(self._listen_commands())
        except Exception:
            pass

    async def _listen_commands(self) -> None:
        try:
            from ...event_bus import EventBus  # type: ignore
        except Exception:
            return
        # This import will be resolved in app context where EventBus is available on module path
        # We need a reference to the global bus; in current design, FanController is given events in main.
        # So this placeholder does nothing if not wired. Left for future.
        return

    def stop(self) -> None:
        if self._device and hasattr(self._device, "close"):
            try:
                self._device.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._device = None

    def set_duty_percent(self, duty: int) -> None:
        duty = max(0, min(100, duty))
        if not self._device:
            return
        try:
            if self._is_pwm:
                # Map: 0% -> 0.0, 1% -> 0.5, 100% -> 1.0, linear from 1..100
                pwm_value = 0.0 if duty == 0 else (0.5 + (duty - 1) * (0.5 / 99.0))
                pwm_value = max(0.0, min(1.0, pwm_value))
                # type: ignore[attr-defined]
                self._device.value = pwm_value
            else:
                # ON/OFF fallback: off at 0%, on for any >=1%
                # type: ignore[attr-defined]
                self._device.value = 1.0 if duty >= 1 else 0.0
            logger.info("Fan duty set to %d%% (value=%.3f)", duty, getattr(self._device, 'value', -1))
        except Exception as exc:
            logger.warning("Failed to set fan duty: %s", exc)





