# MeshCore Broadcast System

**Automated information broadcasting for LoRa mesh networks via MeshCore**

This suite of Python scripts enables automated broadcasting of weather, severe weather alerts, earthquake, solar propagation, SOTA/POTA spots, and calendar notifications to MeshCore mesh radio channels. Built for ham radio operators running emergency communications networks.

**Operator**: W6SAL (Sal Mancuso)  
**Primary Use**: San Jose Simplex Group, West Valley Amateur Radio Association (WVARA)

---

## Table of Contents

- [System Overview](#system-overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Finding Your Bluetooth Address](#finding-your-devices-bluetooth-address)
- [Configuration](#configuration)
- [Core Message Utility](#core-message-utility)
- [Broadcast Modules](#broadcast-modules)
- [Automated Scheduling](#automated-scheduling)
- [Hardware & Platform Notes](#hardware--platform-notes)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## System Overview

The MeshCore Broadcast System consists of specialized Python scripts that fetch real-time data from public APIs and transmit formatted messages to LoRa mesh radio channels via MeshCore devices. All scripts share a common connection infrastructure (`meshcore_send.py`) and are designed for unattended operation via cron.

### Core Components

1. **`meshcore_send.py`** — Core messaging infrastructure (this README)
2. **`weather_broadcast.py`** — Bay Area weather reports ([README](README_weather.md))
3. **`skywarn_broadcast.py`** — NWS severe weather alerts ([README](README_skywarn.md))
4. **`earthquake_broadcast.py`** — USGS seismic monitoring ([README](README_earthquake.md))
5. **`solar_broadcast.py`** — HF/VHF propagation forecasts ([README](README_solar.md))
6. **`sotapota_broadcast.py`** — SOTA/POTA activator spots ([README](README_sotapota.md))
7. **`calendar_broadcast.py`** — Ham radio event notifications ([README](README_calendar.md))

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   meshcore_send.py                      │
│  ┌───────────────────────────────────────────────────┐  │
│  │  • BLE/Serial Connection Manager                  │  │
│  │  • Channel Secret Resolution                      │  │
│  │  • Message Validation (135 byte limit)            │  │
│  │  • Environment Variable Configuration             │  │
│  └───────────────────────────────────────────────────┘  │
└──────────────────┬──────────────────────────────────────┘
                   │ (imported by)
       ┌───────────┼─────────┬─────────┬──────────┐
       │     │     │         │         │          │
  ┌────▼──┐┌─▼────┐┌──▼────┐┌──▼────┐┌──▼─────┐┌──▼─────┐
  │Weather││Skywrn││Quake  ││Solar  ││SOTA/   ││Calendar│
  │Bcast  ││Bcast ││Monitor││Prop   ││POTA    ││Events  │
  └───┬───┘└──┬───┘└──┬────┘└──┬────┘└──┬─────┘└──┬─────┘
      │       │       │        │        │         │
 ┌────▼───────▼───────▼────────▼────────▼─────────▼──┐
 │              Data Sources (APIs)                  │
 │  • Weather Underground (weather)                  │
 │  • NWS api.weather.gov (Skywarn alerts)           │
 │  • USGS Earthquake Hazards (quakes)               │
 │  • HamQSL/N0NBH (solar/HF bands)                  │
 │  • Open-Meteo (tropo ducting)                     │
 │  • POTA/SOTA APIs (activator spots)               │
 │  • Google Sheets CSV (calendar)                   │
 └───────────────────┬───────────────────────────────┘
                     ▼
      ┌──────────────────────────────────────┐
      │      MeshCore Device (LoRa)          │
      │  • T-Deck, Heltec, RAK, etc.         │
      │  • BLE or USB Serial connection      │
      └──────────────────┬───────────────────┘
                         ▼
      ┌──────────────────────────────────────┐
      │    LoRa Mesh Network Channels        │
      │  • MeshHams, San Jose Simplex        │
      │  • WVARA, Custom channels            │
      └──────────────────────────────────────┘
```

### How Components Interact

All broadcast scripts import connection functions, channel definitions, and constants from `meshcore_send.py` and follow this standardized flow:

```python
fetch_data()          # Get data from external APIs
format_messages()     # Build 135-byte compliant messages
connect()             # Establish MeshCore connection
resolve_channel()     # Match channel by secret
send_messages()       # Transmit with error handling
disconnect()          # Clean up connection
```

Channel resolution matches by `channel_secret` hash first (most reliable), then falls back to name matching. This ensures reliable transmission even when device channel slots change.

---

## Installation

### Requirements

- Python 3.9 or higher
- MeshCore-compatible LoRa device (T-Deck, Heltec V3, RAK4631, etc.)
- Either Bluetooth LE or USB serial connection to device

### Install Dependencies

```bash
pip install -r requirements.txt
```

The `requirements.txt` includes:
```
requests>=2.31.0
meshcore>=0.1.0
pandas>=2.0.0
```

### Platform-Specific Setup

#### macOS
```bash
pip3 install -r requirements.txt

# Find your device's BLE address (optional, can auto-discover)
system_profiler SPBluetoothDataType | grep -A 10 "MeshCore"

# Or find serial port if using USB
ls /dev/tty.usbmodem*
```

#### Linux (Raspberry Pi, Ubuntu, etc.)
```bash
sudo apt-get update
sudo apt-get install python3-pip python3-venv bluez bluetooth

# Create virtual environment (recommended)
python3 -m venv ~/meshcore-env
source ~/meshcore-env/bin/activate

pip install -r requirements.txt

# Add user to dialout group for serial access
sudo usermod -a -G dialout $USER
# Log out and back in for group changes to take effect

# Find serial device
ls /dev/meshcore      # udev symlink
ls /dev/ttyACM* /dev/ttyUSB*   # fallback
```

---

## Finding Your Device's Bluetooth Address

Before using BLE mode, you need your MeshCore device's Bluetooth address.

### macOS

1. **Pair your device**: System Settings → Bluetooth → Connect when it appears
2. **Open System Information**: Option-click Bluetooth icon in menu bar
3. **Find your device** under Devices/Paired Devices — copy the Address field
4. **Set it**: `export MESHCORE_BLE_ADDR="YOUR-ADDRESS-HERE"`

### Linux

```bash
bluetoothctl
[bluetooth]# scan on
# Look for your device, e.g.:
# [NEW] Device 73:AE:A3:10:1B:52 MeshCore
[bluetooth]# scan off
[bluetooth]# exit
```

### Windows

1. Settings → Bluetooth & devices → pair your device
2. Device Manager → Bluetooth → right-click device → Properties → Details tab
3. Property dropdown → "Bluetooth device address"
4. Set: `$env:MESHCORE_BLE_ADDR="YOUR-ADDRESS-HERE"`

### Auto-Discovery (No Address Needed)

```bash
export MESHCORE_MODE=ble
# Don't set MESHCORE_BLE_ADDR — library auto-discovers
python meshcore_send.py --list-channels
```

Note: Auto-discovery takes 10-15 seconds longer on first connection.

---

## Configuration

### Environment Variables

All scripts respect these environment variables:

```bash
# Connection mode: "ble" or "serial"
export MESHCORE_MODE=ble

# BLE Configuration
export MESHCORE_BLE_ADDR="YOUR-ADDRESS-HERE"
export MESHCORE_PIN="123456"  # Optional pairing PIN

# Serial Configuration
export MESHCORE_MODE=serial
export MESHCORE_PORT="/dev/tty.usbmodem90706983BBCC1"  # macOS
# export MESHCORE_PORT="/dev/meshcore"                 # Linux (udev)
export MESHCORE_BAUD=115200
```

**Recommendation**: Add these to `~/.bashrc` or `~/.zshrc`, or create a `.env` file.

### Channel Configuration

Channels are defined in `meshcore_send.py` with their unique secrets:

```python
CHANNELS = {
    "meshhams":          {"name": "MeshHams",          "secret": "a7408e..."},
    "sanjosesimplex":    {"name": "San Jose Simplex",  "secret": "9f47b0..."},
    "wvara":             {"name": "WVARA",             "secret": "a9e971..."},
    "weather":           {"name": "weather",           "secret": "88f502..."},
}
```

To add new channels:
1. Configure the channel on your device via MeshCore app
2. Run `python meshcore_send.py --list-channels` to view all device channels and secrets
3. Add the channel to the `CHANNELS` dict with its exact name and secret

---

## Core Message Utility

### Send a Message
```bash
python meshcore_send.py --channel meshhams "Test message from W6SAL"
python meshcore_send.py -c sanjosesimplex "Checking into the net"
```

### List Channels
```bash
python meshcore_send.py --list-channels
```

Output shows all configured channels on your device with slot numbers and secrets.

### Message Constraints

All messages are limited to **135 bytes** (UTF-8 encoded). This is a hard constraint from MeshCore/Meshtastic firmware. All broadcast scripts auto-truncate, but use `--dry-run` to preview messages before transmitting.

---

## Broadcast Modules

Each module has its own detailed README:

| Module | Description | Data Source | API Key? | README |
|--------|-------------|-------------|----------|--------|
| `weather_broadcast.py` | Bay Area weather reports | Weather Underground | Yes (free) | [README_weather.md](README_weather.md) |
| `skywarn_broadcast.py` | NWS severe weather alerts | NWS api.weather.gov | No | [README_skywarn.md](README_skywarn.md) |
| `earthquake_broadcast.py` | Seismic monitoring | USGS | No | [README_earthquake.md](README_earthquake.md) |
| `solar_broadcast.py` | HF/VHF propagation | HamQSL / Open-Meteo | No | [README_solar.md](README_solar.md) |
| `sotapota_broadcast.py` | SOTA/POTA activator spots | SOTA/POTA APIs | No | [README_sotapota.md](README_sotapota.md) |
| `calendar_broadcast.py` | Event notifications | Google Sheets CSV | No | [README_calendar.md](README_calendar.md) |

---

## Automated Scheduling

All scripts are designed for unattended operation via cron.

### Recommended Crontab

```bash
crontab -e

# Weather broadcasts: Every 6 hours
0 6,12,18,0 * * * /usr/bin/python3 /home/sal/meshcore/weather_broadcast.py --channel meshhams

# Skywarn alerts: Every 15 minutes
*/15 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zip 95125 --radius 50 --channel meshhams

# Earthquake monitoring: Every 30 minutes
*/30 * * * * /usr/bin/python3 /home/sal/meshcore/earthquake_broadcast.py --hours 2 --channel meshhams

# Solar propagation: Every 3 hours
0 7,10,13,16,19,22 * * * /usr/bin/python3 /home/sal/meshcore/solar_broadcast.py --channel meshhams

# Solar with VHF: Once daily at 07:00
0 7 * * * /usr/bin/python3 /home/sal/meshcore/solar_broadcast.py --vhf --channel meshhams

# SOTA/POTA spots: Every hour
0 * * * * /usr/bin/python3 /home/sal/meshcore/sotapota_broadcast.py --band vhf --channel meshhams

# Calendar notifications: Every 15 minutes
*/15 * * * * /usr/bin/python3 /home/sal/meshcore/calendar_broadcast.py

# Morning summary: Daily at 06:00
0 6 * * * /usr/bin/python3 /home/sal/meshcore/weather_broadcast.py --channel meshhams && sleep 30 && /usr/bin/python3 /home/sal/meshcore/solar_broadcast.py --vhf --channel meshhams
```

### Schedule Recommendations

**Weather**: 4-6 times daily. More frequent updates waste airtime.

**Skywarn**: Every 15 minutes during severe weather season, every 30-60 minutes otherwise. NWS alerts update in near real-time.

**Earthquakes**: Every 30 minutes with `--hours 2` to avoid re-transmitting. For serious monitoring, 15-minute intervals with `--minmag 3.0`.

**Solar**: Every 3 hours aligns with propagation shifts. Daily `--vhf` for slower-changing VHF conditions.

**SOTA/POTA**: Hourly is reasonable for spot monitoring. Increase during contest weekends.

**Calendar**: Every 15 minutes catches 24h and 2h notification windows. State management prevents duplicates.

### Logging

```bash
mkdir -p ~/meshcore/logs

# Updated crontab with logging:
*/15 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zip 95125 --radius 50 --channel meshhams >> ~/meshcore/logs/skywarn.log 2>&1
0 6,12,18,0 * * * /usr/bin/python3 /home/sal/meshcore/weather_broadcast.py --channel meshhams >> ~/meshcore/logs/weather.log 2>&1
*/30 * * * * /usr/bin/python3 /home/sal/meshcore/earthquake_broadcast.py --hours 2 --channel meshhams >> ~/meshcore/logs/earthquake.log 2>&1
```

Log rotation with `logrotate`:
```bash
# Create /etc/logrotate.d/meshcore
/home/sal/meshcore/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

---

## Hardware & Platform Notes

### Supported Platforms

- ✅ **macOS** (Apple Silicon and Intel)
- ✅ **Linux** (Ubuntu 22.04+, Raspberry Pi OS Bookworm)
- ✅ **Raspberry Pi** (Pi 4, Pi Zero W, Pi Zero 2 W — with considerations below)

### Supported Devices

- Lilygo T-Deck (recommended for field use)
- Heltec V3 / WiFi LoRa 32 V3
- RAK4631 WisBlock
- Any MeshCore firmware-compatible device

### Connection Methods

**Bluetooth LE (BLE)** — Recommended for mobility. No cables, ~10m range, slightly higher latency.

**USB Serial** — Recommended for fixed installations. Most reliable, lowest latency.

### Raspberry Pi Considerations

⚠️ Running on Raspberry Pi requires addressing three issues:

**Issue 1: Hardware Flow Control** — Linux enables `crtscts` by default, causing hangs.
```bash
# Fix via udev rule (persistent):
sudo tee /etc/udev/rules.d/99-meshcore.rules <<EOF
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", \
    SYMLINK+="meshcore", MODE="0666", RUN+="/bin/stty -F /dev/%k -crtscts"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

**Issue 2: Device File Race** — Scripts at boot start before udev creates `/dev/meshcore`.
```bash
# Use wait-for-device wrapper:
@reboot /home/sal/meshcore/wait-for-device.sh python3 /home/sal/meshcore/weather_broadcast.py
```

**Issue 3: Command Timeout** — MeshCore library's 3s timeout is too aggressive for Pi.
```python
# In meshcore library: change DEFAULT_TIMEOUT from 3 to 10
DEFAULT_TIMEOUT = 10.0
```

**Performance Expectations**:
- Pi Zero W: 5-8 seconds per message
- Pi 4: 3-4 seconds per message
- macOS: 2-3 seconds per message

---

## Troubleshooting

### Connection Issues

```bash
# Check device is powered and in range
bluetoothctl
[bluetooth]# scan on

# For serial
ls -l /dev/tty* | grep -i usb
ls -l /dev/meshcore

# Check environment
env | grep MESHCORE
```

**"Channel not found"** — Run `python meshcore_send.py --list-channels` and verify secrets match.

**Permission denied** — `sudo usermod -a -G dialout $USER` then log out/in.

### Message Issues

**"Message too long"** — All messages auto-truncate at 135 bytes. Use `--dry-run` to preview.

**Messages not appearing** — Verify channel, check subscriptions on receiving devices, confirm secrets match.

### Debugging

```bash
# Test connection manually:
python3 -c "
import asyncio
from meshcore import MeshCore

async def test():
    mc = await MeshCore.create_serial('/dev/meshcore', 115200)
    print('Connected!')
    await mc.disconnect()

asyncio.run(test())
"
```

---

## Contributing

This is a personal project for W6SAL's emergency communications operations. To adapt for your area:

1. Fork the repository and customize for your region
2. Update location constants (`SJC_LAT`/`SJC_LON`) in applicable scripts
3. Modify channels in `meshcore_send.py` to match your network
4. Adjust cron schedules based on your needs

Bug reports and feature suggestions welcome via GitHub issues.

---

## License

Released as open source for the amateur radio community. Use, modify, and distribute freely. No warranty provided.

**Sal Mancuso — W6SAL**

---

## Version History

- **v1.1** (Feb 2026) - Added Skywarn broadcast, SOTA/POTA spots, restructured documentation
- **v1.0** (Feb 2026) - Initial release with weather, earthquake, solar, and calendar broadcasts
