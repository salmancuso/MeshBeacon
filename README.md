# MeshBeacon

**Turn your MeshCore node into an automated information beacon — weather, earthquakes, HF/VHF propagation, SOTA/POTA spots, and community events broadcast to your LoRa mesh.**

MeshBeacon is a suite of Python scripts that fetch live data from public APIs and broadcast formatted messages to LoRa mesh radio channels via MeshCore. Designed for ham radio operators running emergency communications and community information networks — no internet required on the receiving end.

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/youruser/meshbeacon.git
cd meshbeacon

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy example config files and fill in your credentials
cp meshcore.keys.example meshcore.keys
cp weather.keys.example weather.keys
cp calendar.keys.example calendar.keys

# 4. Edit meshcore.keys with your device address and channel secrets
#    (see "Finding Your Bluetooth Address" below)
nano meshcore.keys

# 5. Test your connection
python meshcore_send.py --list-channels

# 6. Send a test message
python meshcore_send.py --channel myhams "Hello from MeshBeacon!"

# 7. Try a dry run of any broadcast script
python weather_broadcast.py --dry-run
python solar_broadcast.py --dry-run
python earthquake_broadcast.py --dry-run
```

---

## Table of Contents

- [Quick Start](#quick-start)
- [System Overview](#system-overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Getting a Weather Underground API Key](#getting-a-weather-underground-api-key)
- [Finding Your Bluetooth Address](#finding-your-bluetooth-address)
- [Usage Guide](#usage-guide)
- [Automated Scheduling (Cron)](#automated-scheduling-cron)
- [Scheduling on macOS and Windows](#scheduling-on-macos-and-windows)
- [Hardware & Platform Notes](#hardware--platform-notes)
- [Troubleshooting](#troubleshooting)
- [Data Sources](#data-sources)
- [Message Format](#message-format)
- [Contributing](#contributing)

---

## System Overview

The MeshBeacon system consists of six specialized Python scripts that fetch real-time data from public APIs and transmit formatted messages to LoRa mesh radio channels via MeshCore devices. All scripts share a common connection infrastructure and are designed for unattended operation via cron jobs or scheduled tasks.

### Core Components

| Script | Purpose | Data Source | API Key Required? |
|--------|---------|-------------|-------------------|
| `meshcore_send.py` | Core messaging infrastructure | — | No |
| `weather_broadcast.py` | Bay Area weather reports | Weather Underground | **Yes** (free) |
| `earthquake_broadcast.py` | USGS seismic monitoring | USGS | No |
| `solar_broadcast.py` | HF/VHF propagation forecasts | HamQSL / Open-Meteo | No |
| `calendar_broadcast.py` | Ham radio event notifications | Google Sheets CSV | No |
| `sotapota_broadcast.py` | SOTA/POTA activator spots | SOTA API / POTA API | No |

### Credential Files

All secrets, API keys, and connection details are kept in `.keys` files that are excluded from version control via `.gitignore`. Example files are provided for each:

| Config File | Example File | What It Stores |
|-------------|--------------|----------------|
| `meshcore.keys` | `meshcore.keys.example` | BLE/serial connection, channel names & secrets |
| `weather.keys` | `weather.keys.example` | Weather Underground API key, PWS stations, cities |
| `calendar.keys` | `calendar.keys.example` | Google Sheets published CSV URL |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   meshcore_send.py                      │
│  ┌───────────────────────────────────────────────────┐  │
│  │  • BLE/Serial Connection Manager                  │  │
│  │  • Channel Secret Resolution                      │  │
│  │  • Message Validation (135 byte limit)            │  │
│  │  • Config loaded from meshcore.keys               │  │
│  └───────────────────────────────────────────────────┘  │
└──────────────────┬──────────────────────────────────────┘
                   │ (imported by)
         ┌─────────┼─────────┬─────────┬──────────┐
         │         │         │         │          │
    ┌────▼───┐ ┌──▼────┐ ┌──▼────┐ ┌──▼─────┐ ┌──▼─────┐
    │Weather │ │Quake  │ │Solar  │ │Calendar│ │SOTA/   │
    │Brdcast │ │Monitor│ │Prop   │ │Events  │ │POTA    │
    └────┬───┘ └──┬────┘ └──┬────┘ └──┬─────┘ └──┬─────┘
         │        │         │         │           │
    ┌────▼────────▼─────────▼─────────▼───────────▼─┐
    │      Data Sources (APIs)                      │
    │  • Weather Underground (weather)              │
    │  • Open-Meteo (tropo data)                    │
    │  • USGS Earthquake Hazards                    │
    │  • HamQSL/N0NBH (solar/HF bands)             │
    │  • Google Sheets (calendar CSV)               │
    │  • POTA API (park activator spots)            │
    │  • SOTA API (summit activator spots)          │
    └───────────────────────────────────────────────┘
                   │
                   ▼
    ┌──────────────────────────────────────┐
    │      MeshCore Device (LoRa)          │
    │  • T-Deck, Heltec, RAK, etc.        │
    │  • BLE or USB Serial connection      │
    └──────────────────────────────────────┘
                   │
                   ▼
    ┌──────────────────────────────────────┐
    │    LoRa Mesh Network Channels        │
    └──────────────────────────────────────┘
```

### How It Works

Each broadcast script follows the same pattern:

```python
fetch_data()          # Get data from external APIs
format_messages()     # Build 135-byte compliant messages
connect()             # Establish MeshCore connection
resolve_channel()     # Match channel by secret
send_messages()       # Transmit with error handling
disconnect()          # Clean up connection
```

Channel resolution matches channels by their unique `channel_secret` hash rather than assuming slot indices, ensuring reliable transmission even when device channel slots change.

---

## Installation

### Requirements

- Python 3.9 or higher
- MeshCore-compatible LoRa device (T-Deck, Heltec V3, RAK4631, etc.)
- Bluetooth LE or USB serial connection to device

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

# Find serial port if using USB
ls /dev/tty.usbmodem*
```

#### Linux (Raspberry Pi, Ubuntu, etc.)

```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install python3-pip python3-venv bluez bluetooth

# Create virtual environment (recommended)
python3 -m venv ~/meshcore-env
source ~/meshcore-env/bin/activate

# Install Python packages
pip install -r requirements.txt

# Add user to dialout group for serial access
sudo usermod -a -G dialout $USER
# Log out and back in for group changes to take effect
```

---

## Configuration

### meshcore.keys — Connection & Channels

This is the primary configuration file. Copy `meshcore.keys.example` to `meshcore.keys` and edit it:

```bash
cp meshcore.keys.example meshcore.keys
```

The file contains your connection mode (BLE or serial), device address, and channel definitions. Each channel entry has three pipe-separated fields:

```
CHANNEL = key | Display Name | channel_secret_hex
```

To discover your channel secrets, connect to your device and run:

```bash
python meshcore_send.py --list-channels
```

### weather.keys — Weather Underground Config

Copy `weather.keys.example` to `weather.keys` and add your API key and station/city definitions. See [Getting a Weather Underground API Key](#getting-a-weather-underground-api-key) below.

### calendar.keys — Calendar Events URL

Copy `calendar.keys.example` to `calendar.keys` and set the `EVENTS_CSV_URL` to your published Google Sheet. See the [Calendar Notifications](#calendar-notifications) usage section for spreadsheet setup instructions.

### Environment Variable Overrides

Connection settings in `meshcore.keys` can be overridden by environment variables. This is useful for running on different machines without changing the config file:

```bash
export MESHCORE_MODE=ble
export MESHCORE_BLE_ADDR="YOUR-DEVICE-ADDRESS"
export MESHCORE_PIN="123456"        # Optional BLE PIN
export MESHCORE_PORT="/dev/ttyACM0" # For serial mode
export MESHCORE_BAUD=115200
```

Environment variables take priority over values in `meshcore.keys`.

---

## Getting a Weather Underground API Key

The weather broadcast script uses the Weather Underground API, which requires a free API key. Here's how to get one:

1. **Go to** [wunderground.com/member/api-keys](https://www.wunderground.com/member/api-keys)

2. **Create a free account** (or sign in if you already have one). If you own a Personal Weather Station (PWS), register it — PWS owners get enhanced API access.

3. **Generate an API key** on the API Keys page. The free tier provides 1,500 calls per day and 30 calls per minute, which is more than sufficient for scheduled broadcasts.

4. **Copy your key** and paste it into `weather.keys`:
   ```
   WU_API_KEY = your_actual_api_key_here
   ```

5. **Add your stations and/or cities** to `weather.keys`:
   ```
   # PWS stations give the best data (live observations + forecast)
   STATION = KCASTATION1 | My Backyard

   # Cities use forecast data only (no live observations)
   CITY = San Jose | 37.3239 | -121.8921
   ```

6. **Find PWS station IDs** near you at [wunderground.com/wundermap](https://www.wunderground.com/wundermap). Click on any weather station pin to see its ID (format: `KCASTATION123`).

7. **Test your configuration**:
   ```bash
   python weather_broadcast.py --dry-run
   ```

---

## Finding Your Bluetooth Address

Before using BLE mode, you need your MeshCore device's Bluetooth address.

### macOS

1. **Pair your device**: Open **System Settings** → **Bluetooth**, power on your MeshCore device, and click **Connect**.
2. **Find the address**: Hold **Option** and click the **Bluetooth** menu bar icon, or go to Apple Menu → **About This Mac** → **System Report** → **Bluetooth**. Look under paired devices for the address.
3. **Set it in meshcore.keys**:
   ```
   BLE_ADDR = A1B2C3D4-E5F6-7890-ABCD-EF1234567890
   ```

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

Open **Settings** → **Bluetooth & devices**, pair the device, then open **Device Manager** → expand **Bluetooth** → right-click your device → **Properties** → **Details** tab → select **Bluetooth device address**.

### Auto-Discovery (No Address Needed)

If you leave `BLE_ADDR` blank in your config, the MeshCore library will scan and connect to the first MeshCore device found. This takes 10–15 seconds longer on the first connection.

### Testing Your Connection

```bash
python meshcore_send.py --list-channels
```

If connection fails, verify the device is powered on, in range (within 10 meters), and not already connected to another app.

---

## Usage Guide

### Core Message Utility

```bash
# Send a message
python meshcore_send.py --channel myhams "Test message"

# List all channels on the connected device
python meshcore_send.py --list-channels
```

### Weather Broadcasting

```bash
python weather_broadcast.py                              # All locations
python weather_broadcast.py --channel wxchannel          # Different channel
python weather_broadcast.py --dry-run                    # Preview only
python weather_broadcast.py --stations KCASTATION1       # Specific PWS
python weather_broadcast.py --cities "Santa Cruz"        # Specific city
python weather_broadcast.py --pws-only                   # PWS stations only
python weather_broadcast.py --cities-only                # Cities only
python weather_broadcast.py --delay 60                   # 60s between msgs
python weather_broadcast.py --keys /path/to/weather.keys # Custom keys path
```

### Earthquake Monitoring

```bash
python earthquake_broadcast.py                     # Last 24hr, mag 2.5+
python earthquake_broadcast.py --hours 72          # Last 3 days
python earthquake_broadcast.py --minmag 3.0        # Magnitude 3.0+ only
python earthquake_broadcast.py --limit 5           # Max 5 earthquakes
python earthquake_broadcast.py --channel myhams    # Different channel
python earthquake_broadcast.py --dry-run           # Preview only
```

### Solar Propagation

```bash
python solar_broadcast.py                   # Solar indices + HF bands (2 msgs)
python solar_broadcast.py --vhf             # Add VHF + tropo (4 msgs total)
python solar_broadcast.py --hfband          # HF bands only (1 msg)
python solar_broadcast.py --channel myhams  # Different channel
python solar_broadcast.py --dry-run         # Preview only
```

### Calendar Notifications

#### Spreadsheet Setup

Create a Google Sheet with these columns:

| Column | Format | Example |
|--------|--------|---------|
| `EventDatetime` | `YYYY-MM-DD HHMM` | `2026-02-15 1900` |
| `EventName` | Text | `WVARA Net` |
| `Description` | Text | `Weekly 2m net on 146.76 MHz` |
| `Channels` | Comma-separated | `myhams,localnet` |

Publish the sheet as CSV: **File** → **Share** → **Publish to web** → select CSV format → copy the URL into `calendar.keys`.

#### Commands

```bash
python calendar_broadcast.py                    # Check and send due notifications
python calendar_broadcast.py --dry-run          # Preview without sending
python calendar_broadcast.py --preview          # Show upcoming events (7 days)
python calendar_broadcast.py --preview-days 30  # Show upcoming events (30 days)
python calendar_broadcast.py --reset-state      # Clear notification history
```

Notifications are sent at two windows: **24 hours** and **2 hours** before each event. The script tracks what has been sent in `~/.meshcore_calendar_state.json` to prevent duplicates.

### SOTA/POTA Activator Spotter

Broadcasts current Summits on the Air (SOTA) and Parks on the Air (POTA) activator spots within range of San Jose. No API keys required — both APIs are free and public.

**Band filtering** (`--band`): Restrict spots by frequency band before distance filtering.

| Band | Frequency Range | Default Radius |
|------|----------------|----------------|
| HF | 1–30 MHz | 0–1000 miles |
| VHF/UHF | ≥ 30 MHz | 0–100 miles |

**Distance filtering** uses min/max radius pairs for each band, allowing ring-shaped (donut) queries — for example, "only HF activations 300–1000 miles away" to exclude nearby stations you could easily work on VHF.

**Deduplication**: When the same callsign appears in both SOTA and POTA feeds (or is spotted multiple times), only the most recent spot is kept.

#### Commands

```bash
# Basic usage
python sotapota_broadcast.py                           # All bands, SOTA + POTA
python sotapota_broadcast.py --sota-only               # SOTA spots only
python sotapota_broadcast.py --pota-only               # POTA spots only
python sotapota_broadcast.py --channel myhams          # Different channel
python sotapota_broadcast.py --dry-run                 # Preview without transmitting

# Band filtering
python sotapota_broadcast.py --band hf                 # HF spots only (1-30 MHz)
python sotapota_broadcast.py --band vhf                # VHF/UHF spots only (≥ 30 MHz)

# Distance ring filtering (min and max radius)
python sotapota_broadcast.py --band vhf --vhf-min-radius 5 --vhf-max-radius 50
python sotapota_broadcast.py --band hf --hf-min-radius 300 --hf-max-radius 1000

# Other options
python sotapota_broadcast.py --limit 10                # Max spots to broadcast
python sotapota_broadcast.py --hours 4                 # SOTA lookback window (hours)
python sotapota_broadcast.py --delay 8                 # 8s between messages
```

#### How It Works

1. Fetches all current POTA activator spots (the POTA API returns lat/lon directly)
2. Fetches recent SOTA spots, then looks up summit coordinates for each via the SOTA summit API
3. Deduplicates by callsign, keeping only the most recent spot per operator
4. Applies band filter (`--band hf`, `--band vhf`, or both)
5. Applies distance ring filter (min/max radius per band)
6. Sorts by distance (closest first) and broadcasts up to `--limit`

SOTA spots are pre-filtered to North American associations (W0–W9, VE, XE) before coordinate lookups to minimize API calls. QRT spots (where the operator has signed off) are automatically excluded.

---

## Automated Scheduling (Cron)

All scripts are designed for unattended operation. Use `cron` (Linux/macOS) or Task Scheduler (Windows) to run them on a schedule.

### Setting Up Cron

```bash
# Edit your crontab
crontab -e
```

### Recommended Crontab

Below is a production crontab. Adjust paths to match your installation:

```bash
# ── Create log directory first: mkdir -p ~/logs ──────────────────────────────

# CALENDAR: Check every 30 minutes (catches 24h and 2h notification windows)
0,30 * * * * /path/to/venv/bin/python /path/to/meshbeacon/calendar_broadcast.py >> ~/logs/meshcore.log 2>&1

# SOLAR (VHF + tropo): Once daily at 7:20 AM
20 7 * * * /path/to/venv/bin/python /path/to/meshbeacon/solar_broadcast.py --vhf --channel myhams >> ~/logs/meshcore.log 2>&1

# SOLAR (HF bands only): Every 3 hours at :20
20 */3 * * * /path/to/venv/bin/python /path/to/meshbeacon/solar_broadcast.py --hfband --channel myhams >> ~/logs/meshcore.log 2>&1

# WEATHER: Twice daily (early morning and afternoon)
7 5 * * * /path/to/venv/bin/python /path/to/meshbeacon/weather_broadcast.py --channel myhams >> ~/logs/meshcore.log 2>&1
7 14 * * * /path/to/venv/bin/python /path/to/meshbeacon/weather_broadcast.py --channel myhams >> ~/logs/meshcore.log 2>&1

# EARTHQUAKE: Every 30 minutes, last 2 hours, magnitude 2.5+
*/30 * * * * /path/to/venv/bin/python /path/to/meshbeacon/earthquake_broadcast.py --hours 2 --channel myhams >> ~/logs/meshcore.log 2>&1

# SOTA/POTA: Every 15 minutes during daytime (peak activation hours)
*/15 8-22 * * * /path/to/venv/bin/python /path/to/meshbeacon/sotapota_broadcast.py --channel myhams --limit 5 >> ~/logs/meshcore.log 2>&1
```

### Schedule Guidelines

- **Calendar**: Every 15–30 minutes to catch notification windows. State tracking prevents duplicates.
- **Weather**: 2–4 times daily is sufficient. More frequent updates waste airtime.
- **Solar**: HF conditions change slowly — every 3 hours is ideal. The full VHF/tropo report (`--vhf`) is best once daily.
- **Earthquakes**: Every 30 minutes with `--hours 2` avoids re-broadcasting old events.
- **SOTA/POTA**: Every 15 minutes during daytime (8 AM–10 PM) catches activations during peak hours. Use `--limit 5` to avoid flooding the channel. Most SOTA activations last 20–60 minutes, so 15-minute polling is responsive without being excessive.

### Log Rotation

Keep logs manageable with `logrotate`:

```bash
# Create /etc/logrotate.d/meshcore
/home/youruser/logs/meshcore.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

---

## Scheduling on macOS and Windows

### macOS — Using `cron` or `launchd`

macOS fully supports `cron` (edit with `crontab -e`). However, Apple's recommended approach is `launchd`. If you use `cron`, note that macOS may prompt for Bluetooth permissions the first time a script runs — approve it in System Settings → Privacy & Security → Bluetooth.

**Using cron on macOS** works identically to Linux. Just use the full path to your Python interpreter:

```bash
0,30 * * * * /Users/youruser/myvenv/bin/python /Users/youruser/meshbeacon/calendar_broadcast.py >> /Users/youruser/logs/meshcore.log 2>&1
```

**Important**: macOS `cron` jobs run with a minimal environment. Always use full absolute paths for both the Python interpreter and the script. If your virtual environment needs activation, point directly to the venv's `python` binary as shown above.

### Windows — Using Task Scheduler

1. Open **Task Scheduler** (search in Start menu)
2. Click **Create Basic Task**
3. Set the trigger (e.g., daily, every 30 minutes)
4. For the action, choose **Start a program**:
   - Program: `C:\path\to\python.exe`
   - Arguments: `C:\path\to\meshbeacon\weather_broadcast.py --channel myhams`
   - Start in: `C:\path\to\meshbeacon`
5. Under **Conditions**, uncheck "Start the task only if the computer is on AC power" for laptops
6. Repeat for each script/schedule combination

**PowerShell alternative** — create a scheduled task from the command line:

```powershell
$action = New-ScheduledTaskAction -Execute "C:\Python312\python.exe" `
    -Argument "C:\meshbeacon\weather_broadcast.py --channel myhams" `
    -WorkingDirectory "C:\meshbeacon"
$trigger = New-ScheduledTaskTrigger -Daily -At "7:00AM"
Register-ScheduledTask -TaskName "MeshCore Weather" -Action $action -Trigger $trigger
```

---

## Hardware & Platform Notes

### Supported Platforms

- ✅ **macOS** (Apple Silicon and Intel) — Recommended for personal setups
- ✅ **Linux** (Ubuntu, Raspberry Pi OS) — Recommended for always-on servers
- ✅ **Windows** (10/11) — Works via Task Scheduler
- ✅ **Raspberry Pi** (Pi 4, Pi Zero W, Pi Zero 2 W) — See notes below

### Supported Devices

Any device compatible with MeshCore firmware, including Lilygo T-Deck, Heltec V3, and RAK4631 WisBlock.

### Connection Methods

**Bluetooth LE (BLE)** — No cables, works up to ~10 meters, slightly higher latency. Recommended for mobile setups.

**USB Serial** — Most reliable, lowest latency, no range limitation. Recommended for fixed installations and Raspberry Pi.

### Raspberry Pi Considerations

Running on Raspberry Pi (especially Pi Zero W) requires addressing three known issues that don't occur on macOS:

#### Issue 1: Hardware Flow Control

The Linux serial driver enables `crtscts` by default, causing hangs with MeshCore devices. Fix with a udev rule:

```bash
# Create /etc/udev/rules.d/99-meshcore.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", \
    SYMLINK+="meshcore", MODE="0666", \
    RUN+="/bin/stty -F /dev/%k -crtscts"

sudo udevadm control --reload-rules && sudo udevadm trigger
```

#### Issue 2: Device File Race at Boot

Scripts launched at boot start before `/dev/meshcore` exists. Use a wrapper:

```bash
#!/bin/bash
# wait-for-device.sh
DEVICE="/dev/meshcore"
MAX_WAIT=60
WAIT_TIME=0
while [ ! -e "$DEVICE" ] && [ $WAIT_TIME -lt $MAX_WAIT ]; do
    sleep 2
    WAIT_TIME=$((WAIT_TIME + 2))
done
if [ -e "$DEVICE" ]; then
    exec "$@"
else
    echo "Device not found after ${MAX_WAIT}s" >&2
    exit 1
fi
```

#### Issue 3: Command Timeouts

The MeshCore library's default 3-second timeout is too aggressive for slower hardware. Either increase `DEFAULT_TIMEOUT` to 10 in the library source, or implement retry logic:

```python
async def send_with_retry(mc, idx, msg, retries=3):
    for attempt in range(retries):
        try:
            result = await mc.commands.send_chan_msg(idx, msg)
            if result.type != EventType.ERROR:
                return True
        except asyncio.TimeoutError:
            if attempt < retries - 1:
                await asyncio.sleep(2)
    return False
```

**Performance expectations**: Pi Zero W takes 5–8 seconds per message (vs 2–3 on macOS). Pi 4 is 3–4 seconds.

---

## Troubleshooting

### Connection Issues

**"Could not connect to device"** — Verify the device is powered on and in range. For BLE, check it's not already connected to another app. For serial, verify the port exists:

```bash
ls -l /dev/tty* | grep -i usb    # macOS/Linux
ls -l /dev/meshcore               # Linux with udev rule
```

**"Channel not found"** — Run `python meshcore_send.py --list-channels` and verify the channel name and secret in `meshcore.keys` match what's on the device.

**Permission denied on serial port** — Add your user to the `dialout` group:

```bash
sudo usermod -a -G dialout $USER   # Log out and back in
```

### Message Issues

**"Message too long"** — All messages are limited to 135 bytes (UTF-8). Scripts auto-truncate, but use `--dry-run` to preview.

**Messages not appearing on other devices** — Confirm channel secrets match across all devices and that other devices are subscribed to the channel.

### Data Fetch Issues

**"Failed to fetch data"** — Check internet connectivity. Most APIs are free with generous rate limits. Use `--dry-run` to test fetching without transmitting.

### Debugging

```bash
# Check environment
env | grep MESHCORE

# Test connection
python meshcore_send.py --list-channels

# Preview any broadcast without transmitting
python weather_broadcast.py --dry-run
python solar_broadcast.py --dry-run
python earthquake_broadcast.py --dry-run
python calendar_broadcast.py --dry-run
```

---

## Data Sources

| Source | URL | Key Required? | Update Frequency |
|--------|-----|---------------|------------------|
| Weather Underground | api.weather.com | Yes (free) | Real-time |
| USGS Earthquake Hazards | earthquake.usgs.gov | No | Real-time |
| HamQSL / N0NBH | hamqsl.com/solarxml.php | No | Every 3 hours |
| Open-Meteo | api.open-meteo.com | No | Hourly |
| Google Sheets (calendar) | docs.google.com | No | On edit |
| POTA API | api.pota.app | No | Real-time |
| SOTA API | api2.sota.org.uk | No | Real-time |

---

## Message Format

All messages are constrained to **135 bytes** (UTF-8), the MeshCore/LoRa hard limit. Messages use multi-line formatting with `\n` and emoji indicators for at-a-glance status.

### Sample Messages

**Weather**
```
WX My Backyard
Temp 58F Feels 56F
Hi 63F Lo 44F
Hum 65% Rain 20%
Wind 5mph NW G10
Partly Cloudy
```

**Earthquake**
```
EARTHQUAKE
🟡 M3.3 - 5 km SW of Ridgemark
46.5mi from SJC | Depth: 5.0mi
Feb 11 03:34 PST
```

**Solar Indices**
```
☀️ SOLAR:
SFI=185
SN=85
A=5
K=2
Xray=A1.2
Wind=425km/s
Bt=-3nT
[10 Feb 1800z] ✅
```

**HF Bands**
```
📡 BANDS D/N:
80 = ✅
40 = ✅
30 = ✅
20 = 🟡
17 = 🟡
15 = ❌
12 = ❌
10 = ❌
```

**VHF Conditions**
```
🔭 VHF:
Es=Band Closed
Aurora=No Aurora
Meteor=Perseids+14d ❌
```

**Tropospheric Ducting**
```
🌊 TROPO SJC:
Idx=6/10
dT=+12F@925mb
Pres=1022mb
✅ Likely
```

**NOAA Alert** (fires automatically when conditions warrant)
```
💥🔆💫 ALERT: Geomag=G2(K=6+) Flare=M5.1(R2) Wind=650km/s
```

**Calendar Notification**
```
EVENT TOMORROW:
WVARA Net
Sat Feb 14 @ 7:00 PM
Weekly 2m net on 146.76 MHz
```

**SOTA Activator Spot**
```
SOTA
W6/SC-001
Call: KG6NBO
SSB 14.244
14:30 PST
978mi NW of SJC
```

**POTA Activator Spot**
```
POTA
US-4701
Call: KC1GGP
SSB 14.307
09:31 PST
45mi SE of SJC
```

---

## Contributing

This project was built for amateur radio emergency communications. To adapt it for your area:

1. **Fork the repository**
2. **Update meshcore.keys** with your channels and device info
3. **Update weather.keys** with your local PWS stations and cities
4. **Adjust location constants** (`SJC_LAT`/`SJC_LON`) in earthquake, solar, and SOTA/POTA scripts for your grid square
5. **Set up your cron schedules** based on your network's needs

Bug reports and feature suggestions are welcome via GitHub issues.

---

## Author
Sal Mancuso W6SAL

---

## License

Released as open source for the amateur radio community. Use, modify, and distribute freely. No warranty provided.

---

## Version History

- **v1.2** (Feb 2026) — Added SOTA/POTA activator spot broadcasting with distance-based filtering
- **v1.1** (Feb 2026) — Externalized all credentials to `.keys` config files for safe GitHub deployment
- **v1.0** (Feb 2026) — Initial release: weather, earthquake, solar, and calendar broadcasts with BLE/serial support