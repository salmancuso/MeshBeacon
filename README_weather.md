# Weather Broadcast — `weather_broadcast.py`

**Bay Area weather reports for MeshCore mesh networks**

Fetches current conditions and forecasts from Weather Underground and broadcasts formatted messages to MeshCore channels. Supports both Personal Weather Station (PWS) live observations and city-level forecasts.

---

## Data Source

**Weather Underground API**
- URL: `api.weather.com`
- API key: Required (free tier available)
- Update frequency: Real-time for PWS, hourly for forecasts
- Coverage: Global (PWS network + NWS forecasts)

Get your free API key: https://www.wunderground.com/member/api-keys

---

## Configuration

All credentials and locations are managed in a `weather.keys` file (see `weather.keys.example`):

```ini
WU_API_KEY=your_api_key_here

# PWS stations: ID | Display Label
STATION=KCASANJO823 | SJC (Home)
STATION=KCASANTA45  | Santa Cruz

# Cities: Name | Latitude | Longitude
CITY=Gilroy | 36.9933 | -121.5683
CITY=Palo Alto | 37.4419 | -122.1430
```

**STATION** entries use PWS live observations + forecast (best accuracy).  
**CITY** entries use forecast only (current temp is estimated from daypart data).

Both produce the same message format.

---

## Usage

```bash
# All configured locations → default channel
python weather_broadcast.py

# Specific channel
python weather_broadcast.py --channel weather

# Filter to specific stations or cities
python weather_broadcast.py --stations KCASANJO823
python weather_broadcast.py --cities "Santa Cruz" "Palo Alto"

# Type filters
python weather_broadcast.py --pws-only
python weather_broadcast.py --cities-only

# Custom delay between messages
python weather_broadcast.py --delay 10

# Preview without transmitting
python weather_broadcast.py --dry-run

# Custom keys file location
python weather_broadcast.py --keys /path/to/weather.keys
```

---

## Message Format

Every location produces the same 6-line format (≤135 bytes):

```
WX SJC (Home)
Temp 58F Feels 56F
Hi 63F Lo 44F
Hum 65% Rain 20%
Wind 5mph NW G10
Partly Cloudy
```

---

## Automated Scheduling

```bash
# Every 6 hours
0 6,12,18,0 * * * /usr/bin/python3 /home/sal/meshcore/weather_broadcast.py --channel meshhams >> ~/meshcore/logs/weather.log 2>&1
```

4-6 times daily is sufficient for general awareness. More frequent updates waste airtime.

---

## Dependencies

```
requests>=2.31.0
meshcore>=0.1.0
```

Requires a Weather Underground API key configured in `weather.keys`.
