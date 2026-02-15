#!/usr/bin/env python3
"""
calendar_broadcast.py - Send calendar notifications for ham radio events to MeshCore.

Fetches events from a Google Sheet CSV and sends notifications:
  - 24 hours before the event
  - 2 hours before the event

Events are routed to appropriate channels based on the Channels column.
Tracks sent notifications to avoid duplicates.

Usage:
    python calendar_broadcast.py                    # Check and send due notifications
    python calendar_broadcast.py --dry-run          # Preview without sending
    python calendar_broadcast.py --preview          # Show all upcoming events
    python calendar_broadcast.py --reset-state      # Clear notification history

Typical deployment:
    Run via cron every 15-30 minutes to catch notification windows

Requires:
    pip install requests meshcore pandas
"""

import asyncio
import argparse
import sys
import os
import json
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
    import pandas as pd
except ImportError:
    print("Missing dependencies. Run: pip install requests pandas", file=sys.stderr)
    sys.exit(1)

# Import MeshCore connection utilities
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from meshcore_send import (
    CHANNELS, MAX_MSG_LEN, CONNECT_DELAY,
    connect, resolve_channel_index, _norm
)
from meshcore import EventType

# ── Configuration ──────────────────────────────────────────────────────────

DEFAULT_CALENDAR_KEYS = Path(__file__).parent / "calendar.keys"

# Notification windows (in hours before event)
NOTIFICATION_WINDOWS = [24, 2]  # 24 hours before, 2 hours before


def load_calendar_config(keys_path: Path = None) -> dict:
    """Load calendar configuration from calendar.keys."""
    if keys_path is None:
        keys_path = DEFAULT_CALENDAR_KEYS

    config = {"events_csv_url": ""}

    if not keys_path.exists():
        print(
            f"ERROR: Config file not found: {keys_path}\n"
            f"  Copy calendar.keys.example → calendar.keys and edit it.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(keys_path, "r") as f:
        for ln, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip().upper(), val.strip()

            if key == "EVENTS_CSV_URL":
                config["events_csv_url"] = val

    return config


_CALENDAR_CONFIG = load_calendar_config()
EVENTS_CSV_URL = _CALENDAR_CONFIG["events_csv_url"]

# State file to track sent notifications
STATE_FILE = Path.home() / ".meshcore_calendar_state.json"

# Format for event datetime in CSV
DATETIME_FORMAT = "%Y-%m-%d %H%M"

# ── State Management ───────────────────────────────────────────────────────

def load_state() -> dict:
    """Load notification state from disk."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load state file: {e}", file=sys.stderr)
    return {"sent_notifications": {}}


def save_state(state: dict):
    """Save notification state to disk."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not save state file: {e}", file=sys.stderr)


def notification_key(event_datetime: str, event_name: str, hours_before: int) -> str:
    """Generate unique key for a notification."""
    return f"{event_datetime}|{event_name}|{hours_before}h"


def is_notification_sent(state: dict, event_datetime: str, event_name: str, hours_before: int) -> bool:
    """Check if notification has already been sent."""
    key = notification_key(event_datetime, event_name, hours_before)
    return key in state.get("sent_notifications", {})


def mark_notification_sent(state: dict, event_datetime: str, event_name: str, hours_before: int):
    """Mark notification as sent."""
    key = notification_key(event_datetime, event_name, hours_before)
    if "sent_notifications" not in state:
        state["sent_notifications"] = {}
    state["sent_notifications"][key] = datetime.now().isoformat()


def cleanup_old_notifications(state: dict, days_to_keep: int = 7):
    """Remove notification records older than specified days."""
    cutoff = datetime.now() - timedelta(days=days_to_keep)
    sent = state.get("sent_notifications", {})
    
    keys_to_remove = []
    for key, timestamp_str in sent.items():
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
            if timestamp < cutoff:
                keys_to_remove.append(key)
        except:
            keys_to_remove.append(key)  # Remove invalid entries
    
    for key in keys_to_remove:
        del sent[key]
    
    if keys_to_remove:
        print(f"Cleaned up {len(keys_to_remove)} old notification records")


# ── Event Fetching ─────────────────────────────────────────────────────────

def fetch_events() -> pd.DataFrame:
    """Fetch events from Google Sheet CSV."""
    try:
        print(f"Fetching events from Google Sheet...")
        resp = requests.get(EVENTS_CSV_URL, timeout=15)
        resp.raise_for_status()
        
        # Read CSV into DataFrame
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        
        # Parse datetime
        df['event_dt'] = pd.to_datetime(df['EventDatetime'], format=DATETIME_FORMAT)
        
        print(f"  Loaded {len(df)} events")
        return df
        
    except Exception as e:
        print(f"Error fetching events: {e}", file=sys.stderr)
        sys.exit(1)


def parse_channels(channel_str: str) -> list:
    """Parse comma-separated channel list."""
    if pd.isna(channel_str):
        return ["public"]  # Default to public if no channels specified
    
    channels = [_norm(ch) for ch in str(channel_str).split(',')]
    # Filter to only known channels
    return [ch for ch in channels if ch in CHANNELS]


# ── Notification Logic ─────────────────────────────────────────────────────

def format_notification(event: dict, hours_before: int) -> str:
    """
    Format notification message for an event.
    Keep under MAX_MSG_LEN (135 chars).
    """
    event_time = event['event_dt'].strftime("%I:%M %p").lstrip('0')
    event_date = event['event_dt'].strftime("%a %b %d")
    
    if hours_before == 24:
        prefix = "TOMORROW"
    elif hours_before == 2:
        prefix = "IN 2 HOURS"
    else:
        prefix = f"IN {hours_before}H"
    
    # Build message
    msg = (
        f"EVENT {prefix}:\n"
        f"{event['EventName']}\n"
        f"{event_date} @ {event_time}\n"
        f"{event['Description']}"
    )
    
    # Truncate if too long
    if len(msg) > MAX_MSG_LEN:
        # Try shorter description
        max_desc = MAX_MSG_LEN - len(msg) + len(event['Description']) - 3
        msg = (
            f"EVENT {prefix}:\n"
            f"{event['EventName']}\n"
            f"{event_date} @ {event_time}\n"
            f"{event['Description'][:max_desc]}..."
        )
    
    return msg[:MAX_MSG_LEN]


def get_pending_notifications(df: pd.DataFrame, state: dict, now: datetime = None) -> list:
    """
    Find all notifications that should be sent now.
    
    Returns list of tuples: (event_dict, hours_before, channels_list)
    """
    if now is None:
        now = datetime.now()
    
    pending = []
    
    for _, row in df.iterrows():
        event_dt = row['event_dt']
        
        # Skip past events
        if event_dt < now:
            continue
        
        # Check each notification window
        for hours_before in NOTIFICATION_WINDOWS:
            notification_time = event_dt - timedelta(hours=hours_before)
            time_until_notification = (notification_time - now).total_seconds() / 60  # minutes
            
            # Notification window: -15 to +15 minutes from target time
            # This allows for cron jobs that run every 15-30 minutes
            if -15 <= time_until_notification <= 15:
                # Check if already sent
                event_dict = row.to_dict()
                if not is_notification_sent(state, row['EventDatetime'], row['EventName'], hours_before):
                    channels = parse_channels(row['Channels'])
                    if channels:  # Only add if there are valid channels
                        pending.append((event_dict, hours_before, channels))
    
    return pending


# ── Broadcasting ───────────────────────────────────────────────────────────

async def send_notification(mc, channel_key: str, channel_idx: int, message: str) -> bool:
    """Send a single notification message."""
    print(f"  [{channel_key.upper()}] -> {message!r}")
    result = await mc.commands.send_chan_msg(channel_idx, message)
    if result.type == EventType.ERROR:
        print(f"    x Error: {result.payload}", file=sys.stderr)
        return False
    print(f"    OK Sent")
    return True


async def broadcast_notifications(pending: list, dry_run: bool, state: dict):
    """
    Send all pending notifications.
    
    Args:
        pending: List of (event_dict, hours_before, channels_list)
        dry_run: If True, preview but don't send
        state: State dict to update with sent notifications
    """
    if not pending:
        print("\nNo notifications due at this time.")
        return
    
    print(f"\n{'='*56}")
    print(f"  {len(pending)} Notification(s) Ready to Send")
    print(f"  Dry run: {dry_run}")
    print(f"{'='*56}\n")
    
    # Group by event and hours_before to prepare messages
    messages_to_send = []
    for event, hours_before, channels in pending:
        msg = format_notification(event, hours_before)
        for channel in channels:
            messages_to_send.append({
                'event': event,
                'hours_before': hours_before,
                'channel': channel,
                'message': msg
            })
    
    print(f"Total messages to send: {len(messages_to_send)}\n")
    
    if dry_run:
        for item in messages_to_send:
            print(f"[{item['channel'].upper()}] {item['event']['EventName']} ({item['hours_before']}h before)")
            print(f"  {item['message']!r}\n")
        print("-- Dry run complete, nothing transmitted --")
        return
    
    # Connect to radio
    print("Connecting to radio...")
    mc = await connect()
    
    try:
        # Resolve channel indices once
        channel_indices = {}
        for channel_key in set(item['channel'] for item in messages_to_send):
            desired = CHANNELS[channel_key]
            idx = await resolve_channel_index(mc, desired)
            if idx is None:
                print(f"Warning: Could not find channel '{desired['name']}' on device", file=sys.stderr)
            else:
                channel_indices[channel_key] = idx
                print(f"  Resolved '{channel_key}' -> slot {idx}")
        
        print()
        
        # Send all messages
        for i, item in enumerate(messages_to_send):
            channel_key = item['channel']
            
            if channel_key not in channel_indices:
                print(f"Skipping message to {channel_key} (channel not found)")
                continue
            
            print(f"[{i+1}/{len(messages_to_send)}] {item['event']['EventName']} ({item['hours_before']}h)")
            
            success = await send_notification(
                mc,
                channel_key,
                channel_indices[channel_key],
                item['message']
            )
            
            if success:
                # Mark as sent in state
                mark_notification_sent(
                    state,
                    item['event']['EventDatetime'],
                    item['event']['EventName'],
                    item['hours_before']
                )
            
            # Small delay between messages to different channels
            if i < len(messages_to_send) - 1:
                await asyncio.sleep(3)
        
    finally:
        await mc.disconnect()
    
    # Save updated state
    save_state(state)
    
    print(f"\n{'='*56}")
    print(f"  Broadcast complete -- 73 de W6SAL")
    print(f"{'='*56}\n")


# ── Preview Mode ───────────────────────────────────────────────────────────

def preview_upcoming_events(df: pd.DataFrame, days: int = 7):
    """Show upcoming events for the next N days."""
    now = datetime.now()
    future_cutoff = now + timedelta(days=days)
    
    upcoming = df[(df['event_dt'] >= now) & (df['event_dt'] <= future_cutoff)].copy()
    upcoming = upcoming.sort_values('event_dt')
    
    print(f"\n{'='*56}")
    print(f"  Upcoming Events (Next {days} Days)")
    print(f"{'='*56}\n")
    
    if upcoming.empty:
        print("No events scheduled in this period.\n")
        return
    
    for _, event in upcoming.iterrows():
        event_time = event['event_dt'].strftime("%a %b %d @ %I:%M %p").lstrip('0')
        channels = ", ".join(parse_channels(event['Channels']))
        
        print(f"{event_time}")
        print(f"  {event['EventName']}")
        print(f"  {event['Description']}")
        print(f"  Channels: {channels}")
        
        # Show when notifications will be sent
        for hours_before in NOTIFICATION_WINDOWS:
            notif_time = event['event_dt'] - timedelta(hours=hours_before)
            if notif_time >= now:
                notif_str = notif_time.strftime("%a %b %d @ %I:%M %p")
                print(f"    >> {hours_before}h notification: {notif_str}")
        print()


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Broadcast calendar notifications for ham radio events to MeshCore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Notification windows: 24 hours before, 2 hours before\n"
            f"Available channels: {', '.join(CHANNELS.keys())}\n\n"
            "Typical deployment:\n"
            "  Add to crontab to run every 15-30 minutes:\n"
            "  */15 * * * * /usr/bin/python3 /path/to/calendar_broadcast.py\n"
        )
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview notifications without transmitting"
    )
    p.add_argument(
        "--preview",
        action="store_true",
        help="Show upcoming events for the next 7 days"
    )
    p.add_argument(
        "--preview-days",
        type=int,
        default=7,
        help="Number of days to preview (default: 7)"
    )
    p.add_argument(
        "--reset-state",
        action="store_true",
        help="Clear notification history (for testing)"
    )
    args = p.parse_args()
    
    # Handle state reset
    if args.reset_state:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            print(f"Cleared notification state: {STATE_FILE}")
        else:
            print("No state file to clear")
        return
    
    # Fetch events
    if not EVENTS_CSV_URL or "YOUR_SPREADSHEET_ID_HERE" in EVENTS_CSV_URL:
        print(
            "ERROR: EVENTS_CSV_URL not configured.\n"
            f"  Edit calendar.keys and set your Google Sheet published CSV URL.\n"
            "  See calendar.keys.example for instructions.",
            file=sys.stderr,
        )
        sys.exit(1)
    df = fetch_events()
    
    # Preview mode
    if args.preview:
        preview_upcoming_events(df, args.preview_days)
        return
    
    # Load state
    state = load_state()
    cleanup_old_notifications(state)
    
    # Check for pending notifications
    pending = get_pending_notifications(df, state)
    
    if not pending:
        print(f"\nChecked at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("No notifications due at this time.\n")
        return
    
    # Send notifications
    asyncio.run(broadcast_notifications(pending, args.dry_run, state))


if __name__ == "__main__":
    main()