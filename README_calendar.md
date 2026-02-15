# Calendar Broadcast — `calendar_broadcast.py`

**Ham radio event notification broadcasting for MeshCore mesh networks**

Reads upcoming events from a Google Sheets CSV and sends timed notifications to MeshCore channels. Supports dual notification windows (24 hours and 2 hours before events) with duplicate prevention.

---

## Data Source

**Google Sheets (Published as CSV)**
- User-managed spreadsheet
- Free, no API key required
- Update frequency: Whenever you edit the sheet
- Publicly accessible via published link

---

## Setup

1. Create a Google Sheet with these columns:
   - `EventDatetime` — format: `YYYY-MM-DD HHMM` (e.g., `2026-02-15 1900`)
   - `EventName` — e.g., `WVARA Net`
   - `Description` — e.g., `Weekly 2m net on 146.76 MHz`
   - `Channels` — comma-separated channel keys (e.g., `meshhams,wvara`)

2. Publish the sheet as CSV:
   - File → Share → Publish to web → CSV format
   - Copy the published URL
   - Update `EVENTS_CSV_URL` in `calendar_broadcast.py`

---

## Usage

```bash
# Check and send due notifications
python calendar_broadcast.py

# Preview upcoming events (next 7 days)
python calendar_broadcast.py --preview

# Preview next 30 days
python calendar_broadcast.py --preview --preview-days 30

# Dry run (show what would be sent)
python calendar_broadcast.py --dry-run

# Reset notification state (for testing)
python calendar_broadcast.py --reset-state
```

---

## Features

- **Dual Notification Windows**: Alerts 24 hours and 2 hours before events
- **Multi-Channel Routing**: Events can specify target channels in CSV
- **Duplicate Prevention**: Tracks sent notifications to avoid re-sending
- **State Management**: History in `~/.meshcore_calendar_state.json`, auto-cleanup after 7 days

---

## Message Format

**24 Hours Before**
```
EVENT TOMORROW:
WVARA Net
Sat Feb 14 @ 7:00 PM
Weekly 2m net on 146.76 MHz
```

**2 Hours Before**
```
EVENT SOON:
WVARA Net
Sat Feb 14 @ 7:00 PM
Weekly 2m net on 146.76 MHz
```

---

## Automated Scheduling

```bash
# Every 15 minutes to catch notification windows
*/15 * * * * /usr/bin/python3 /home/sal/meshcore/calendar_broadcast.py >> ~/meshcore/logs/calendar.log 2>&1
```

Run every 15 minutes to reliably catch both the 24-hour and 2-hour notification windows. The state management prevents duplicate sends.

---

## Dependencies

```
requests>=2.31.0
meshcore>=0.1.0
pandas>=2.0.0
```

No API keys required.
