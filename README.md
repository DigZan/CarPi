## CarPi

Modular Raspberry Pi in-car system with sensors, audio mixing, Bluetooth, navigation, and logging. Designed for unattended boot, auto-update, and simple, idempotent installation.

### Features (initial baseline)
- Sensors module:
  - BME280 via I2C (0x76) sampled every 1s
  - ICM-20948 IMU via I2C (0x68 / magnetometer 0x0C) sampled as fast as possible
  - NEO-6M GPS via UART `/dev/ttyS0` (9600 baud), parsed on incoming NMEA
  - PWM fan control on GPIO 18
- SQLite time-series storage via `aiosqlite`
- Event bus for inter-module pub/sub
- Structured file logging with rotation
- Systemd service for auto-start at boot
- Systemd timer for auto-update from Git
- Stubs for Audio Mixer, Bluetooth, Navigation, Music modules (ready for future expansion)

### Hardware wiring (summary)
- BME280 (I2C @ 0x76): 3.3V, GND, SDA (GPIO2), SCL (GPIO3)
- ICM-20948 (I2C @ 0x68): 3.3V, GND, SDA (GPIO2), SCL (GPIO3)
- GPS NEO-6M (UART): TX -> GPIO15 (RX), RX -> GPIO14 (TX, optional), VCC 3.3V/5V per module
- PWM fan: control on GPIO18 via NPN transistor; fan powered separately; grounds common

### One-line install (on the Raspberry Pi)
Run in a terminal on Raspberry Pi OS (Bookworm recommended):

```bash
curl -fsSL https://raw.githubusercontent.com/yourname/CarPi/main/install.sh | sudo bash
```

This will:
- Install OS dependencies
- Create `/opt/carpi`, clone repo, and set up a Python venv
- Install Python requirements
- Configure and enable systemd services
- Enable hourly auto-update

Service will start automatically. Logs and DB:
- Logs: `/var/log/carpi/*.log`
- Database: `/opt/carpi/data/carpi.sqlite`

### Manage the service
```bash
sudo systemctl status carpi.service
sudo journalctl -u carpi.service -n 200 -f
sudo systemctl restart carpi.service
```

### Configuration
Copy `.env.example` to `.env` and adjust values. Defaults are suitable for typical Pi setups.

### Auto-update
The systemd timer runs hourly to pull from `main` if there are new commits. You can also update manually:

```bash
sudo /opt/carpi/update.sh
```

### Development notes
- The Audio Mixer, Bluetooth, Navigation, and Music modules are stubs. They are wired into the app but do not perform full functionality yet. They establish interfaces and event contracts so you can implement incrementally.
- Sensor modules are functional and persist to SQLite.

### Uninstall
```bash
sudo systemctl disable --now carpi.service carpi-update.timer
sudo rm -f /etc/systemd/system/carpi.service /etc/systemd/system/carpi-update.service /etc/systemd/system/carpi-update.timer
sudo systemctl daemon-reload
sudo userdel -r carpi 2>/dev/null || true
sudo rm -rf /opt/carpi /var/log/carpi
```



