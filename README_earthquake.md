# Earthquake Broadcast — `earthquake_broadcast.py`

**USGS seismic monitoring and alerting for MeshCore mesh networks**

Monitors earthquakes within a configurable radius of San Jose using the USGS Earthquake Hazards Program API and broadcasts formatted alerts to MeshCore channels.

---

## Data Source

**USGS Earthquake Hazards Program**
- URL: `earthquake.usgs.gov/fdsnws/event/1/`
- Free, no API key required
- Update frequency: Real-time (typically < 5 minutes after event)
- Coverage: Global, with focus on US regions

---

## Usage

```bash
# Last 24 hours, magnitude 2.5+ (defaults)
python earthquake_broadcast.py

# Last 72 hours
python earthquake_broadcast.py --hours 72

# Only magnitude 3.0+
python earthquake_broadcast.py --minmag 3.0

# Limit to 5 most significant events
python earthquake_broadcast.py --limit 5

# Specific channel
python earthquake_broadcast.py --channel sanjosesimplex

# Preview without transmitting
python earthquake_broadcast.py --dry-run
```

If no earthquakes are found within the search criteria, the script exits cleanly without transmitting.

---

## Features

- **100-mile radius** monitoring from San Jose (covers entire Bay Area)
- **Magnitude filtering**: Configurable minimum (default: 2.5+)
- **Rich details**: Magnitude, location, depth, distance from SJC, timestamp
- **Severity icons**: 🟢 minor, 🟡 light, 🟠 moderate, 🔴 major

---

## Message Format

```
EARTHQUAKE
🟡 M3.3 - 5 km SW of Ridgemark
46.5mi from SJC | Depth: 5.0mi
Feb 11 03:34 PST
```

---

## Automated Scheduling

```bash
# Every 30 minutes, last 2 hours
*/30 * * * * /usr/bin/python3 /home/sal/meshcore/earthquake_broadcast.py --hours 2 --channel meshhams >> ~/meshcore/logs/earthquake.log 2>&1

# Serious monitoring: every 15 min, magnitude 3.0+
*/15 * * * * /usr/bin/python3 /home/sal/meshcore/earthquake_broadcast.py --hours 1 --minmag 3.0 --channel meshhams >> ~/meshcore/logs/earthquake.log 2>&1
```

---

## Dependencies

```
requests>=2.31.0
meshcore>=0.1.0
```

No API keys required.
