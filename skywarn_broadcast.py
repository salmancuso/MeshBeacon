#!/usr/bin/env python3
"""
skywarn_broadcast.py - Broadcast NWS severe weather alerts to MeshCore channels.

Fetches active watches, warnings, and advisories from the National Weather
Service (NWS) API and broadcasts formatted messages to the configured channel.
Supports filtering by zip code + radius, direct lat/lon, severity level, and
alert type keywords.

Data Source:
  • NWS API (api.weather.gov) — free, no API key required
  • Zippopotam.us — free zip-to-coordinate geocoding, no API key required

Severity Levels (NWS):
  🔴 Extreme   — Extraordinary threat to life or property
  🟠 Severe    — Significant threat to life or property
  🟡 Moderate  — Possible threat to life or property
  🟢 Minor     — Minimal or no known threat
  ⚪ Unknown   — Severity not yet determined

Usage:
    python skywarn_broadcast.py --zip 95125 --radius 50
    python skywarn_broadcast.py --lat 37.3382 --lon -121.8863 --radius 75
    python skywarn_broadcast.py --zip 95125 --severity severe
    python skywarn_broadcast.py --zip 95125 --type tornado,flood
    python skywarn_broadcast.py --channel saltest --dry-run
    python skywarn_broadcast.py --state CA                # All alerts for state

Requires:
    pip install requests meshcore
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime, timezone, timedelta
from math import radians, cos, sin, asin, sqrt

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

NWS_ALERTS_URL  = "https://api.weather.gov/alerts/active"
NWS_POINTS_URL  = "https://api.weather.gov/points"
ZIPPO_URL       = "https://api.zippopotam.us/us"

NWS_USER_AGENT  = "(MeshCore Skywarn Broadcast, w6sal@arrl.net)"

# Center point defaults: San Jose, CA (CM97bg)
DEFAULT_LAT     =  37.3382
DEFAULT_LON     = -121.8863
DEFAULT_STATE   = "CA"

DEFAULT_CHANNEL = "meshhams"
DEFAULT_DELAY   = 5.0
DEFAULT_RADIUS  = 50      # miles
DEFAULT_LIMIT   = 10

# Severity ordering (highest → lowest)
SEVERITY_RANK = {
    "extreme":  0,
    "severe":   1,
    "moderate": 2,
    "minor":    3,
    "unknown":  4,
}

SEVERITY_ICON = {
    "extreme":  "🔴",
    "severe":   "🟠",
    "moderate": "🟡",
    "minor":    "🟢",
    "unknown":  "⚪",
}

# Skywarn-relevant event types (NWS event strings)
# Used when --skywarn-only is set to filter to classic Skywarn events
SKYWARN_EVENTS = {
    "tornado warning", "tornado watch",
    "severe thunderstorm warning", "severe thunderstorm watch",
    "flash flood warning", "flash flood watch",
    "flood warning", "flood watch", "flood advisory",
    "special weather statement",
    "severe weather statement",
    "tornado emergency",
    "particularly dangerous situation",
}


# ── Geocoding ────────────────────────────────────────────────────────────────

def zip_to_coords(zipcode: str) -> dict | None:
    """
    Convert US zip code to lat/lon/state via Zippopotam.us (free, no key).
    Returns {"lat": float, "lon": float, "state": str, "place": str} or None.
    """
    try:
        url = f"{ZIPPO_URL}/{zipcode}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            print(f"Zip code '{zipcode}' not found.", file=sys.stderr)
            return None
        resp.raise_for_status()
        data = resp.json()
        places = data.get("places", [])
        if not places:
            return None
        place = places[0]
        return {
            "lat":   float(place["latitude"]),
            "lon":   float(place["longitude"]),
            "state": place.get("state abbreviation", ""),
            "place": place.get("place name", ""),
        }
    except Exception as e:
        print(f"Zip lookup failed: {e}", file=sys.stderr)
        return None


def get_state_from_point(lat: float, lon: float) -> str | None:
    """Use NWS /points endpoint to determine state for a lat/lon."""
    try:
        url = f"{NWS_POINTS_URL}/{lat:.4f},{lon:.4f}"
        headers = {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        props = resp.json().get("properties", {})
        # relativeLocation.properties.state
        rel = props.get("relativeLocation", {}).get("properties", {})
        return rel.get("state", None)
    except Exception as e:
        print(f"NWS point lookup failed: {e}", file=sys.stderr)
        return None


# ── Distance ─────────────────────────────────────────────────────────────────

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two points."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a)) * 0.621371


def polygon_centroid(coords: list) -> tuple[float, float] | None:
    """
    Compute centroid of a GeoJSON polygon ring.
    coords is a list of [lon, lat] pairs.
    Returns (lat, lon) or None.
    """
    if not coords:
        return None
    lats = [c[1] for c in coords]
    lons = [c[0] for c in coords]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def alert_centroid(feature: dict) -> tuple[float, float] | None:
    """
    Extract a representative lat/lon from an NWS alert feature.
    Tries geometry polygon first, then falls back to None.
    """
    geom = feature.get("geometry")
    if geom and geom.get("type") == "Polygon":
        rings = geom.get("coordinates", [])
        if rings:
            return polygon_centroid(rings[0])
    if geom and geom.get("type") == "MultiPolygon":
        # Use first polygon
        polys = geom.get("coordinates", [])
        if polys and polys[0]:
            return polygon_centroid(polys[0][0])
    return None


# ── NWS API ──────────────────────────────────────────────────────────────────

def fetch_alerts_by_state(state: str) -> list[dict]:
    """Fetch all active alerts for a US state."""
    headers = {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}
    params = {"area": state}
    try:
        resp = requests.get(NWS_ALERTS_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("features", [])
    except Exception as e:
        print(f"NWS alerts fetch failed: {e}", file=sys.stderr)
        return []


def fetch_alerts_by_point(lat: float, lon: float) -> list[dict]:
    """Fetch active alerts for a specific point."""
    headers = {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}
    params = {"point": f"{lat:.4f},{lon:.4f}"}
    try:
        resp = requests.get(NWS_ALERTS_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("features", [])
    except Exception as e:
        print(f"NWS point alerts fetch failed: {e}", file=sys.stderr)
        return []


def fetch_alerts(lat: float, lon: float, state: str, radius: float) -> list[dict]:
    """
    Fetch and merge alerts from both point and state queries.
    Deduplicates by alert ID and filters by radius from center point.
    """
    print(f"Fetching NWS alerts for {state} (radius {radius:.0f}mi from {lat:.4f},{lon:.4f})...")

    # Get point-specific alerts (always relevant)
    point_alerts = fetch_alerts_by_point(lat, lon)
    print(f"  Point alerts: {len(point_alerts)}")

    # Get state-level alerts for radius filtering
    state_alerts = fetch_alerts_by_state(state)
    print(f"  State alerts: {len(state_alerts)}")

    # Merge and deduplicate by alert ID
    seen_ids = set()
    merged = []
    for feature in point_alerts + state_alerts:
        props = feature.get("properties", {})
        alert_id = props.get("id", "")
        if alert_id in seen_ids:
            continue
        seen_ids.add(alert_id)
        merged.append(feature)

    print(f"  Merged (deduped): {len(merged)}")

    # Filter by radius using polygon centroid
    filtered = []
    no_geom_count = 0
    for feature in merged:
        centroid = alert_centroid(feature)
        if centroid:
            dist = haversine_distance(lat, lon, centroid[0], centroid[1])
            if dist <= radius:
                feature["_distance_mi"] = dist
                filtered.append(feature)
        else:
            # No geometry — check if it was in the point query (directly relevant)
            props = feature.get("properties", {})
            alert_id = props.get("id", "")
            point_ids = {f.get("properties", {}).get("id", "") for f in point_alerts}
            if alert_id in point_ids:
                feature["_distance_mi"] = 0.0
                filtered.append(feature)
            else:
                no_geom_count += 1

    if no_geom_count:
        print(f"  Skipped {no_geom_count} alerts without geometry (outside point match)")
    print(f"  Within radius: {len(filtered)}")

    return filtered


# ── Filtering ────────────────────────────────────────────────────────────────

def filter_alerts(alerts: list[dict], min_severity: str | None,
                  type_filter: list[str] | None, skywarn_only: bool) -> list[dict]:
    """Apply severity, type, and Skywarn filters."""
    result = []
    for feature in alerts:
        props = feature.get("properties", {})
        severity = (props.get("severity") or "unknown").lower()
        event = (props.get("event") or "").lower()

        # Severity filter
        if min_severity:
            min_rank = SEVERITY_RANK.get(min_severity.lower(), 4)
            alert_rank = SEVERITY_RANK.get(severity, 4)
            if alert_rank > min_rank:
                continue

        # Type keyword filter
        if type_filter:
            if not any(kw.lower() in event for kw in type_filter):
                continue

        # Skywarn-only filter
        if skywarn_only:
            if event not in SKYWARN_EVENTS:
                continue

        result.append(feature)

    return result


# ── Message Formatting ───────────────────────────────────────────────────────

def format_alert_message(feature: dict) -> str:
    """
    Format an NWS alert into a compact multi-line MeshCore message (≤135 bytes).

    Format:
        ⚠️ SKYWARN
        🟠 Severe Thunderstorm Wrn
        Santa Clara County, CA
        Until 3:45 PM PST
    """
    props = feature.get("properties", {})
    severity = (props.get("severity") or "Unknown").lower()
    icon = SEVERITY_ICON.get(severity, "⚪")
    event = props.get("event") or "Weather Alert"

    # Shorten common event names to save bytes
    short_event = event
    replacements = [
        ("Warning", "Wrn"), ("Watch", "Wtch"), ("Advisory", "Adv"),
        ("Statement", "Stmt"), ("Severe ", "Svr "), ("Thunderstorm", "T-Storm"),
        ("Special Weather ", "Spc WX "),
    ]
    for long, short in replacements:
        short_event = short_event.replace(long, short)

    # Area description
    area = props.get("areaDesc") or ""
    # Truncate long area descriptions
    if len(area) > 45:
        # Take first county/zone mentioned
        area = area.split(";")[0].strip()
    if len(area) > 45:
        area = area[:42] + "..."

    # Expiration time
    expires_str = ""
    expires = props.get("expires") or props.get("ends")
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            pst = exp_dt - timedelta(hours=8)
            expires_str = f"Until {pst.strftime('%-I:%M %p')} PST"
        except Exception:
            expires_str = ""

    # Distance
    dist = feature.get("_distance_mi")
    dist_str = f"{dist:.0f}mi" if dist is not None and dist > 0 else ""

    # Build message
    lines = ["⚠️ SKYWARN", f"{icon} {short_event}"]
    if area:
        lines.append(area)
    if expires_str:
        lines.append(expires_str)
    if dist_str:
        lines.append(dist_str)

    msg = "\n".join(lines)

    # Ensure within byte limit
    if len(msg.encode("utf-8")) > MAX_MSG_LEN:
        # Drop distance, then shorten area further
        lines_trimmed = ["⚠️ SKYWARN", f"{icon} {short_event}"]
        if area:
            area_short = area[:30] + "..." if len(area) > 30 else area
            lines_trimmed.append(area_short)
        if expires_str:
            lines_trimmed.append(expires_str)
        msg = "\n".join(lines_trimmed)

    if len(msg.encode("utf-8")) > MAX_MSG_LEN:
        msg = msg.encode("utf-8")[:MAX_MSG_LEN].decode("utf-8", errors="ignore")

    return msg


def format_no_alerts_message(place: str, radius: float) -> str:
    """Format an all-clear message."""
    return f"⚠️ SKYWARN\n✅ No active alerts\n{place}\nRadius: {radius:.0f}mi"


# ── Broadcast ────────────────────────────────────────────────────────────────

async def broadcast(lat: float, lon: float, state: str, place: str,
                    radius: float, channel_key: str, min_severity: str | None,
                    type_filter: list[str] | None, skywarn_only: bool,
                    send_clear: bool, limit: int, dry_run: bool, delay: float):

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  MeshCore Skywarn Broadcast  (NWS API)")
    print(f"  {timestamp}")
    print(f"  Center  : {lat:.4f}, {lon:.4f} ({place or state})")
    print(f"  Radius  : {radius:.0f} mi")
    print(f"  Channel : {channel_key.upper()}")
    print(f"  Severity: {min_severity or 'all'}")
    print(f"  Skywarn : {'events only' if skywarn_only else 'all NWS alerts'}")
    print(f"  Dry run : {dry_run}")
    print(f"{'='*60}\n")

    # Fetch alerts
    raw_alerts = fetch_alerts(lat, lon, state, radius)

    # Apply filters
    alerts = filter_alerts(raw_alerts, min_severity, type_filter, skywarn_only)
    print(f"After filters: {len(alerts)} alert(s)")

    if not alerts:
        print(f"\n✅ No active alerts within {radius:.0f}mi of {place or 'center'}.")
        if send_clear:
            msg = format_no_alerts_message(place or state, radius)
            messages = [("All Clear", msg)]
        else:
            print("  (Use --send-clear to broadcast an all-clear message)")
            return
    else:
        # Sort by severity (most severe first), then distance
        alerts.sort(key=lambda f: (
            SEVERITY_RANK.get((f.get("properties", {}).get("severity") or "unknown").lower(), 4),
            f.get("_distance_mi", 999),
        ))

        if len(alerts) > limit:
            print(f"Limiting to {limit} most severe alerts (of {len(alerts)})")
            alerts = alerts[:limit]

        # Build messages
        messages = []
        print(f"\n{len(alerts)} alert(s) to broadcast:\n")
        for i, feature in enumerate(alerts, 1):
            props = feature.get("properties", {})
            severity = props.get("severity", "?")
            event = props.get("event", "?")
            area = (props.get("areaDesc") or "")[:50]
            dist = feature.get("_distance_mi", 0)
            print(f"  [{i}] {severity}: {event} — {area} ({dist:.0f}mi)")
            msg = format_alert_message(feature)
            messages.append((f"{event}", msg))

    if dry_run:
        print(f"\n--- Message previews ---\n")
        for label, msg in messages:
            byte_len = len(msg.encode("utf-8"))
            print(f"  [{byte_len:3d}B] {label}")
            for line in msg.split("\n"):
                print(f"       {line}")
            print()
        print("-- Dry run complete, nothing transmitted --")
        return

    # Transmit
    print("\nConnecting to radio...")
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

    print(f"\n{'='*60}")
    print(f"  Broadcast complete — 73 de W6SAL")
    print(f"{'='*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Broadcast NWS severe weather alerts (Skywarn) to MeshCore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Location (pick one):\n"
            "  --zip 95125             Convert zip to lat/lon automatically\n"
            "  --lat 37.34 --lon -121.89   Direct coordinates\n"
            "  --state CA              All alerts for a state (no radius filter)\n"
            "\n"
            "Severity Levels:\n"
            "  extreme  — Extraordinary threat (tornado emergency, etc.)\n"
            "  severe   — Significant threat (warnings)\n"
            "  moderate — Possible threat (watches)\n"
            "  minor    — Minimal threat (advisories)\n"
            "\n"
            "Examples:\n"
            "  python skywarn_broadcast.py --zip 95125 --radius 50\n"
            "  python skywarn_broadcast.py --zip 73301 --severity severe --dry-run\n"
            "  python skywarn_broadcast.py --lat 37.34 --lon -121.89 --skywarn-only\n"
            "  python skywarn_broadcast.py --zip 95125 --type tornado,flood\n"
            "\n"
            "Data Source:\n"
            "  NWS API (api.weather.gov) — free, no API key required\n"
            f"\n  Available channels: {', '.join(CHANNELS.keys())}\n"
        )
    )

    # Location args
    loc = p.add_argument_group("location")
    loc.add_argument("--zip", "-z", metavar="ZIP",
                     help="US zip code (auto-converts to lat/lon)")
    loc.add_argument("--lat", type=float, metavar="DEG",
                     help="Latitude (decimal degrees)")
    loc.add_argument("--lon", type=float, metavar="DEG",
                     help="Longitude (decimal degrees)")
    loc.add_argument("--state", "-s", metavar="ST",
                     help="State abbreviation (e.g. CA, TX) — fetches all state alerts")

    # Filter args
    filt = p.add_argument_group("filters")
    filt.add_argument("--radius", "-r", type=float, default=DEFAULT_RADIUS,
                      metavar="MI",
                      help=f"Radius in miles from center (default: {DEFAULT_RADIUS})")
    filt.add_argument("--severity", choices=["extreme", "severe", "moderate", "minor"],
                      help="Minimum severity level to include")
    filt.add_argument("--type", dest="type_filter", metavar="KEYWORDS",
                      help="Comma-separated event type keywords (e.g. tornado,flood)")
    filt.add_argument("--skywarn-only", action="store_true",
                      help="Only classic Skywarn events (tornado, severe t-storm, flash flood)")

    # Broadcast args
    p.add_argument("--channel", "-c", default=DEFAULT_CHANNEL,
                   choices=list(CHANNELS.keys()),
                   help=f"Target channel (default: {DEFAULT_CHANNEL})")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"Max alerts to broadcast (default: {DEFAULT_LIMIT})")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"Seconds between messages (default: {DEFAULT_DELAY})")
    p.add_argument("--send-clear", action="store_true",
                   help="Send an all-clear message when no alerts are active")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch and preview messages without transmitting")

    args = p.parse_args()

    # Resolve location
    lat, lon, state, place = DEFAULT_LAT, DEFAULT_LON, DEFAULT_STATE, "San Jose, CA"

    if args.zip:
        geo = zip_to_coords(args.zip)
        if geo is None:
            print(f"Could not resolve zip code '{args.zip}'.", file=sys.stderr)
            sys.exit(1)
        lat, lon, state = geo["lat"], geo["lon"], geo["state"]
        place = f"{geo['place']}, {state}"
        print(f"Zip {args.zip} → {place} ({lat:.4f}, {lon:.4f})")
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
        place = f"{lat:.4f}, {lon:.4f}"
        if args.state:
            state = args.state.upper()
        else:
            resolved = get_state_from_point(lat, lon)
            if resolved:
                state = resolved
            else:
                print("Could not determine state. Use --state.", file=sys.stderr)
                sys.exit(1)
    elif args.state:
        state = args.state.upper()
        place = state
        # For state-only mode, use a very large radius
        if args.radius == DEFAULT_RADIUS:
            args.radius = 9999
    else:
        print(f"No location specified, using default: {place}")

    # Parse type filter
    type_filter = None
    if args.type_filter:
        type_filter = [t.strip() for t in args.type_filter.split(",") if t.strip()]

    asyncio.run(broadcast(
        lat, lon, state, place, args.radius, args.channel,
        args.severity, type_filter, args.skywarn_only,
        args.send_clear, args.limit, args.dry_run, args.delay,
    ))


if __name__ == "__main__":
    main()