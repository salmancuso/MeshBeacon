# Skywarn Broadcast — `skywarn_broadcast.py`

**NWS severe weather alert broadcasting for MeshCore mesh networks**

Fetches active watches, warnings, and advisories from the National Weather Service (NWS) API and broadcasts formatted messages to MeshCore channels. Supports filtering by zip code + radius, severity level, and alert type.

---

## Data Source

**NWS API** (`api.weather.gov`)
- Free, no API key required
- Update frequency: Near real-time (typically < 5 minutes after issuance)
- Coverage: All US states and territories
- Data: Watches, warnings, advisories, special statements

**Zippopotam.us** — Free zip-to-coordinate geocoding (no API key)

---

## Quick Start

```bash
# Alerts within 50 miles of zip code 95125 (San Jose)
python skywarn_broadcast.py --zip 95125 --radius 50

# Preview without transmitting
python skywarn_broadcast.py --zip 95125 --radius 50 --dry-run

# Direct coordinates
python skywarn_broadcast.py --lat 37.3382 --lon -121.8863 --radius 75

# All alerts for a state
python skywarn_broadcast.py --state CA

# Different channel
python skywarn_broadcast.py --zip 95125 --channel sanjosesimplex
```

---

## Usage

### Location Options (pick one)

```bash
--zip 95125                          # US zip code (auto-geocoded)
--lat 37.34 --lon -121.89            # Direct coordinates
--state CA                           # All alerts for state (no radius)
```

If no location is specified, defaults to San Jose, CA (CM97bg).

### Filtering

```bash
--radius 50                          # Miles from center (default: 50)
--severity severe                    # Minimum severity (extreme/severe/moderate/minor)
--type tornado,flood                 # Comma-separated event type keywords
--skywarn-only                       # Only classic Skywarn events
```

The `--skywarn-only` flag limits to: Tornado Warning/Watch, Severe Thunderstorm Warning/Watch, Flash Flood Warning/Watch, Flood Warning/Watch/Advisory, Special/Severe Weather Statements.

### Broadcast Options

```bash
--channel meshhams                   # Target channel (default: meshhams)
--limit 10                           # Max alerts to broadcast (default: 10)
--delay 5                            # Seconds between messages (default: 5)
--send-clear                         # Send all-clear when no alerts active
--dry-run                            # Preview without transmitting
```

---

## Severity Levels

| Icon | Level | Description | Typical Events |
|------|-------|-------------|----------------|
| 🔴 | Extreme | Extraordinary threat | Tornado Emergency |
| 🟠 | Severe | Significant threat | Tornado Warning, Severe T-Storm Warning |
| 🟡 | Moderate | Possible threat | Tornado Watch, Severe T-Storm Watch |
| 🟢 | Minor | Minimal threat | Wind Advisory, Frost Advisory |
| ⚪ | Unknown | Not yet determined | Special Weather Statement |

---

## Message Format

### Active Alert
```
⚠️ SKYWARN
🟠 Svr T-Storm Wrn
Santa Clara County, CA
Until 3:45 PM PST
23mi
```

### All Clear (with `--send-clear`)
```
⚠️ SKYWARN
✅ No active alerts
San Jose, CA
Radius: 50mi
```

---

## How Radius Filtering Works

The script combines two NWS API queries for comprehensive coverage:

1. **Point query** (`?point=lat,lon`) — catches all alerts directly affecting your location
2. **State query** (`?area=STATE`) — catches broader regional alerts

Alerts are deduplicated by ID, then filtered by computing the distance from your center point to each alert's polygon centroid. Alerts from the point query with no geometry are always included (they directly affect your location).

---

## Automated Scheduling

```bash
# Every 15 minutes during severe weather season
*/15 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zip 95125 --radius 50 --channel meshhams >> ~/meshcore/logs/skywarn.log 2>&1

# Every 30 minutes (routine monitoring)
*/30 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zip 95125 --radius 50 --channel meshhams

# Severe-only monitoring (reduces noise)
*/15 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zip 95125 --radius 50 --severity severe --channel meshhams

# With all-clear messages
*/30 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zip 95125 --radius 50 --send-clear --channel meshhams
```

---

## Examples

```bash
# Bay Area severe weather monitoring
python skywarn_broadcast.py --zip 95125 --radius 75 --severity moderate

# Oklahoma tornado alley — Skywarn events only
python skywarn_broadcast.py --zip 73301 --radius 100 --skywarn-only

# Florida hurricane monitoring — all alert types
python skywarn_broadcast.py --state FL --severity severe

# Flood monitoring in a specific area
python skywarn_broadcast.py --zip 95125 --radius 50 --type flood,flash

# Preview what would be sent
python skywarn_broadcast.py --zip 95125 --radius 50 --dry-run
```

---

## Dependencies

```
requests>=2.31.0
meshcore>=0.1.0
```

No additional API keys required. The NWS API and Zippopotam.us are both free public services.
