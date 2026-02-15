# SOTA/POTA Broadcast — `sotapota_broadcast.py`

**Nearby SOTA and POTA activator spot broadcasting for MeshCore mesh networks**

Fetches current activator spots from the SOTA and POTA APIs, filters by distance and frequency band from San Jose, and broadcasts formatted messages to the configured channel.

---

## Data Sources

**POTA API** (`api.pota.app`)
- Free, no API key required
- Real-time activator spots with coordinates

**SOTA API** (`api2.sota.org.uk`)
- Free, no API key required
- Spot data with summit coordinate lookups

---

## Usage

```bash
# VHF/UHF spots between 5 and 50 miles
python sotapota_broadcast.py --band vhf --vhf-min-radius 5 --vhf-max-radius 50

# HF spots between 300 and 1000 miles
python sotapota_broadcast.py --band hf --hf-min-radius 300 --hf-max-radius 1000

# All bands, default radii
python sotapota_broadcast.py

# Source filtering
python sotapota_broadcast.py --sota-only
python sotapota_broadcast.py --pota-only

# Custom channel, limit, delay
python sotapota_broadcast.py --channel sanjosesimplex --limit 5 --delay 8

# Preview without transmitting
python sotapota_broadcast.py --dry-run
```

---

## Frequency Bands

| Band | Range | Default Max Radius |
|------|-------|--------------------|
| HF | 1–30 MHz | 1000 miles |
| VHF/UHF | ≥ 30 MHz | 100 miles |

Unknown frequencies default to HF (wider radius).

---

## Message Format

```
POTA
US-0541
Call: W6ABC
SSB 14.282
14:30 PST
45mi SE of SJC
```

---

## Key Options

| Option | Default | Description |
|--------|---------|-------------|
| `--band` | all | Filter: `all`, `hf`, or `vhf` |
| `--hf-max-radius` | 1000 mi | Max distance for HF spots |
| `--hf-min-radius` | 0 mi | Min distance for HF spots |
| `--vhf-max-radius` | 100 mi | Max distance for VHF/UHF spots |
| `--vhf-min-radius` | 0 mi | Min distance for VHF/UHF spots |
| `--limit` | 10 | Max spots to broadcast |
| `--hours` | 2 | SOTA lookback hours |
| `--delay` | 5s | Seconds between messages |

---

## Automated Scheduling

```bash
# VHF spots every hour
0 * * * * /usr/bin/python3 /home/sal/meshcore/sotapota_broadcast.py --band vhf --channel meshhams >> ~/meshcore/logs/sotapota.log 2>&1

# HF spots every 2 hours
0 */2 * * * /usr/bin/python3 /home/sal/meshcore/sotapota_broadcast.py --band hf --channel meshhams >> ~/meshcore/logs/sotapota.log 2>&1
```

---

## Dependencies

```
requests>=2.31.0
meshcore>=0.1.0
```

No API keys required.
