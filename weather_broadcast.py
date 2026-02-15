#!/usr/bin/env python3
"""
weather_broadcast.py - Fetch weather from Weather Underground and broadcast
                       to MeshCore channels.

Every location produces the same message format:

    WX <Label>
    Temp 58F Feels 56F
    Hi 63F Lo 44F
    Hum 65% Rain 20%
    Wind 5mph NW G10
    Partly Cloudy

Two source types (both via Weather Underground API):
  • STATION — PWS current observations + 5-day forecast by geocode
  • CITY    — 5-day forecast by geocode only (current temp is estimated)

All credentials and config live in 'weather.keys' (see weather.keys.example).

Usage:
    python weather_broadcast.py                              # All locations
    python weather_broadcast.py --channel weather            # Different channel
    python weather_broadcast.py --dry-run                    # Preview only
    python weather_broadcast.py --stations KCASANJO823       # Filter PWS
    python weather_broadcast.py --cities "Santa Cruz"        # Filter cities
    python weather_broadcast.py --pws-only / --cities-only   # Type filter
    python weather_broadcast.py --delay 60                   # 60s between msgs
    python weather_broadcast.py --keys /path/to/weather.keys # Custom keys path

Requires:
    pip install requests meshcore
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# Reuse all connection logic from meshcore_send.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from meshcore_send import (
    CHANNELS, MAX_MSG_LEN, CONNECT_DELAY,
    connect, resolve_channel_index,
)
from meshcore import EventType


# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_KEYS_FILE = Path(__file__).parent / "weather.keys"
WU_PWS_URL        = "https://api.weather.com/v2/pws/observations/current"
WU_FORECAST_URL   = "https://api.weather.com/v3/wx/forecast/daily/5day"
DEFAULT_CHANNEL   = "meshhams"
DEFAULT_DELAY     = 5.0


def load_config(keys_path: Path) -> dict:
    config = {"wu_api_key": "", "stations": [], "cities": []}
    if not keys_path.exists():
        print(f"WARNING: Config file not found: {keys_path}", file=sys.stderr)
        print(f"  Copy weather.keys.example → weather.keys", file=sys.stderr)
        return config

    with open(keys_path, "r") as f:
        for ln, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip().upper(), val.strip()

            if key == "WU_API_KEY":
                config["wu_api_key"] = val
            elif key == "STATION":
                parts = [p.strip() for p in val.split("|")]
                if len(parts) >= 2:
                    config["stations"].append({"id": parts[0], "label": parts[1]})
                elif parts and parts[0]:
                    config["stations"].append({"id": parts[0], "label": parts[0]})
            elif key == "CITY":
                parts = [p.strip() for p in val.split("|")]
                if len(parts) >= 3:
                    try:
                        config["cities"].append({
                            "name": parts[0],
                            "lat":  float(parts[1]),
                            "lon":  float(parts[2]),
                        })
                    except ValueError:
                        print(f"  Bad CITY coords line {ln}", file=sys.stderr)
    return config


# ── Helpers ───────────────────────────────────────────────────────────────────

def degrees_to_compass(deg) -> str:
    if deg is None:
        return "---"
    try:
        deg = float(deg)
    except (TypeError, ValueError):
        return "---"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(deg / 22.5) % 16]


def _v(val, suffix="", default="--"):
    if val is None:
        return default
    if isinstance(val, float) and val == int(val):
        return f"{int(val)}{suffix}"
    return f"{val}{suffix}"


# ── Unified Message Format ────────────────────────────────────────────────────

def format_message(label: str, wx: dict) -> str:
    """
    Build the consistent 6-line weather message (≤135 chars).

    Required wx keys:
        temp, feels_like, hi, lo, humidity, precip_chance,
        wind_spd, wind_dir, wind_gust, condition
    """
    gust = ""
    if wx.get("wind_gust") and wx["wind_gust"] > 0:
        gust = f" G{_v(wx['wind_gust'])}"

    lines = [
        f"WX {label}",
        f"Temp {_v(wx['temp'],'F')} Feels {_v(wx['feels_like'],'F')}",
        f"Hi {_v(wx['hi'],'F')} Lo {_v(wx['lo'],'F')}",
        f"Hum {_v(wx['humidity'],'%')} Rain {_v(wx['precip_chance'],'%')}",
        f"Wind {_v(wx['wind_spd'],'mph')} {wx['wind_dir']}{gust}",
        f"{wx.get('condition', '')}",
    ]

    return "\n".join(lines)[:MAX_MSG_LEN]


# ── WU API: PWS observations ─────────────────────────────────────────────────

def fetch_pws_obs(station_id: str, api_key: str) -> dict | None:
    """Fetch current observations.  Returns obs dict with lat/lon or None."""
    params = {
        "stationId": station_id, "format": "json",
        "units": "e", "numericPrecision": "decimal", "apiKey": api_key,
    }
    try:
        r = requests.get(WU_PWS_URL, params=params, timeout=10)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        if not obs:
            print("no data (offline?)", file=sys.stderr, end="")
            return None
        return obs[0]
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "?"
        print(f"HTTP {code}", file=sys.stderr, end="")
        return None
    except Exception as e:
        print(f"error: {e}", file=sys.stderr, end="")
        return None


# ── WU API: 5-day daily forecast ──────────────────────────────────────────────

def fetch_forecast(lat: float, lon: float, api_key: str) -> dict | None:
    """Fetch today's forecast.  Returns parsed dict or None."""
    params = {
        "geocode": f"{lat},{lon}", "format": "json",
        "units": "e", "language": "en-US", "apiKey": api_key,
    }
    try:
        r = requests.get(WU_FORECAST_URL, params=params, timeout=10)
        r.raise_for_status()
        j = r.json()

        hi = j.get("temperatureMax", [None])[0]
        lo = j.get("temperatureMin", [None])[0]
        if hi is None:
            hi = j.get("calendarDayTemperatureMax", [None])[0]

        dp = j.get("daypart", [{}])
        if isinstance(dp, list) and dp:
            dp = dp[0]

        # Pick today-day (0) or tonight (1) if day expired
        idx = 0
        names = dp.get("daypartName", [])
        if isinstance(names, list) and len(names) > 0 and names[0] is None:
            idx = 1

        def _dp(field):
            arr = dp.get(field, [])
            if isinstance(arr, list) and len(arr) > idx:
                return arr[idx]
            return None

        condition = _dp("wxPhraseLong") or ""
        if len(condition) > 25:
            condition = condition[:22] + "..."

        return {
            "hi":            hi,
            "lo":            lo,
            "humidity":      _dp("relativeHumidity"),
            "wind_spd":      _dp("windSpeed"),
            "wind_dir_deg":  _dp("windDirection"),
            "wind_gust":     None,  # forecast doesn't include gust
            "precip_chance":  _dp("precipChance"),
            "condition":     condition,
            "dp_temp":       _dp("temperature"),
            "dp_heat_idx":   _dp("temperatureHeatIndex"),
            "dp_wind_chill": _dp("temperatureWindChill"),
        }
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "?"
        print(f"forecast HTTP {code}", file=sys.stderr, end="")
        return None
    except Exception as e:
        print(f"forecast error: {e}", file=sys.stderr, end="")
        return None


# ── Build unified wx dict per location type ───────────────────────────────────

def build_station_wx(station: dict, api_key: str) -> dict | None:
    """
    STATION: fetch PWS obs (current temp, feels, hum, wind, gust)
             + forecast by geocode (hi, lo, rain%, condition).
    """
    obs = fetch_pws_obs(station["id"], api_key)
    if obs is None:
        return None

    imp = obs.get("imperial", {})
    lat = obs.get("lat")
    lon = obs.get("lon")

    # Build base from observations
    wx = {
        "temp":       imp.get("temp"),
        "feels_like": imp.get("heatIndex") or imp.get("windChill"),
        "humidity":   obs.get("humidity"),
        "wind_spd":   imp.get("windSpeed"),
        "wind_dir":   degrees_to_compass(obs.get("winddir")),
        "wind_gust":  imp.get("windGust"),
    }

    # Supplement with forecast data
    if lat is not None and lon is not None:
        fc = fetch_forecast(lat, lon, api_key)
        if fc:
            wx["hi"]            = fc["hi"]
            wx["lo"]            = fc["lo"]
            wx["precip_chance"] = fc["precip_chance"]
            wx["condition"]     = fc["condition"]
        else:
            wx.update({"hi": None, "lo": None, "precip_chance": None, "condition": ""})
    else:
        wx.update({"hi": None, "lo": None, "precip_chance": None, "condition": ""})

    return wx


def build_city_wx(city: dict, api_key: str) -> dict | None:
    """
    CITY: fetch forecast (hi, lo, rain%, condition, daypart temp as proxy
          for current temp since no PWS).
    """
    fc = fetch_forecast(city["lat"], city["lon"], api_key)
    if fc is None:
        return None

    # Use daypart temperature as current-temp proxy
    feels = fc.get("dp_heat_idx") or fc.get("dp_wind_chill") or fc.get("dp_temp")

    return {
        "temp":          fc.get("dp_temp"),
        "feels_like":    feels,
        "hi":            fc["hi"],
        "lo":            fc["lo"],
        "humidity":      fc["humidity"],
        "wind_spd":      fc["wind_spd"],
        "wind_dir":      degrees_to_compass(fc.get("wind_dir_deg")),
        "wind_gust":     fc["wind_gust"],
        "precip_chance": fc["precip_chance"],
        "condition":     fc["condition"],
    }


# ── Broadcast ─────────────────────────────────────────────────────────────────

async def broadcast(config: dict, channel_key: str, station_filter: list,
                    city_filter: list, pws_only: bool, cities_only: bool,
                    dry_run: bool, delay: float):

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*56}")
    print(f"  MeshCore Weather Broadcast  (Weather Underground)")
    print(f"  {timestamp}")
    print(f"  Channel : {channel_key.upper()}")
    print(f"  Delay   : {delay:.0f}s between messages")
    print(f"  Dry run : {dry_run}")
    print(f"{'='*56}\n")

    api_key = config["wu_api_key"]
    messages = []

    # ── Stations ──────────────────────────────────────────────────────────
    if not cities_only:
        stations = config["stations"]
        if station_filter:
            norm = [s.upper() for s in station_filter]
            stations = [s for s in stations if s["id"].upper() in norm]

        if stations:
            print(f"Fetching {len(stations)} PWS station(s)...")
            for station in stations:
                print(f"  → {station['id']} ({station['label']})... ",
                      end="", flush=True)
                wx = build_station_wx(station, api_key)
                if wx is None:
                    print(" SKIPPED")
                    continue
                msg = format_message(station["label"], wx)
                messages.append((station["label"], msg))
                print(f"OK  [{len(msg)}ch]")
                for line in msg.split("\n"):
                    print(f"    {line}")
                print()

    # ── Cities ────────────────────────────────────────────────────────────
    if not pws_only:
        cities = config["cities"]
        if city_filter:
            norm = [c.lower() for c in city_filter]
            cities = [c for c in cities if c["name"].lower() in norm]

        if cities:
            print(f"Fetching {len(cities)} city forecast(s)...")
            for city in cities:
                print(f"  → {city['name']}... ", end="", flush=True)
                wx = build_city_wx(city, api_key)
                if wx is None:
                    print(" SKIPPED")
                    continue
                msg = format_message(city["name"], wx)
                messages.append((city["name"], msg))
                print(f"OK  [{len(msg)}ch]")
                for line in msg.split("\n"):
                    print(f"    {line}")
                print()

    # ── Transmit ──────────────────────────────────────────────────────────
    if not messages:
        print("\nNo messages to send.", file=sys.stderr)
        sys.exit(1)

    print(f"{len(messages)} message(s) ready.\n")

    if dry_run:
        print("-- Dry run complete, nothing transmitted --")
        return

    print("Connecting to radio...")
    mc = await connect()
    try:
        desired = CHANNELS[channel_key]
        idx = await resolve_channel_index(mc, desired)
        if idx is None:
            print(
                f"Could not find channel '{desired['name']}' on device.\n"
                f"Run: python meshcore_send.py --list-channels",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"Resolved '{channel_key}' → slot {idx}\n")

        for i, (label, msg) in enumerate(messages):
            print(f"[{i+1}/{len(messages)}] {label}")
            for line in msg.split("\n"):
                print(f"  │ {line}")
            result = await mc.commands.send_chan_msg(idx, msg)
            if result.type == EventType.ERROR:
                print(f"  ✗ Error: {result.payload}", file=sys.stderr)
            else:
                print(f"  ✓ Sent")

            if i < len(messages) - 1:
                print(f"  Waiting {delay:.0f}s...\n")
                await asyncio.sleep(delay)

    finally:
        await mc.disconnect()

    print(f"\n{'='*56}")
    print(f"  Broadcast complete — 73 de W6SAL")
    print(f"{'='*56}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Broadcast Weather Underground data to a MeshCore channel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Configuration:\n"
            "  All config lives in weather.keys (see weather.keys.example).\n"
            "\n"
            "  STATION entries use PWS live observations + forecast (best).\n"
            "  CITY entries use forecast only (current temp is estimated).\n"
            "  Both produce the same message format.\n"
            "\n"
            f"  Available channels: {', '.join(CHANNELS.keys())}\n"
            "\n"
            "  Get your free WU API key: https://www.wunderground.com/member/api-keys\n"
        )
    )
    p.add_argument(
        "--channel", "-c", default=DEFAULT_CHANNEL,
        choices=list(CHANNELS.keys()),
        help=f"Target channel (default: {DEFAULT_CHANNEL})"
    )
    p.add_argument(
        "--keys", "-k", type=Path, default=DEFAULT_KEYS_FILE,
        metavar="FILE", help=f"Path to keys file (default: {DEFAULT_KEYS_FILE.name})"
    )
    p.add_argument(
        "--stations", nargs="+", metavar="ID",
        help="Limit to specific PWS station IDs"
    )
    p.add_argument(
        "--cities", nargs="+", metavar="NAME",
        help="Limit to specific city names"
    )

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--pws-only", action="store_true",
                      help="Only broadcast PWS stations")
    mode.add_argument("--cities-only", action="store_true",
                      help="Only broadcast city forecasts")

    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"Seconds between messages (default: {DEFAULT_DELAY})")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch and preview messages without transmitting")
    args = p.parse_args()

    config = load_config(args.keys)

    if not config["wu_api_key"] or config["wu_api_key"] == "your_api_key_here":
        print(
            "ERROR: WU_API_KEY not configured.\n"
            f"  Edit {args.keys} and set your Weather Underground API key.\n"
            "  Get your free key: https://www.wunderground.com/member/api-keys",
            file=sys.stderr,
        )
        sys.exit(1)

    if not config["stations"] and not config["cities"]:
        print(
            "ERROR: No stations or cities configured.\n"
            f"  Edit {args.keys} or copy weather.keys.example → weather.keys",
            file=sys.stderr,
        )
        sys.exit(1)

    asyncio.run(broadcast(
        config, args.channel, args.stations, args.cities,
        args.pws_only, args.cities_only, args.dry_run, args.delay
    ))


if __name__ == "__main__":
    main()