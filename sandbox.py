#!/usr/bin/env python3
"""
skywarn_broadcast.py - Broadcast NWS severe weather alerts to MeshCore channels.

Fetches active watches, warnings, and advisories from the National Weather
Service (NWS) API and broadcasts formatted messages to the configured channel.

Supports filtering by:
  • Zone codes (NWS county/fire weather zone, e.g., CAC085 for Santa Clara County)
  • Zip code + radius
  • Direct lat/lon + radius
  • State
  • Severity level and alert type keywords

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
    # Bay Area by zone (recommended for sfoskywarn)
    python skywarn_broadcast.py --zone CAC001,CAC013,CAC041,CAC053,CAC055,CAC069,CAC075,CAC081,CAC085,CAC087,CAC097
    
    # Traditional radius-based
    python skywarn_broadcast.py --zip 95125 --radius 50
    python skywarn_broadcast.py --lat 37.3382 --lon -121.8863 --radius 75
    
    # Preview without transmitting
    python skywarn_broadcast.py --zone CAC085 --dry-run

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

# Classic Skywarn-relevant event types (NWS event strings)
# Used when --skywarn-only is set to filter to classic Skywarn events
SKYWARN_EVENTS = {
    "tornado warning", "tornado watch", "tornado emergency",
    "severe thunderstorm warning", "severe thunderstorm watch",
    "flash flood warning", "flash flood watch",
    "flood warning", "flood watch", "flood advisory",
    "special weather statement",
    "severe weather statement",
    "particularly dangerous situation",
}

# Bay Area NWS MTR County Warning Area (CWA) — 11-county zone codes
# Reference: https://sfoskywarn.org
BAY_AREA_ZONES = [
    "CAC001",  # Alameda
    "CAC013",  # Contra Costa
    "CAC041",  # Marin
    "CAC053",  # Monterey
    "CAC055",  # Napa
    "CAC069",  # San Benito
    "CAC075",  # San Francisco
    "CAC081",  # San Mateo
    "CAC085",  # Santa Clara
    "CAC087",  # Santa Cruz
    "CAC097",  # Sonoma
]


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


def fetch_alerts_by_zones(zones: list[str]) -> list[dict]:
    """
    Fetch active alerts for specific NWS zone codes (e.g., CAC085, CAZ513).
    zones is a list of zone strings joined by comma for the API.
    Returns list of alert features.
    """
    headers = {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}
    zone_param = ",".join(zones)
    params = {"zone": zone_param}
    try:
        resp = requests.get(NWS_ALERTS_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("features", [])
    except Exception as e:
        print(f"NWS alerts fetch by zones failed: {e}", file=sys.stderr)
        return []


def fetch_alerts(lat: float = None, lon: float = None, state: str = None, 
                 radius: float = None, zones: list[str] = None) -> list[dict]:
    """
    Fetch and deduplicate alerts from multiple NWS API queries.
    
    If zones is provided, use zone query only (lat/lon/state/radius ignored).
    Otherwise:
      - Point query (lat/lon) catches alerts directly affecting location
      - State query catches broader regional alerts
    
    Deduplicates by alert ID, then filters by radius if provided.
    """
    seen_ids = set()
    all_alerts = []
    
    # If zones specified, use only zone query
    if zones:
        print(f"Fetching NWS alerts for zones: {', '.join(zones)}...")
        alerts = fetch_alerts_by_zones(zones)
        print(f"  Zone alerts: {len(alerts)}")
        
        for alert in alerts:
            alert_id = alert.get("id")
            if alert_id not in seen_ids:
                seen_ids.add(alert_id)
                all_alerts.append(alert)
        
        return all_alerts
    
    # Otherwise use point + state queries
    print(f"Fetching NWS alerts for {state} (radius {radius:.0f}mi from {lat:.4f},{lon:.4f})...")
    
    if lat is not None and lon is not None:
        alerts = fetch_alerts_by_point(lat, lon)
        print(f"  Point alerts: {len(alerts)}")
        for alert in alerts:
            alert_id = alert.get("id")
            if alert_id not in seen_ids:
                seen_ids.add(alert_id)
                all_alerts.append(alert)
    
    if state:
        alerts = fetch_alerts_by_state(state)
        print(f"  State alerts: {len(alerts)}")
        for alert in alerts:
            alert_id = alert.get("id")
            if alert_id not in seen_ids:
                seen_ids.add(alert_id)
                all_alerts.append(alert)
    
    print(f"  Merged (deduped): {len(all_alerts)}")
    
    # Compute distances for radius filtering
    if radius is not None and lat is not None and lon is not None:
        filtered = []
        no_geom_count = 0
        point_ids = {f.get("id") for f in fetch_alerts_by_point(lat, lon)}
        
        for alert in all_alerts:
            centroid = alert_centroid(alert)
            if centroid:
                dist = haversine_distance(lat, lon, centroid[0], centroid[1])
                if dist <= radius:
                    alert["_distance_mi"] = dist
                    filtered.append(alert)
            else:
                # No geometry — include if from point query (directly relevant)
                alert_id = alert.get("id")
                if alert_id in point_ids:
                    alert["_distance_mi"] = 0.0
                    filtered.append(alert)
                else:
                    no_geom_count += 1
        
        if no_geom_count:
            print(f"  Skipped {no_geom_count} alerts without geometry (outside point match)")
        print(f"  Within radius: {len(filtered)}")
        all_alerts = filtered
    
    return all_alerts


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


# ── Message Formatting ──────────────────────────────────────────────────────

def chunk_message(msg: str, max_len: int = 160) -> list[str]:
    """
    Break a message into chunks if it exceeds max_len bytes.
    Adds "(1 of N)" / "(2 of N)" suffix to each chunk.
    Returns list of chunks.
    
    Useful for MeshCore's ~184 byte character limit.
    """
    if len(msg.encode("utf-8")) <= max_len:
        return [msg]
    
    # We need room for " (X of N)" suffix
    chunks = []
    words = msg.split()
    current = ""
    
    for word in words:
        test = (current + " " + word).strip()
        if len(test.encode("utf-8")) > max_len - 12:  # Reserve 12 bytes for suffix
            if current:
                chunks.append(current)
            current = word
        else:
            current = test
    
    if current:
        chunks.append(current)
    
    # Add numbering if multiple chunks
    if len(chunks) > 1:
        return [f"{c} ({i+1} of {len(chunks)})" for i, c in enumerate(chunks)]
    return chunks


def format_alert_message(feature: dict) -> str:
    """
    Format a single alert feature as a brief message.
    Uses only the headline (per Curt's feedback) to respect MeshCore 184B limit.
    """
    props = feature.get("properties", {})
    severity = (props.get("severity") or "?").upper()
    headline = props.get("headline", "Alert")
    areaDesc = props.get("areaDesc", "")

    # Build compact message
    icon = SEVERITY_ICON.get(severity.lower(), "⚪")
    msg = f"⚠️ SKYWARN\n{icon} {headline}"
    if areaDesc:
        msg += f"\n{areaDesc}"
    
    return msg


def format_no_alerts_message(place: str, radius: float) -> str:
    """Format an all-clear message."""
    return (
        f"⚠️ SKYWARN\n"
        f"✅ No active alerts\n"
        f"{place}\n"
        f"Radius: {radius:.0f}mi"
    )


def format_summary_message(location: str, alert_count: int, timestamp: str) -> str:
    """
    Format an opening summary message for hourly broadcasts.
    E.g., "As of 2:30 PM PST, there are 3 active alerts for the SF Bay Area. Alerts will follow."
    """
    if alert_count == 0:
        return (
            f"⚠️ SKYWARN SUMMARY\n"
            f"As of {timestamp}: No active alerts for {location}"
        )
    
    plural = "alert" if alert_count == 1 else "alerts"
    return (
        f"⚠️ SKYWARN SUMMARY\n"
        f"As of {timestamp}: {alert_count} active {plural} for {location}.\n"
        f"Details follow."
    )


# ── Broadcast ───────────────────────────────────────────────────────────────

async def broadcast(lat: float, lon: float, state: str, place: str, radius: float,
                    channel: str, min_severity: str | None, type_filter: list[str] | None,
                    skywarn_only: bool, send_clear: bool, limit: int, dry_run: bool,
                    delay: float, zones: list[str] = None, include_summary: bool = False):
    """
    Main broadcast logic: fetch, filter, format, and transmit alerts.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  MeshCore Skywarn Broadcast  (NWS API)")
    print(f"  {timestamp}")
    
    if zones:
        print(f"  Zones   : {', '.join(zones[:3])}{'...' if len(zones) > 3 else ''}")
    else:
        print(f"  Center  : {lat:.4f}, {lon:.4f} ({place or state})")
        print(f"  Radius  : {radius:.0f} mi")
    
    print(f"  Channel : {channel.upper()}")
    print(f"  Severity: {min_severity or 'all'}")
    print(f"  Skywarn : {'events only' if skywarn_only else 'all NWS alerts'}")
    print(f"  Dry run : {dry_run}")
    print(f"{'='*60}\n")

    # Fetch alerts
    raw_alerts = fetch_alerts(lat, lon, state, radius, zones)

    # Apply filters
    alerts = filter_alerts(raw_alerts, min_severity, type_filter, skywarn_only)
    print(f"After filters: {len(alerts)} alert(s)\n")

    messages = []
    
    # Add summary message if requested
    if include_summary:
        summary_time = datetime.now().strftime("%I:%M %p %Z")
        summary = format_summary_message(place or state, len(alerts), summary_time)
        messages.append(("Summary", summary))

    if not alerts:
        print(f"✅ No active alerts within {radius:.0f}mi of {place or 'center'}.")
        if send_clear and not include_summary:
            msg = format_no_alerts_message(place or state, radius)
            messages.append(("All Clear", msg))
        elif not send_clear and not include_summary:
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
        print(f"{len(alerts)} alert(s) to broadcast:\n")
        for i, feature in enumerate(alerts, 1):
            props = feature.get("properties", {})
            severity = props.get("severity", "?")
            event = props.get("event", "?")
            area = (props.get("areaDesc") or "")[:50]
            dist = feature.get("_distance_mi", 0)
            print(f"  [{i}] {severity}: {event} — {area} ({dist:.0f}mi)" if dist else f"  [{i}] {severity}: {event} — {area}")
            
            msg = format_alert_message(feature)
            # Chunk the message if needed
            chunks = chunk_message(msg)
            for chunk in chunks:
                messages.append((f"{event}", chunk))

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
        desired = CHANNELS[channel]
        idx = await resolve_channel_index(mc, desired)
        if idx is None:
            print(
                f"Could not find channel '{desired['name']}' on device.\n"
                f"Run: python meshcore_send.py --list-channels",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"Resolved '{channel}' → slot {idx}\n")

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
            "  --zone CODES            NWS zone codes (comma-separated, e.g., CAC085,CAZ513)\n"
            "                          Recommended for Bay Area / sfoskywarn\n"
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
            "  # Bay Area with zone codes (11-county MTR CWA)\n"
            "  python skywarn_broadcast.py --zone CAC001,CAC013,CAC041,CAC053,CAC055,CAC069,CAC075,CAC081,CAC085,CAC087,CAC097 --dry-run\n"
            "  \n"
            "  # Traditional radius-based (San Jose, 50 miles)\n"
            "  python skywarn_broadcast.py --zip 95125 --radius 50\n"
            "  \n"
            "  # Classic Skywarn only\n"
            "  python skywarn_broadcast.py --zone CAC085 --skywarn-only\n"
            "  \n"
            "  # With summary message (good for cron jobs)\n"
            "  python skywarn_broadcast.py --zone CAC085 --summary\n"
            "\n"
            "Data Source:\n"
            "  NWS API (api.weather.gov) — free, no API key required\n"
            f"\n  Available channels: {', '.join(CHANNELS.keys())}\n"
        )
    )

    # Location args
    loc = p.add_argument_group("location")
    loc.add_argument("--zone", dest="zones", metavar="CODES",
                     help="NWS zone codes (comma-separated, e.g., CAC085,CAZ513)")
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
    p.add_argument("--summary", action="store_true",
                   help="Include opening summary message (good for cron hourly jobs)")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch and preview messages without transmitting")

    args = p.parse_args()

    # Resolve location
    lat, lon, state, place = DEFAULT_LAT, DEFAULT_LON, DEFAULT_STATE, "San Jose, CA"
    zones = None

    # Zone-based query (highest priority)
    if args.zones:
        zones = [z.strip().upper() for z in args.zones.split(",") if z.strip()]
        place = "Bay Area (zone-based)"
        print(f"Using zones: {', '.join(zones)}")
    # Zip-based query
    elif args.zip:
        geo = zip_to_coords(args.zip)
        if geo is None:
            print(f"Could not resolve zip code '{args.zip}'.", file=sys.stderr)
            sys.exit(1)
        lat, lon, state = geo["lat"], geo["lon"], geo["state"]
        place = f"{geo['place']}, {state}"
        print(f"Zip {args.zip} → {place} ({lat:.4f}, {lon:.4f})")
    # Lat/lon query
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
    # State-only query
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
        zones=zones, include_summary=args.summary,
    ))


if __name__ == "__main__":
    main()