#!/usr/bin/env python3
"""
earthquake_broadcast.py - Fetch recent earthquakes near SF Bay Area and broadcast to MeshCore.

Monitors USGS earthquake data for magnitude 2.5+ events within 100 miles of San Jose.
Sends one message per earthquake to the configured channel.

Uses USGS Earthquake API (free, no API key required).

Usage:
    python earthquake_broadcast.py                     # Last 24hr, mag 2.5+ → hamradio
    python earthquake_broadcast.py --hours 72          # Last 3 days
    python earthquake_broadcast.py --minmag 3.0        # Only magnitude 3.0+
    python earthquake_broadcast.py --limit 5           # Max 5 earthquakes
    python earthquake_broadcast.py --channel public    # Different channel
    python earthquake_broadcast.py --dry-run           # Preview messages, no transmit
    python earthquake_broadcast.py --delay 10          # 10s between messages

Requires:
    pip install requests meshcore
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime, timedelta
from math import radians, cos, sin, asin, sqrt

try:
    import requests
except ImportError:
    print("Missing dependency. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# Reuse all connection logic from meshcore_send.py (must be in same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from meshcore_send import (
    CHANNELS, MAX_MSG_LEN, CONNECT_DELAY,
    connect, resolve_channel_index,
)
from meshcore import EventType

# ── Config ────────────────────────────────────────────────────────────────────
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# Center point: San Jose, CA (CM97bg)
SJC_LAT =  37.3382
SJC_LON = -121.8863

# Search radius: 100 miles = ~160.93 km
SEARCH_RADIUS_KM = 160.93

DEFAULT_CHANNEL  = "hamradio"
DEFAULT_DELAY    = 5.0
DEFAULT_HOURS    = 24
DEFAULT_MINMAG   = 2.5
DEFAULT_LIMIT    = 10


# ── Distance Calculation ──────────────────────────────────────────────────────

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance in miles between two points."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    km = 6371 * c
    return km * 0.621371  # Convert to miles


# ── USGS Earthquake Fetch ─────────────────────────────────────────────────────

def fetch_earthquakes(hours: int, minmag: float, limit: int) -> list[dict] | None:
    """
    Fetch recent earthquakes from USGS within 100 miles of San Jose.
    
    Returns list of earthquake dicts with keys:
        - magnitude, place, time, depth, lat, lon, distance_mi, url
    """
    # Calculate starttime (UTC)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)
    
    params = {
        "format":        "geojson",
        "starttime":     start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime":       end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude":  minmag,
        "latitude":      SJC_LAT,
        "longitude":     SJC_LON,
        "maxradiuskm":   SEARCH_RADIUS_KM,
        "orderby":       "time",
        "limit":         limit,
    }
    
    try:
        resp = requests.get(USGS_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        earthquakes = []
        for feature in data.get("features", []):
            props = feature["properties"]
            coords = feature["geometry"]["coordinates"]
            
            lon, lat, depth_km = coords[0], coords[1], coords[2]
            distance_mi = haversine_distance(SJC_LAT, SJC_LON, lat, lon)
            
            # Convert Unix timestamp (ms) to datetime
            eq_time = datetime.fromtimestamp(props["time"] / 1000.0)
            
            earthquakes.append({
                "magnitude":   props.get("mag", 0.0),
                "place":       props.get("place", "Unknown location"),
                "time":        eq_time,
                "depth":       depth_km,
                "lat":         lat,
                "lon":         lon,
                "distance_mi": distance_mi,
                "url":         props.get("url", ""),
            })
        
        return earthquakes
        
    except Exception as e:
        print(f"x Earthquake fetch failed: {e}", file=sys.stderr)
        return None


# ── Message Formatting ────────────────────────────────────────────────────────

def format_message(eq: dict) -> str:
    """
    Format earthquake message to fit within 135 bytes.
    
    Format:
    EARTHQUAKE
    🟡 M3.3 - 5 km SW of Ridgemark
    46.5mi from SJC | Depth: 5.0mi
    Feb 11 03:34 PST
    """
    # Clean up place string - USGS format is often "14km SE of Gilroy, CA"
    place = eq["place"]
    # Remove state abbreviation if present
    place = place.replace(", CA", "").replace(", California", "")
    
    # Convert depth from km to miles
    depth_mi = eq["depth"] * 0.621371
    
    # Get magnitude emoji
    emoji = magnitude_emoji(eq["magnitude"])
    
    # Format time (no seconds to save space)
    time_str = eq["time"].strftime("%b %d %H:%M")
    
    # Build message with line breaks
    msg = (
        f"EARTHQUAKE\n"
        f"{emoji} M{eq['magnitude']:.1f} - {place}\n"
        f"{eq['distance_mi']:.1f}mi from SJC | Depth: {depth_mi:.1f}mi\n"
        f"{time_str} PST"
    )
    
    # Truncate if needed (for very long place names)
    if len(msg.encode("utf-8")) > MAX_MSG_LEN:
        # Try without distance from SJC
        msg = (
            f"EARTHQUAKE\n"
            f"{emoji} M{eq['magnitude']:.1f} - {place}\n"
            f"Depth: {depth_mi:.1f}mi\n"
            f"{time_str} PST"
        )
    
    if len(msg.encode("utf-8")) > MAX_MSG_LEN:
        # Final fallback - truncate place name
        max_place_len = 40
        if len(place) > max_place_len:
            place = place[:max_place_len-3] + "..."
        msg = (
            f"EARTHQUAKE\n"
            f"{emoji} M{eq['magnitude']:.1f} - {place}\n"
            f"Depth: {depth_mi:.1f}mi\n"
            f"{time_str} PST"
        )
    
    return msg[:MAX_MSG_LEN]


def magnitude_emoji(mag: float) -> str:
    """Return emoji based on magnitude severity."""
    if mag >= 5.0: return "🔴"    # Major
    if mag >= 4.0: return "🟠"    # Moderate  
    if mag >= 3.0: return "🟡"    # Light
    return "🟢"                   # Minor


# ── Broadcast ─────────────────────────────────────────────────────────────────

async def broadcast(channel_key: str, hours: int, minmag: float, limit: int,
                    dry_run: bool, delay: float):
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  MeshCore Earthquake Monitor  --  {timestamp}")
    print(f"  Channel  : {channel_key.upper()}")
    print(f"  Period   : Last {hours} hours")
    print(f"  Min Mag  : {minmag}")
    print(f"  Max EQs  : {limit}")
    print(f"  Radius   : 100 miles from San Jose")
    print(f"  Delay    : {delay:.0f}s between messages")
    print(f"  Dry run  : {dry_run}")
    print(f"{'='*60}\n")
    
    # Fetch earthquake data
    print(f"Fetching earthquakes from USGS...")
    earthquakes = fetch_earthquakes(hours, minmag, limit)
    
    if earthquakes is None:
        print("Failed to fetch earthquake data. Aborting.", file=sys.stderr)
        sys.exit(1)
    
    if not earthquakes:
        print(f"\n✓ No earthquakes found (M{minmag}+ in last {hours}hr, within 100mi)")
        print("  This is good news! The Bay Area is quiet.\n")
        if not dry_run:
            # Optionally send an "all clear" message
            print("No earthquakes to report. Exiting without transmission.")
        return
    
    # Sort by magnitude (highest first) then by time (most recent first)
    earthquakes.sort(key=lambda eq: (-eq["magnitude"], -eq["time"].timestamp()))
    
    # Build messages
    messages = []
    print(f"\nFound {len(earthquakes)} earthquake(s):\n")
    for i, eq in enumerate(earthquakes, 1):
        emoji = magnitude_emoji(eq["magnitude"])
        depth_mi = eq["depth"] * 0.621371
        # print(f"EARTHQUAKE {emoji}\nM{eq['magnitude']:.1f} - {eq['place']}")
        # print(f"{eq['distance_mi']:.1f}mi from SJC | Depth: {eq['depth']:.1f}km ({depth_mi:.1f}mi)")
        # print(f"{eq['time'].strftime('%Y-%m-%d %H:%M:%S')} PST\n")
        msg = format_message(eq)
        messages.append((f"Quake {i}", msg))
    
    if dry_run:
        print("-- Dry run complete, nothing transmitted --")
        return
    
    # Connect and transmit
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
        
        print(f"Resolved '{channel_key}' -> slot {idx}\n")
        
        for i, (label, msg) in enumerate(messages):
            print(f"[{i+1}/{len(messages)}] {label}")
            print(f"  -> {msg!r}")
            result = await mc.commands.send_chan_msg(idx, msg)
            if result.type == EventType.ERROR:
                print(f"  x Error: {result.payload}", file=sys.stderr)
            else:
                print(f"  + Sent")
            
            if i < len(messages) - 1:
                print(f"  Waiting {delay:.0f}s...\n")
                await asyncio.sleep(delay)
    
    finally:
        await mc.disconnect()
    
    print(f"\n{'='*60}")
    print(f"  Broadcast complete -- 73 de W6SAL")
    print(f"{'='*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Monitor and broadcast USGS earthquake data for SF Bay Area",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Data source:\n"
            "  USGS Earthquake Hazards Program\n"
            "  https://earthquake.usgs.gov/fdsnws/event/1/\n"
            "\n"
            "Search area:\n"
            "  100-mile radius from San Jose, CA (37.3382, -121.8863)\n"
            "  Covers: SF, Oakland, Santa Cruz, Gilroy, Hollister, Monterey,\n"
            "          Palo Alto, Fremont, Pleasanton, Walnut Creek, Martinez,\n"
            "          Vallejo, Napa, Santa Rosa, Marin, Half Moon Bay, etc.\n"
            "\n"
            "Sample output:\n"
            "  EARTHQUAKE\n"
            "  🟡 M3.3 - 5 km SW of Ridgemark\n"
            "  46.5mi from SJC | Depth: 5.0mi\n"
            "  Feb 11 03:34 PST\n"
            "\n"
            f"Available channels: {', '.join(CHANNELS.keys())}\n"
        )
    )
    p.add_argument(
        "--channel", "-c",
        default=DEFAULT_CHANNEL,
        choices=list(CHANNELS.keys()),
        help=f"Target channel (default: {DEFAULT_CHANNEL})"
    )
    p.add_argument(
        "--hours",
        type=int,
        default=DEFAULT_HOURS,
        help=f"Hours of history to check (default: {DEFAULT_HOURS})"
    )
    p.add_argument(
        "--minmag",
        type=float,
        default=DEFAULT_MINMAG,
        help=f"Minimum magnitude to report (default: {DEFAULT_MINMAG})"
    )
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of earthquakes to report (default: {DEFAULT_LIMIT})"
    )
    p.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Seconds between messages (default: {DEFAULT_DELAY})"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and preview messages without transmitting"
    )
    args = p.parse_args()
    
    asyncio.run(broadcast(
        args.channel,
        args.hours,
        args.minmag,
        args.limit,
        args.dry_run,
        args.delay
    ))


if __name__ == "__main__":
    main()