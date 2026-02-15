#!/usr/bin/env python3
"""
solar_broadcast.py - Solar + VHF/UHF propagation broadcast for MeshCore.

Data sources (all free, no API keys):
  HamQSL / N0NBH  : Solar indices, HF band conditions, Es/Aurora status
  Open-Meteo      : Pressure-level atmospheric data for SJC tropo index

Message layout:
  Always sent (2 msgs):
    [1] ☀️  Solar indices  — SFI SN A K X-ray solar-wind Bt timestamp ✅🟡❌
    [2] 📡  HF band conds  — 80/40 30/20 17/15 12/10 day/night ✅🟡❌
  With --vhf flag (2 more msgs):
    [3] 🔭  VHF status     — Es aurora meteor-shower
    [4] 🌊  Tropo index    — SJC-derived inversion index from Open-Meteo
  With --hfband flag (1 msg only):
    [1] 📡  HF band conds  — Individual bands with day conditions
  Conditional (fires automatically when warranted):
    [N] 🟡  NOAA alert     — G/R/S scales, elevated solar wind

All messages are byte-safe (≤135 UTF-8 bytes).
Location for tropo: San Jose CA  CM97bg  37.3382,-121.8863

Usage:
    python solar_broadcast.py                   # 2 msgs → hamradio
    python solar_broadcast.py --vhf             # 4 msgs (adds VHF + tropo)
    python solar_broadcast.py --hfband          # 1 msg (HF bands only)
    python solar_broadcast.py --channel public  # Different channel
    python solar_broadcast.py --dry-run         # Preview without transmitting
    python solar_broadcast.py --delay 10        # 10s between messages

Requires:
    pip install requests meshcore
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime, date, timedelta
import xml.etree.ElementTree as ET

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
HAMQSL_URL      = "https://www.hamqsl.com/solarxml.php"
OPEN_METEO_URL  = "https://api.open-meteo.com/v1/forecast"
DEFAULT_CHANNEL = "hamradio"
DEFAULT_DELAY   = 5.0
MAX_BYTES       = 135      # MeshCore hard limit in UTF-8 bytes

# Tropo location: San Jose CA, grid CM97bg
SJC_LAT =  37.3382
SJC_LON = -121.8863

# HF band pairs as reported in HamQSL calculatedconditions XML
HF_BANDS = [
    ("80m-40m",  "80/40"),
    ("30m-20m",  "30/20"),
    ("17m-15m",  "17/15"),
    ("12m-10m",  "12/10"),
]

# Condition word → colored circle emoji
COND_ICON = {
    "Excellent": "⭐",
    "Good":      "✅",
    "Fair":      "🟡",
    "Poor":      "❌",
}

# Major meteor shower peaks (name, month, day)
# Sources: AMS / IMO annual meteor calendar
METEOR_SHOWERS = [
    ("Quadrantids",  1,  4),
    ("Lyrids",       4, 23),
    ("Eta Aquarids", 5,  6),
    ("Perseids",     8, 12),
    ("Draconids",   10,  8),
    ("Orionids",    10, 22),
    ("Leonids",     11, 17),
    ("Geminids",    12, 14),
    ("Ursids",      12, 22),
]


# ── Byte-safe truncation ──────────────────────────────────────────────────────

def btrunc(s: str, max_bytes: int = MAX_BYTES) -> str:
    """Truncate string so its UTF-8 encoding is <= max_bytes."""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


# ── NOAA Scale Derivations ────────────────────────────────────────────────────

def k_to_g_scale(k: int) -> str:
    if k >= 9: return "G5"
    if k >= 8: return "G4"
    if k >= 7: return "G3"
    if k >= 6: return "G2"
    if k >= 5: return "G1"
    return ""

def xray_to_r_scale(xray: str) -> str:
    xray = (xray or "").strip().upper()
    if not xray or xray[0] not in "CMXBA":
        return ""
    cls = xray[0]
    try:
        val = float(xray[1:]) if len(xray) > 1 else 0.0
    except ValueError:
        val = 0.0
    if cls == "X":
        if val >= 20: return "R5"
        if val >= 10: return "R4"
        return "R3"
    if cls == "M":
        if val >= 5: return "R2"
        return "R1"
    return ""

def proton_to_s_scale(proton_str: str) -> str:
    try:
        pfu = float(proton_str)
    except (TypeError, ValueError):
        return ""
    if pfu >= 100_000: return "S5"
    if pfu >= 10_000:  return "S4"
    if pfu >= 1_000:   return "S3"
    if pfu >= 100:     return "S2"
    if pfu >= 10:      return "S1"
    return ""

def overall_geo_icon(k: int, xray: str) -> str:
    xray = (xray or "").upper()
    cls  = xray[0] if xray else ""
    try:
        val = float(xray[1:]) if len(xray) > 1 else 0.0
    except ValueError:
        val = 0.0
    if k >= 5 or cls == "X" or (cls == "M" and val >= 5):
        return "❌"
    if k >= 3 or cls == "M" or cls == "C":
        return "🟡"
    return "✅"


# ── Meteor Shower Calendar ────────────────────────────────────────────────────

def nearest_meteor_shower() -> tuple[str, int]:
    """
    Return (shower_name, delta_days) for the shower whose peak is nearest today.
    delta_days > 0 → upcoming, < 0 → recently past.
    Checks this year and next to handle year-boundary cases.
    """
    today = date.today()
    best_name  = "None"
    best_delta = 999

    for name, month, day in METEOR_SHOWERS:
        for year_offset in (0, 1, -1):
            try:
                peak  = date(today.year + year_offset, month, day)
                delta = (peak - today).days
                if abs(delta) < abs(best_delta):
                    best_delta = delta
                    best_name  = name
            except ValueError:
                pass

    return best_name, best_delta


def meteor_label(name: str, delta: int) -> str:
    """Format shower name + delta as a compact string."""
    if -2 <= delta <= 2:
        return f"{name}(Active!)"
    if 0 <= delta <= 60:
        return f"{name}+{delta}d"
    if -14 <= delta < 0:
        return f"{name}{delta}d"   # negative, e.g. Perseids-3d
    # Far out — just show name and days
    sign = "+" if delta > 0 else ""
    return f"{name}{sign}{delta}d"


# ── HamQSL Solar Data Fetch ───────────────────────────────────────────────────

def fetch_solar() -> dict | None:
    """
    Fetch solar propagation data from the HamQSL XML feed.

    HamQSL XML structure (ALL tags are lowercase):
      <solar>
        <solardata>
          <updated>07 Mar 2017 1231 GMT</updated>
          <solarflux>, <aindex>, <kindex>, <xray>, <sunspots>
          <solarwind>, <magneticfield>, <protonflux>
          <calculatedconditions>
            <band name="80m-40m" time="day">Good</band> ...
          </calculatedconditions>
          <calculatedvhfconditions>
            <phenomenon name="Aurora" location="1">No Aurora</phenomenon>
            <phenomenon name="E-Skip" location="1">Band Closed</phenomenon> ...
          </calculatedvhfconditions>
        </solardata>
      </solar>
    """
    try:
        resp = requests.get(HAMQSL_URL, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        sd = root.find("solardata")
        if sd is None:
            for child in root:
                if child.tag.lower() == "solardata":
                    sd = child
                    break
        if sd is None:
            print(
                f"x Could not find <solardata> in XML.\n"
                f"  Root tag: '{root.tag}', children: {[c.tag for c in root]}",
                file=sys.stderr,
            )
            return None

        def txt(tag: str, default: str = "?") -> str:
            el = sd.find(tag)
            return el.text.strip() if el is not None and el.text else default

        hf_bands: dict[tuple, str] = {}
        cc = sd.find("calculatedconditions")
        if cc is not None:
            for band_el in cc.findall("band"):
                hf_bands[(band_el.get("name", ""), band_el.get("time", ""))] = \
                    (band_el.text or "").strip()

        vhf: dict[tuple, str] = {}
        vhfc = sd.find("calculatedvhfconditions")
        if vhfc is not None:
            for ph in vhfc.findall("phenomenon"):
                vhf[(ph.get("name", ""), ph.get("location", ""))] = \
                    (ph.text or "").strip()

        return {
            "updated":    txt("updated"),
            "sfi":        txt("solarflux"),
            "sn":         txt("sunspots"),
            "aindex":     txt("aindex"),
            "kindex":     txt("kindex"),
            "xray":       txt("xray"),
            "solarwind":  txt("solarwind"),
            "magfield":   txt("magneticfield"),
            "protonflux": txt("protonflux"),
            "aurora":     txt("aurora"),
            "hf_bands":   hf_bands,
            "vhf":        vhf,
        }

    except ET.ParseError as e:
        print(f"x XML parse error: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"x Solar fetch failed: {e}", file=sys.stderr)
        return None


# ── Open-Meteo Tropospheric Data Fetch ───────────────────────────────────────

def fetch_tropo() -> dict | None:
    """
    Fetch atmospheric pressure-level data from Open-Meteo for San Jose.

    Uses hourly pressure-level fields to compute temperature inversion strength,
    which is the primary driver of VHF/UHF tropospheric ducting.

    Key physics:
      Standard atmosphere cools ~14°F from surface to 925hPa (~2500ft)
                            and ~27°F from surface to 850hPa (~5000ft).
      When measured temps are WARMER than expected aloft, an inversion exists.
      Strong surface pressure (>1018mb) + inversion = ducting conditions.
      Bay Area marine layer makes this one of the best tropo locations in CONUS.
    """
    params = {
        "latitude":        SJC_LAT,
        "longitude":       SJC_LON,
        "current":         ["temperature_2m", "relative_humidity_2m", "surface_pressure"],
        "hourly":          ["temperature_925hPa", "temperature_850hPa",
                            "relative_humidity_925hPa"],
        "temperature_unit": "fahrenheit",
        "timezone":        "America/Los_Angeles",
        "forecast_days":   1,
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        resp.raise_for_status()
        j = resp.json()

        # Find the index for the current hour in the hourly time array
        now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
        times = j["hourly"]["time"]
        try:
            hi = times.index(now_str)
        except ValueError:
            hi = 0   # fallback: use first hour of forecast

        return {
            "t2m":   round(j["current"]["temperature_2m"]),
            "rh2m":  round(j["current"]["relative_humidity_2m"]),
            "pres":  round(j["current"]["surface_pressure"]),
            "t925":  round(j["hourly"]["temperature_925hPa"][hi]),
            "t850":  round(j["hourly"]["temperature_850hPa"][hi]),
            "rh925": round(j["hourly"]["relative_humidity_925hPa"][hi]),
        }

    except Exception as e:
        print(f"  x Tropo fetch failed (non-fatal): {e}", file=sys.stderr)
        return None


def compute_tropo_index(td: dict) -> tuple[int, str]:
    """
    Compute a 0-10 Hepburn-style tropo index from Open-Meteo pressure data.

    Returns (index, label) where label is a short descriptor.

    Scoring:
      Base score from inversion strength at 925hPa:
        Normal lapse surface→925hPa ≈ 14°F; deviation above normal = inversion
        inv_925 = 14 - (T_surface - T_925)
        0-4°F dev  → score 2    (marginal inversion)
        5-9°F dev  → score 4    (moderate inversion)
        10-17°F dev → score 6   (strong inversion)
        18°F+ dev  → score 8    (exceptional inversion)
      +1 if 850hPa corroborates (inv_850 = 27-(T2m-T850) > 10°F)
      +1 if surface pressure ≥ 1022 mb (strong high pressure system)
      Score 0 if no inversion detected at 925hPa.
    """
    normal_925 = 14   # expected °F drop from surface to 925hPa
    normal_850 = 27   # expected °F drop from surface to 850hPa

    inv_925 = normal_925 - (td["t2m"] - td["t925"])  # positive = inversion
    inv_850 = normal_850 - (td["t2m"] - td["t850"])

    if inv_925 <= 0:
        score = 0
    elif inv_925 < 5:
        score = 2
    elif inv_925 < 10:
        score = 4
    elif inv_925 < 18:
        score = 6
    else:
        score = 8

    if inv_850 > 10:
        score = min(10, score + 1)

    if td["pres"] >= 1022:
        score = min(10, score + 1)
    elif td["pres"] >= 1018 and score == 0:
        score = 1   # high pressure alone — marginal potential

    labels = {
        0: "None", 1: "Marginal", 2: "Marginal",
        3: "Possible", 4: "Possible",
        5: "Likely", 6: "Likely",
        7: "Strong", 8: "Strong",
        9: "Exceptional", 10: "Exceptional",
    }
    return score, labels.get(score, "?")


def tropo_icon(index: int) -> str:
    """Operator-perspective icon: higher index = better DX = greener."""
    if index >= 6: return "✅"
    if index >= 3: return "🟡"
    return "❌"


# ── Short Timestamp ───────────────────────────────────────────────────────────

def _short_ts(raw: str) -> str:
    """'10 Feb 2026 1800 GMT' -> '10 Feb 1800z'"""
    try:
        parts = raw.replace(" GMT", "").split()
        return f"{parts[0]} {parts[1]} {parts[3]}z"
    except Exception:
        return raw[:15]


# ── HamQSL VHF Helpers ────────────────────────────────────────────────────────

def _vhf_aurora(solar: dict) -> str:
    """Extract aurora status string from HamQSL VHF phenomena."""
    vhf = solar.get("vhf", {})
    for loc in ("1", "2", "northern_hemi", ""):
        v = vhf.get(("Aurora", loc))
        if v:
            return v
    # Fallback to top-level aurora field
    a = solar.get("aurora", "")
    return a if a and a != "?" else "No"


def _vhf_eskip(solar: dict) -> str:
    """Extract E-skip status string from HamQSL VHF phenomena."""
    vhf = solar.get("vhf", {})
    for ename in ("E-Skip", "E-skip", "Es"):
        for loc in ("1", "2", "3", "us_hemi", "eu_hemi", ""):
            v = vhf.get((ename, loc))
            if v:
                return v
    return "?"


# ── Message Builders ──────────────────────────────────────────────────────────

def _band_icon(solar: dict, band: str, time: str) -> str:
    raw = solar["hf_bands"].get((band, time), "")
    return COND_ICON.get(raw, "?")


def build_messages(solar: dict, tropo: dict | None,
                   include_vhf: bool, hfband_only: bool = False) -> list[tuple[str, str]]:
    """
    Returns list of (label, message) tuples, each guaranteed ≤ MAX_BYTES.

    Always:   [0] Solar indices   [1] HF band conditions
    --vhf:    [2] VHF status      [3] Tropo index (if data available)
    --hfband: [0] HF band conditions only
    Auto:     [N] NOAA alert      (when K≥4, M/X flare, or S-event)
    """
    msgs: list[tuple[str, str]] = []

    # ── HF Band Only Mode ──────────────────────────────────────────────────────
    if hfband_only:
        band_lines = []
        for xml_name, label in HF_BANDS:
            day_icon = _band_icon(solar, xml_name, 'day')
            bands = label.split('/')
            for band in bands:
                band_lines.append(f"{band} = {day_icon}")
        
        m2 = "📡 HF BANDS:\n" + "\n".join(band_lines)
        msgs.append(("HF Band Conditions", btrunc(m2)))
        return msgs

    # ── Standard Mode ──────────────────────────────────────────────────────────
    try:
        k = int(solar["kindex"])
    except (ValueError, TypeError):
        k = 0

    # ── Msg 1: Solar Indices ──────────────────────────────────────────────────
    ts   = _short_ts(solar["updated"])
    icon = overall_geo_icon(k, solar["xray"])
    m1_full = (
        f"☀️ SOLAR:\n"
        f"SFI={solar['sfi']}\n"
        f"SN={solar['sn']}\n"
        f"A={solar['aindex']}\n"
        f"K={solar['kindex']}\n"
        f"Xray={solar['xray']}\n"
        f"Wind={solar['solarwind']}km/s\n"
        f"Bt={solar['magfield']}nT\n"
        f"[{ts}] {icon}"
    )
    m1_short = (
        f"☀️ SOLAR:\n"
        f"SFI={solar['sfi']}\n"
        f"SN={solar['sn']}\n"
        f"A={solar['aindex']}\n"
        f"K={solar['kindex']}\n"
        f"Xray={solar['xray']}\n"
        f"[{ts}] {icon}"
    )
    m1 = m1_full if len(m1_full.encode("utf-8")) <= MAX_BYTES else m1_short
    msgs.append(("Solar Indices", btrunc(m1)))

    # ── Msg 2: HF Band Conditions ─────────────────────────────────────────────
    band_lines = []
    for xml_name, label in HF_BANDS:
        # Get the day condition icon for this band pair
        day_icon = _band_icon(solar, xml_name, 'day')
        
        # Split the pair (e.g., "80/40" -> ["80", "40"])
        bands = label.split('/')
        
        # Add a line for each individual band with the day condition
        for band in bands:
            band_lines.append(f"{band} = {day_icon}")

    m2 = "📡 BANDS D/N:\n" + "\n".join(band_lines)
    msgs.append(("HF Band Conditions", btrunc(m2)))

    if include_vhf:
        # ── Msg 3: VHF Status (Es + Aurora + Meteor Shower) ──────────────────
        aurora = _vhf_aurora(solar)
        es     = _vhf_eskip(solar)
        shower_name, shower_delta = nearest_meteor_shower()
        meteor = meteor_label(shower_name, shower_delta)

        # Derive overall VHF icon
        aurora_active = aurora.lower() not in ("no aurora", "no", "inactive", "none", "?")
        es_open       = "open" in es.lower() or "active" in es.lower()
        meteor_active = abs(shower_delta) <= 2

        if aurora_active or es_open or meteor_active:
            vhf_icon = "✅"   # Something is happening — great for DX
        else:
            vhf_icon = "❌"   # Nothing open

        m3 = (
            f"🔭 VHF:\n"
            f"Es={es}\n"
            f"Aurora={aurora}\n"
            f"Meteor={meteor} {vhf_icon}"
        )
        msgs.append(("VHF Status", btrunc(m3)))

        # ── Msg 4: Tropospheric Ducting Index (SJC) ───────────────────────────
        if tropo is not None:
            idx, label = compute_tropo_index(tropo)
            t_icon     = tropo_icon(idx)
            inv_dt     = tropo["t925"] - (tropo["t2m"] - 14)
            sign       = "+" if inv_dt >= 0 else ""
            m4 = (
                f"🌊 TROPO SJC:\n"
                f"Idx={idx}/10\n"
                f"dT={sign}{round(inv_dt)}F@925mb\n"
                f"Pres={tropo['pres']}mb\n"
                f"{t_icon} {label}"
            )
            msgs.append(("Tropo Index SJC", btrunc(m4)))

    # ── Conditional: NOAA Alert ───────────────────────────────────────────────
    g_scale = k_to_g_scale(k)
    r_scale = xray_to_r_scale(solar["xray"])
    s_scale = proton_to_s_scale(solar["protonflux"])

    if (k >= 4) or bool(r_scale) or bool(s_scale):
        alert_parts: list[str] = []

        if g_scale:
            alert_parts.append(f"Geomag={g_scale}(K={k}+)")
        elif k == 4:
            alert_parts.append(f"Geomag=Active(K=4)")

        if r_scale:
            alert_parts.append(f"Flare={solar['xray']}({r_scale})")
        elif solar["xray"] not in ("?", ""):
            xc = (solar["xray"] or "").upper()
            if xc and xc[0] in "MXC":
                alert_parts.append(f"Flare={solar['xray']}")

        if s_scale:
            alert_parts.append(f"Proton={s_scale}")

        try:
            wv = float(solar["solarwind"])
            if wv >= 500:
                alert_parts.append(f"Wind={round(wv)}km/s")
        except (ValueError, TypeError):
            pass

        if alert_parts:
            msgs.append(("NOAA Alert", btrunc("💥🔆💫 ALERT: " + " ".join(alert_parts))))

    return msgs


# ── Broadcast ─────────────────────────────────────────────────────────────────

async def broadcast(channel_key: str, include_vhf: bool, hfband_only: bool, 
                    dry_run: bool, delay: float):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  MeshCore Propagation Broadcast  --  {timestamp}")
    print(f"  Channel : {channel_key.upper()}")
    
    if hfband_only:
        print(f"  Mode    : HF BANDS ONLY (--hfband)")
    else:
        print(f"  VHF/Tropo msgs : {'yes (--vhf)' if include_vhf else 'no (add --vhf to enable)'}")
    
    print(f"  Delay   : {delay:.0f}s between messages")
    print(f"  Dry run : {dry_run}")
    print(f"{'='*60}\n")

    # ── Fetch all data before connecting to radio ─────────────────────────────
    print("Fetching solar data from hamqsl.com (N0NBH)...")
    solar = fetch_solar()
    if solar is None:
        print("Failed to fetch solar data. Aborting.", file=sys.stderr)
        sys.exit(1)

    tropo = None
    if include_vhf and not hfband_only:
        print("Fetching tropo data from open-meteo.com (SJC)...")
        tropo = fetch_tropo()   # non-fatal if this fails

    messages = build_messages(solar, tropo, include_vhf, hfband_only)

    # ── Console Summary ───────────────────────────────────────────────────────
    if not hfband_only:
        try:
            k_int = int(solar["kindex"])
        except (ValueError, TypeError):
            k_int = 0

        print(f"\nSolar data snapshot:")
        print(f"  Updated    : {solar['updated']}")
        print(f"  SFI        : {solar['sfi']}")
        print(f"  Sunspots   : {solar['sn']}")
        print(f"  A-index    : {solar['aindex']}")
        g = k_to_g_scale(k_int)
        print(f"  K-index    : {solar['kindex']}" + (f"  -> NOAA {g}" if g else ""))
        r = xray_to_r_scale(solar["xray"])
        print(f"  X-ray      : {solar['xray']}" + (f"  -> NOAA {r}" if r else ""))
        print(f"  Solar Wind : {solar['solarwind']} km/s")
        print(f"  Mag Field  : {solar['magfield']} nT")
        if solar["protonflux"] != "?":
            s = proton_to_s_scale(solar["protonflux"])
            print(f"  Proton flux: {solar['protonflux']} pfu" + (f"  -> NOAA {s}" if s else ""))
        print(f"  HF bands   : {len(solar['hf_bands'])} conditions parsed")
        if solar["vhf"]:
            print(f"  VHF phenom : {len(solar['vhf'])} phenomena parsed")

        if tropo is not None:
            idx, label = compute_tropo_index(tropo)
            print(f"\nTropo data snapshot (SJC / CM97bg):")
            print(f"  Surface    : {tropo['t2m']}°F  RH={tropo['rh2m']}%  Pres={tropo['pres']}mb")
            print(f"  T @ 925hPa : {tropo['t925']}°F  RH={tropo['rh925']}%  (~2500ft)")
            print(f"  T @ 850hPa : {tropo['t850']}°F  (~5000ft)")
            inv = tropo["t925"] - (tropo["t2m"] - 14)
            sign = "+" if inv >= 0 else ""
            print(f"  Inversion  : {sign}{round(inv)}°F deviation from standard lapse rate")
            print(f"  Tropo index: {idx}/10  ({label})")

        sn, sd = nearest_meteor_shower()
        print(f"\nNearest meteor shower: {sn} ({'+' if sd>=0 else ''}{sd}d from peak)")

    print(f"\nMessages to transmit ({len(messages)} total):")
    for i, (label, msg) in enumerate(messages, 1):
        byte_len = len(msg.encode("utf-8"))
        print(f"  [{i}] {label}")
        print(f"       [{byte_len:3d}B] {msg}")

    print()
    if dry_run:
        print("-- Dry run complete, nothing transmitted --")
        return

    # ── Connect and transmit ──────────────────────────────────────────────────
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
        description="Broadcast solar + VHF/UHF propagation data to MeshCore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Data sources:\n"
            "  HamQSL/N0NBH : https://www.hamqsl.com/solarxml.php  (3hr updates)\n"
            "  Open-Meteo   : https://api.open-meteo.com            (1hr updates)\n"
            "\n"
            "Sample output (quiet day, no --vhf):\n"
            "  ☀️ SOLAR: SFI=185 SN=85 A=5 K=2 Xray=A1.2 Wind=425km/s Bt=-3nT [10 Feb 1800z] ✅\n"
            "  📡 BANDS D/N: 80/40=✅/✅ 30/20=✅/🟡 17/15=🟡/❌ 12/10=❌/❌\n"
            "\n"
            "Sample output with --vhf:\n"
            "  🔭 VHF: Es=Band Closed Aurora=No Aurora Meteor=Perseids+14d ❌\n"
            "  🌊 TROPO SJC: Idx=6/10 dT=+12F@925mb Pres=1022mb ✅ Likely\n"
            "\n"
            "Sample output with --hfband:\n"
            "  📡 BANDS D/N:\n"
            "  80 = ✅\n"
            "  40 = ✅\n"
            "  30 = ✅\n"
            "  20 = 🟡\n"
            "  17 = 🟡\n"
            "  15 = ❌\n"
            "  12 = ❌\n"
            "  10 = ❌\n"
            "\n"
            "Sample NOAA alert (fires automatically when warranted):\n"
            "  🟡 ALERT: Geomag=G2(K=6+) Flare=M5.1(R2) Wind=650km/s\n"
            f"\nAvailable channels: {', '.join(CHANNELS.keys())}\n"
        )
    )
    p.add_argument(
        "--channel", "-c",
        default=DEFAULT_CHANNEL,
        choices=list(CHANNELS.keys()),
        help=f"Target channel (default: {DEFAULT_CHANNEL})"
    )
    p.add_argument(
        "--vhf",
        action="store_true",
        help="Add VHF status + SJC tropo index messages (2 additional msgs)"
    )
    p.add_argument(
        "--hfband",
        action="store_true",
        help="Send only HF band conditions (single message)"
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
    
    # Validate conflicting flags
    if args.vhf and args.hfband:
        p.error("--vhf and --hfband cannot be used together")
    
    asyncio.run(broadcast(args.channel, args.vhf, args.hfband, args.dry_run, args.delay))


if __name__ == "__main__":
    main()