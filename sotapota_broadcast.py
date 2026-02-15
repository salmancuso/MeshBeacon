#!/usr/bin/env python3
"""
sotapota_broadcast.py - Broadcast active SOTA/POTA spots near San Jose to MeshCore.

Fetches current activator spots from the SOTA and POTA APIs, filters by distance
from San Jose, and broadcasts formatted messages to the configured channel.

Frequency Bands:
  • HF      : 1-30 MHz
  • VHF/UHF : ≥ 30 MHz

Usage:
    python sotapota_broadcast.py --band vhf --vhf-min-radius 5 --vhf-max-radius 50
    python sotapota_broadcast.py --band hf --hf-min-radius 300 --hf-max-radius 1000
    python sotapota_broadcast.py --channel myhams           # Different channel
    python sotapota_broadcast.py --limit 10                # Max spots to broadcast
    python sotapota_broadcast.py --dry-run                 # Preview without transmitting
    python sotapota_broadcast.py --delay 8                 # 8s between messages

Requires:
    pip install requests meshcore
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime, timezone, timedelta
from math import radians, cos, sin, asin, sqrt, atan2, degrees

try:
    import requests
except ImportError:
    print("Missing dependency. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# Reuse connection logic from meshcore_send.py (must be in same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from meshcore_send import (
    CHANNELS, MAX_MSG_LEN, CONNECT_DELAY,
    connect, resolve_channel_index,
)
from meshcore import EventType


# ── Config ────────────────────────────────────────────────────────────────────

# POTA API endpoints
POTA_SPOTS_URL = "https://api.pota.app/spot/activator"

# SOTA API endpoints
SOTA_SPOTS_URL  = "https://api2.sota.org.uk/api/spots"
SOTA_SUMMIT_URL = "https://api2.sota.org.uk/api/summits"

# Center point: San Jose, CA (CM97bg)
SJC_LAT =  37.3382
SJC_LON = -121.8863

DEFAULT_CHANNEL    = "meshhams"
DEFAULT_DELAY      = 5.0
DEFAULT_HF_MAX_RADIUS = 1000   # miles
DEFAULT_VHF_MAX_RADIUS = 100    # miles
DEFAULT_HF_MIN_RADIUS = 0.0 # miles
DEFAULT_VHF_MIN_RADIUS = 0.0 # miles
DEFAULT_LIMIT      = 10
DEFAULT_SOTA_HOURS = 2

# SOTA associations that could be within range (rough pre-filter)
NEARBY_ASSOCIATIONS = {
    "W6", "W7", "W5", "W0", "W1", "W2", "W3", "W4", "W8", "W9",
    "VE",                    # Canada
    "XE",                    # Mexico
}


# ── Distance & Bearing ────────────────────────────────────────────────────────

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two points."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a)) * 0.621371


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing in degrees from point 1 to point 2."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = sin(dlon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    return (degrees(atan2(x, y)) + 360) % 360


def bearing_to_compass(deg: float) -> str:
    """Convert bearing degrees to 8-point compass direction."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(deg / 45) % 8]


# ── Band Classification ──────────────────────────────────────────────────────

def parse_frequency_mhz(freq_str: str) -> float | None:
    """
    Parse frequency string to MHz. Handles:
      - POTA format: kHz as string, e.g. "14307", "146520.0"
      - SOTA format: MHz as string, e.g. "14.062", "145.500"
    """
    if not freq_str:
        return None
    try:
        val = float(freq_str.replace(",", ""))
        if val > 1000:
            return val / 1000.0
        else:
            return val
    except (ValueError, TypeError):
        return None

def is_hf(freq_mhz: float | None) -> bool:
    """Return True if frequency is in HF range (1-30 MHz)."""
    if freq_mhz is None:
        return True  # Default to HF (wider radius) if unknown
    return 1.0 <= freq_mhz < 30.0


def freq_display(freq_mhz: float | None) -> str:
    """Format frequency for display."""
    if freq_mhz is None:
        return "?"
    if freq_mhz >= 100:
        return f"{freq_mhz:.1f}"
    return f"{freq_mhz:.3f}".rstrip("0").rstrip(".")


# ── POTA Spots ────────────────────────────────────────────────────────────────
def fetch_pota_spots() -> list[dict]:
    try:
        print("Fetching POTA spots...")
        resp = requests.get(POTA_SPOTS_URL, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, list):
            return []
        spots = []
        for s in raw:
            lat, lon = s.get("latitude"), s.get("longitude")
            if lat is None or lon is None or "qrt" in (s.get("comments") or "").lower():
                continue
            freq = parse_frequency_mhz(s.get("frequency", ""))
            try:
                spot_dt = datetime.strptime(s.get("spotTime", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except Exception:
                spot_dt = datetime.now(timezone.utc)
            dist = haversine_distance(SJC_LAT, SJC_LON, lat, lon)
            brng = bearing(SJC_LAT, SJC_LON, lat, lon)
            spots.append({
                "program": "POTA", "callsign": s.get("activator", "?"), "reference": s.get("reference", "?"),
                "name": s.get("name", ""), "mode": s.get("mode", "?"), "freq_mhz": freq, "lat": lat, "lon": lon,
                "grid": s.get("grid6") or s.get("grid4") or "", "time_utc": spot_dt, "distance_mi": dist,
                "bearing_dir": bearing_to_compass(brng),
            })
        print(f"  Fetched {len(raw)} POTA spots")
        return spots
    except Exception as e:
        print(f"x POTA fetch failed: {e}", file=sys.stderr)
        return []

# ── SOTA Spots ────────────────────────────────────────────────────────────────
_summit_cache: dict[str, dict | None] = {}
def fetch_summit_details(assoc_code: str, summit_code: str) -> dict | None:
    cache_key = f"{assoc_code}/{summit_code}"
    if cache_key in _summit_cache: return _summit_cache[cache_key]
    url = f"{SOTA_SUMMIT_URL}/{assoc_code}/{summit_code}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200: _summit_cache[cache_key] = None; return None
        data = resp.json()
        result = {"lat": data.get("latitude"), "lon": data.get("longitude"), "name": data.get("name", ""), "grid": data.get("gridRef1", "")}
        _summit_cache[cache_key] = result; return result
    except Exception: _summit_cache[cache_key] = None; return None

def fetch_sota_spots(hours: int) -> list[dict]:
    try:
        limit = -abs(hours)
        url = f"{SOTA_SPOTS_URL}/{limit}/all"
        print(f"Fetching SOTA spots (last {hours}h)...")
        resp = requests.get(url, timeout=15); resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, list): return []
        print(f"  Fetched {len(raw)} SOTA spots")
    except Exception as e:
        print(f"x SOTA spots fetch failed: {e}", file=sys.stderr); return []
    filtered = [s for s in raw if s.get("associationCode", "") and any(s["associationCode"].startswith(a) for a in NEARBY_ASSOCIATIONS)]
    print(f"  {len(filtered)} spots in nearby associations (pre-filter)")
    if not filtered: return []
    print(f"  Looking up summit coordinates...")
    spots = []
    for s in filtered:
        assoc, summit = s.get("associationCode", ""), s.get("summitCode", "")
        if not assoc or not summit: continue
        details = fetch_summit_details(assoc, summit)
        if details is None or details.get("lat") is None: continue
        lat, lon = details["lat"], details["lon"]
        freq = parse_frequency_mhz(s.get("frequency", ""))
        try: spot_dt = datetime.strptime(s.get("timeStamp", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception: spot_dt = datetime.now(timezone.utc)
        dist = haversine_distance(SJC_LAT, SJC_LON, lat, lon)
        brng = bearing(SJC_LAT, SJC_LON, lat, lon)
        spots.append({
            "program": "SOTA", "callsign": s.get("activatorCallsign", "?"), "reference": f"{assoc}/{summit}",
            "name": details.get("name", ""), "mode": s.get("mode", "?"), "freq_mhz": freq, "lat": lat, "lon": lon,
            "grid": details.get("grid", ""), "time_utc": spot_dt, "distance_mi": dist, "bearing_dir": bearing_to_compass(brng),
        })
    print(f"  {len(spots)} SOTA spots with coordinates resolved")
    return spots

# ── Filtering ─────────────────────────────────────────────────────────────────

### MODIFIED ###
def filter_spots(spots: list[dict], hf_max_radius: float, vhf_max_radius: float,
                 hf_min_radius: float, vhf_min_radius: float) -> list[dict]:
    """Filter spots by distance based on frequency band."""
    result = []
    for s in spots:
        dist = s["distance_mi"]
        if is_hf(s["freq_mhz"]):
            if hf_min_radius <= dist <= hf_max_radius:
                result.append(s)
        else: # VHF/UHF
            if vhf_min_radius <= dist <= vhf_max_radius:
                result.append(s)
    return result


# ── Message Formatting ────────────────────────────────────────────────────────
def format_spot_message(spot: dict) -> str:
    """Formats a spot into a multi-line string for display."""
    program, ref, call, mode = spot["program"], spot["reference"], spot["callsign"], spot["mode"] or "?"
    freq, dist, dir_str = freq_display(spot["freq_mhz"]), spot["distance_mi"], spot["bearing_dir"]
    pst = spot["time_utc"] - timedelta(hours=8)
    time_str = pst.strftime("%H:%M PST")
    msg = f"{program}\n{ref}\nCall: {call}\n{mode} {freq}\n{time_str}\n{dist:.0f}mi {dir_str} of SJC"
    if len(msg.encode("utf-8")) > MAX_MSG_LEN:
        msg = f"{program} {ref}\n{call} {mode} {freq}\n{time_str}\n{dist:.0f}mi {dir_str} of SJC"
    if len(msg.encode("utf-8")) > MAX_MSG_LEN:
        msg = msg.encode("utf-8")[:MAX_MSG_LEN].decode("utf-8", errors="ignore")
    return msg

# ── Broadcast ─────────────────────────────────────────────────────────────────

### MODIFIED ###
async def broadcast(channel_key: str, sota_only: bool, pota_only: bool, band: str,
                    hf_max_radius: float, vhf_max_radius: float, hf_min_radius: float, vhf_min_radius: float,
                    limit: int, sota_hours: int, dry_run: bool, delay: float):

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  MeshCore SOTA/POTA Spotter  --  {timestamp}")
    print(f"  Channel  : {channel_key.upper()}")
    mode_str = "SOTA only" if sota_only else ("POTA only" if pota_only else "SOTA + POTA")
    print(f"  Programs : {mode_str}")
    print(f"  Band Filter: {band.upper()}")
    if band != 'vhf': print(f"  HF Radius: {hf_min_radius:.0f} - {hf_max_radius:.0f} mi")
    if band != 'hf': print(f"  VHF Radius: {vhf_min_radius:.0f} - {vhf_max_radius:.0f} mi")
    print(f"  Max spots: {limit}")
    print(f"  Dry run  : {dry_run}")
    print(f"{'='*60}\n")

    all_spots: list[dict] = []
    if not pota_only: all_spots.extend(fetch_sota_spots(sota_hours))
    if not sota_only: all_spots.extend(fetch_pota_spots())
    if not all_spots: print("\nNo spots retrieved from APIs."); return
    print(f"\nTotal raw spots: {len(all_spots)}")

    if all_spots:
        print("Deduplicating spots by callsign (keeping most recent)...")
        all_spots.sort(key=lambda s: s['time_utc'].timestamp())
        unique_spots_by_call = {s['callsign']: s for s in all_spots}
        all_spots = list(unique_spots_by_call.values())
        print(f"  {len(unique_spots_by_call)} unique callsigns remain")

    if band != 'all':
        initial_count = len(all_spots)
        if band == 'hf': all_spots = [s for s in all_spots if is_hf(s['freq_mhz'])]
        elif band == 'vhf': all_spots = [s for s in all_spots if not is_hf(s['freq_mhz'])]
        print(f"After band filter ('{band.upper()}'): {initial_count} -> {len(all_spots)} spots")

    nearby = filter_spots(all_spots, hf_max_radius, vhf_max_radius, hf_min_radius, vhf_min_radius)
    print(f"After distance filter: {len(nearby)} spots")

    if not nearby: print(f"\nNo active SOTA/POTA spots within the specified band/range."); return

    nearby.sort(key=lambda s: (s["distance_mi"], -s["time_utc"].timestamp()))
    if len(nearby) > limit:
        print(f"Limiting to {limit} closest spots (of {len(nearby)})")
        nearby = nearby[:limit]

    messages = []
    print(f"\n{len(nearby)} spot(s) to broadcast:\n")
    for i, spot in enumerate(nearby, 1):
        band_str = "HF" if is_hf(spot["freq_mhz"]) else "VHF/UHF"
        print(f"  [{i}] {spot['program']} {spot['reference']} - {spot['callsign']} {spot['mode']} {freq_display(spot['freq_mhz'])} ({band_str}) - {spot['distance_mi']:.0f}mi {spot['bearing_dir']}")
        messages.append((f"{spot['program']} {spot['callsign']}", format_spot_message(spot)))

    if dry_run:
        print(f"\n--- Message previews ---\n")
        for label, msg in messages:
            print(f"  [{len(msg.encode('utf-8')):3d}B] {label}\n       " + "\n       ".join(msg.split("\n")) + "\n")
        print("-- Dry run complete, nothing transmitted --")
        return

    print("\nConnecting to radio...")
    mc = await connect()
    try:
        idx = await resolve_channel_index(mc, CHANNELS[channel_key])
        if idx is None: print(f"Could not find channel '{CHANNELS[channel_key]['name']}'", file=sys.stderr); sys.exit(1)
        print(f"Resolved '{channel_key}' -> slot {idx}\n")
        for i, (label, msg) in enumerate(messages):
            print(f"[{i + 1}/{len(messages)}] {label}\n  | " + "\n  | ".join(msg.split("\n")))
            result = await mc.commands.send_chan_msg(idx, msg)
            if result.type == EventType.ERROR: print(f"  x Error: {result.payload}", file=sys.stderr)
            else: print(f"  + Sent")
            if i < len(messages) - 1: print(f"  Waiting {delay:.0f}s...\n"); await asyncio.sleep(delay)
    finally:
        await mc.disconnect()
    print(f"\n{'='*60}\n  Broadcast complete -- 73 de W6SAL\n{'='*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ### MODIFIED ###
    p = argparse.ArgumentParser(
        description="Broadcast nearby SOTA/POTA activator spots to MeshCore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Frequency Bands:\n"
            "  HF      : 1-30 MHz\n"
            "  VHF/UHF : ≥ 30 MHz\n\n"
            "Example Usage:\n"
            "  # Find VHF/UHF spots between 5 and 50 miles\n"
            "  python sotapota_broadcast.py --band vhf --vhf-min-radius 5 --vhf-max-radius 50\n\n"
            "  # Find HF spots between 300 and 1000 miles away\n"
            "  python sotapota_broadcast.py --band hf --hf-min-radius 300 --hf-max-radius 1000\n"
        )
    )
    p.add_argument("--channel", "-c", default=DEFAULT_CHANNEL, choices=list(CHANNELS.keys()) or None, help=f"Target channel (default: {DEFAULT_CHANNEL})")
    source = p.add_mutually_exclusive_group()
    source.add_argument("--sota-only", action="store_true", help="Only broadcast SOTA spots")
    source.add_argument("--pota-only", action="store_true", help="Only broadcast POTA spots")
    p.add_argument("--band", default="all", choices=['all', 'hf', 'vhf'], help="Filter by frequency band (default: all)")
    p.add_argument("--hf-max-radius", type=float, default=DEFAULT_HF_MAX_RADIUS, metavar="MI", help=f"Max distance for HF spots (default: {DEFAULT_HF_MAX_RADIUS})")
    p.add_argument("--hf-min-radius", type=float, default=DEFAULT_HF_MIN_RADIUS, metavar="MI", help=f"Min distance for HF spots (default: {DEFAULT_HF_MIN_RADIUS})")
    p.add_argument("--vhf-max-radius", type=float, default=DEFAULT_VHF_MAX_RADIUS, metavar="MI", help=f"Max distance for VHF/UHF spots (default: {DEFAULT_VHF_MAX_RADIUS})")
    p.add_argument("--vhf-min-radius", type=float, default=DEFAULT_VHF_MIN_RADIUS, metavar="MI", help=f"Min distance for VHF/UHF spots (default: {DEFAULT_VHF_MIN_RADIUS})")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Max spots to broadcast (default: {DEFAULT_LIMIT})")
    p.add_argument("--hours", type=int, default=DEFAULT_SOTA_HOURS, help=f"SOTA lookback hours (default: {DEFAULT_SOTA_HOURS})")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help=f"Seconds between messages (default: {DEFAULT_DELAY})")
    p.add_argument("--dry-run", action="store_true", help="Fetch and preview messages without transmitting")
    args = p.parse_args()

    asyncio.run(broadcast(
        args.channel, args.sota_only, args.pota_only, args.band,
        args.hf_max_radius, args.vhf_max_radius, args.hf_min_radius, args.vhf_min_radius,
        args.limit, args.hours, args.dry_run, args.delay,
    ))


if __name__ == "__main__":
    main()