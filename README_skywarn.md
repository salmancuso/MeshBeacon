# Skywarn Broadcast — `skywarn_broadcast.py`

**NWS severe weather alert broadcasting for MeshCore mesh networks**

Fetches active watches, warnings, and advisories from the National Weather Service (NWS) API and broadcasts formatted messages to MeshCore channels. Supports filtering by zone codes, zip code + radius, severity level, and alert type.

**Latest Updates (May 2026)**
- Zone code filtering (NWS county/fire weather zones) — recommended for Bay Area
- Message chunking for MeshCore 184-byte limit
- Optional summary message for hourly cron jobs
- Classic Skywarn event filtering

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

### Zone-Based (Recommended for Bay Area / sfoskywarn)

```bash
# All 11 SF Bay Area counties (MTR CWA)
python skywarn_broadcast.py --zone CAC001,CAC013,CAC041,CAC053,CAC055,CAC069,CAC075,CAC081,CAC085,CAC087,CAC097

# Just Santa Clara County
python skywarn_broadcast.py --zone CAC085

# Preview without transmitting
python skywarn_broadcast.py --zone CAC085 --dry-run

# With summary message (great for cron)
python skywarn_broadcast.py --zone CAC085 --summary
```

### Traditional Radius-Based

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
--zone CAC085,CAZ513                     # NWS zone codes (comma-separated)
--zip 95125                              # US zip code (auto-geocoded)
--lat 37.34 --lon -121.89                # Direct coordinates
--state CA                               # All alerts for state (no radius)
```

If no location is specified, defaults to San Jose, CA (CM97bg).

### Filtering

```bash
--radius 50                          # Miles from center (default: 50)
--severity severe                    # Minimum severity (extreme/severe/moderate/minor)
--type tornado,flood                 # Comma-separated event type keywords
--skywarn-only                       # Only classic Skywarn events
```

The `--skywarn-only` flag limits to: Tornado Warning/Watch/Emergency, Severe Thunderstorm Warning/Watch, Flash Flood Warning/Watch, Flood Warning/Watch/Advisory, Special/Severe Weather Statements.

### Broadcast Options

```bash
--channel meshhams                   # Target channel (default: meshhams)
--limit 10                           # Max alerts to broadcast (default: 10)
--delay 5                            # Seconds between messages (default: 5)
--send-clear                         # Send all-clear when no alerts active
--summary                            # Include summary message at start (hourly cron jobs)
--dry-run                            # Preview without transmitting
```

---

## NWS Zone Codes

### Bay Area MTR County Warning Area (11-County CWA)

Reference: https://sfoskywarn.org

**County Codes (CAC prefix = County Area Code)**
```
CAC001  Alameda
CAC013  Contra Costa
CAC041  Marin
CAC053  Monterey
CAC055  Napa
CAC069  San Benito
CAC075  San Francisco
CAC081  San Mateo
CAC085  Santa Clara
CAC087  Santa Cruz
CAC097  Sonoma
```

**Public/Fire Weather Zones (CAZ prefix)**
```
CAZ505  Coastal North Bay Including Point Reyes National Seashore
CAZ506  North Bay Interior Mountains
CAZ508  San Francisco Bay Shoreline
CAZ509  San Francisco Peninsula Coast
CAZ510  East Bay Hills and Diablo Range
CAZ512  Santa Cruz Mountains
CAZ513  Santa Clara Valley
CAZ514  Eastern Santa Clara Hills
CAZ515  Monterey Bay Shoreline
CAZ516  Northern Salinas Valley/Hollister Valley
CAZ517  Southern Salinas Valley/Arroyo Seco/Lake San Antonio
CAZ518  Mountains of San Benito County and Interior Monterey County
CAZ528  Northern Monterey Bay
CAZ529  Southern Monterey Bay and Big Sur Coast
```

**Query Examples**
```bash
# Classic warnings for Santa Clara Valley
python skywarn_broadcast.py --zone CAZ513 --skywarn-only

# Flood alerts for multiple zones
python skywarn_broadcast.py --zone CAZ513,CAZ514 --type flood

# All warnings for entire Bay Area
python skywarn_broadcast.py --zone CAC001,CAC013,CAC041,CAC053,CAC055,CAC069,CAC075,CAC081,CAC085,CAC087,CAC097 --severity severe
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
🟠 Severe Thunderstorm Warning
Santa Clara County, CA
```

### Multi-Part Alert (MeshCore Chunking)
For long headlines exceeding ~160 bytes:
```
⚠️ SKYWARN
🟠 Long alert headline text... (1 of 2)

⚠️ SKYWARN
🟠 ...continuation of headline (2 of 2)
```

### Summary Message (with `--summary`)
```
⚠️ SKYWARN SUMMARY
As of 2:30 PM PST: 3 active alerts for Bay Area.
Details follow.
```

### All Clear (with `--send-clear`)
```
⚠️ SKYWARN
✅ No active alerts
San Jose, CA
Radius: 50mi
```

---

## Automated Scheduling

### Hourly monitoring (recommended for Bay Area / sfoskywarn)

```bash
# Every hour, with summary message at start
0 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zone CAC001,CAC013,CAC041,CAC053,CAC055,CAC069,CAC075,CAC081,CAC085,CAC087,CAC097 --channel sfoskywarn --summary --skywarn-only >> ~/meshcore/logs/skywarn.log 2>&1
```

### Every 30 minutes (routine monitoring)

```bash
# 30-minute intervals, no duplicates sent
*/30 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zone CAC085 --channel meshhams >> ~/meshcore/logs/skywarn.log 2>&1
```

### During severe weather season (every 15 min)

```bash
# More frequent updates when conditions warrant
*/15 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zip 95125 --radius 50 --severity severe --channel meshhams >> ~/meshcore/logs/skywarn.log 2>&1
```

### With all-clear messages

```bash
# Hourly with all-clear when quiet
0 * * * * /usr/bin/python3 /home/sal/meshcore/skywarn_broadcast.py --zone CAC085 --send-clear --channel meshhams >> ~/meshcore/logs/skywarn.log 2>&1
```

---

## How Zone Filtering Works

The NWS has two ways of dividing areas by geography:

1. **County Zones (CAC codes)** — Political boundaries (e.g., Santa Clara County = CAC085)
2. **Public/Fire Weather Zones (CAZ codes)** — Geography-based, can span counties (e.g., Santa Clara Valley = CAZ513)

Most NWS alerts are issued on a county level. Severe weather (tornadoes) and fire weather alerts are often issued on a public weather zone basis.

The API query parameter `?zone=CODE1,CODE2,CODE3...` fetches all active alerts for those zones.

**Why zones instead of radius?**
- More geographically accurate than a simple radius
- Matches the MTR (NWS Monterey) CWA structure
- Reduces noise from alerts outside your area
- Aligns with how professional Skywarn coordinators work

---

## Examples

```bash
# Bay Area Skywarn with classic events only
python skywarn_broadcast.py --zone CAC001,CAC013,CAC041,CAC053,CAC055,CAC069,CAC075,CAC081,CAC085,CAC087,CAC097 --skywarn-only --channel sfoskywarn

# Santa Clara County, severe alerts only
python skywarn_broadcast.py --zone CAC085 --severity severe

# Santa Clara Valley (fire weather zone), all alerts
python skywarn_broadcast.py --zone CAZ513

# Traditional radius (Oklahoma tornado alley example)
python skywarn_broadcast.py --zip 73301 --radius 100 --skywarn-only

# Florida statewide, all alert types
python skywarn_broadcast.py --state FL --severity severe

# Flood monitoring in Bay Area
python skywarn_broadcast.py --zone CAC085,CAC087 --type flood

# Preview what would be sent (dry run)
python skywarn_broadcast.py --zone CAC085 --dry-run
```

---

## Message Chunking (MeshCore 184-Byte Limit)

The script automatically breaks long alert headlines into numbered chunks:

- **Single alert**: Sent as one message
- **Long headline** (e.g., 200+ bytes): Split into "(1 of 2)" and "(2 of 2)" parts
- **Preserves readability**: Breaks on word boundaries

Example:
```
⚠️ SKYWARN
🟠 This is a very long alert headline that exceeds the MeshCore... (1 of 2)

⚠️ SKYWARN
🟠 ...safe character limit and needs to be chunked (2 of 2)
```

---

## Dependencies

```
requests>=2.31.0
meshcore>=0.1.0
```

No additional API keys required. The NWS API and Zippopotam.us are both free public services.

---

## Contact

- **Sal W6SAL**: w6sal@yahoo.com