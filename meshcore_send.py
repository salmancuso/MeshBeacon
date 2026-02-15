#!/usr/bin/env python3
"""
Send messages to MeshCore channels reliably by matching the on-device channel_secret.

All connection and channel configuration lives in 'meshcore.keys'
(see meshcore.keys.example).

Usage:
  python meshcore_send.py --channel myhams "Test message"
  python meshcore_send.py --list-channels
"""

import asyncio
import sys
import os
import argparse
from pathlib import Path
from meshcore import MeshCore, EventType

# ── Configuration Loading ─────────────────────────────────────────────────────

DEFAULT_KEYS_FILE = Path(__file__).parent / "meshcore.keys"

MAX_MSG_LEN = 135
CONNECT_DELAY = 2.0
MAX_CHANNEL_SLOTS_TO_SCAN = 16


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("-", "")


def load_meshcore_config(keys_path: Path = None) -> dict:
    """
    Load connection and channel configuration from meshcore.keys.

    Returns dict with keys:
        mode, ble_addr, ble_pin, serial_port, baud_rate, channels
    """
    if keys_path is None:
        keys_path = DEFAULT_KEYS_FILE

    config = {
        "mode": os.environ.get("MESHCORE_MODE", "ble"),
        "ble_addr": os.environ.get("MESHCORE_BLE_ADDR", ""),
        "ble_pin": os.environ.get("MESHCORE_PIN", ""),
        "serial_port": os.environ.get("MESHCORE_PORT", "/dev/ttyACM0"),
        "baud_rate": int(os.environ.get("MESHCORE_BAUD", 115200)),
        "channels": {},
    }

    if not keys_path.exists():
        print(
            f"WARNING: Config file not found: {keys_path}\n"
            f"  Copy meshcore.keys.example → meshcore.keys and edit it.",
            file=sys.stderr,
        )
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

            if key == "MODE":
                config["mode"] = val.lower()
            elif key == "BLE_ADDR":
                config["ble_addr"] = val
            elif key == "BLE_PIN":
                config["ble_pin"] = val
            elif key == "SERIAL_PORT":
                config["serial_port"] = val
            elif key == "BAUD_RATE":
                try:
                    config["baud_rate"] = int(val)
                except ValueError:
                    print(f"  Bad BAUD_RATE on line {ln}", file=sys.stderr)
            elif key == "CHANNEL":
                parts = [p.strip() for p in val.split("|")]
                if len(parts) >= 3:
                    ch_key = _norm(parts[0])
                    config["channels"][ch_key] = {
                        "name": parts[1],
                        "secret": parts[2].lower(),
                    }
                else:
                    print(f"  Bad CHANNEL on line {ln} (need: key | name | secret)", file=sys.stderr)

    return config


# ── Load config at import time so other scripts can use CHANNELS ──────────────

_CONFIG = load_meshcore_config()
CHANNELS = _CONFIG["channels"]


# ── Helpers ───────────────────────────────────────────────────────────────────


async def connect() -> MeshCore:
    mode = _CONFIG["mode"]
    ble_addr = _CONFIG["ble_addr"]
    ble_pin = _CONFIG["ble_pin"]
    serial_port = _CONFIG["serial_port"]
    baud_rate = _CONFIG["baud_rate"]

    if mode == "ble":
        if ble_addr:
            print(f"Connecting via BLE to {ble_addr}...")
            mc = (
                await MeshCore.create_ble(ble_addr, pin=str(ble_pin))
                if ble_pin
                else await MeshCore.create_ble(ble_addr)
            )
        else:
            print("Scanning for BLE device...")
            mc = (
                await MeshCore.create_ble(pin=str(ble_pin))
                if ble_pin
                else await MeshCore.create_ble()
            )
    else:
        print(f"Connecting via serial on {serial_port}...")
        mc = await MeshCore.create_serial(serial_port, baud_rate)

    await asyncio.sleep(CONNECT_DELAY)
    return mc


def payload_name(payload: dict) -> str:
    return payload.get("channel_name") or payload.get("name") or ""


def payload_secret_hex(payload: dict) -> str:
    sec = payload.get("channel_secret") or payload.get("secret")
    if isinstance(sec, bytes):
        return sec.hex()
    if isinstance(sec, str):
        return sec.strip().lower()
    return ""


async def resolve_channel_index(mc: MeshCore, desired: dict) -> int | None:
    want_secret = (desired.get("secret") or "").strip().lower()
    want_name = _norm(desired.get("name") or "")

    for idx in range(MAX_CHANNEL_SLOTS_TO_SCAN):
        r = await mc.commands.get_channel(idx)
        if r.type == EventType.ERROR:
            continue

        payload = r.payload or {}
        got_secret = payload_secret_hex(payload)
        got_name = _norm(payload_name(payload))

        if want_secret and got_secret == want_secret:
            return idx
        if want_name and got_name == want_name:
            return idx

    return None


async def list_channels():
    mc = await connect()
    try:
        print("Device channel slots:")
        for idx in range(MAX_CHANNEL_SLOTS_TO_SCAN):
            r = await mc.commands.get_channel(idx)
            if r.type == EventType.ERROR:
                continue
            payload = r.payload or {}
            print(
                f"  slot {payload.get('channel_idx', idx)}: "
                f"{payload_name(payload) or '(blank)'}  "
                f"secret={payload_secret_hex(payload) or '(none)'}"
            )
    finally:
        await mc.disconnect()


async def send_message(channel_key: str, message: str) -> bool:
    key = _norm(channel_key)
    if key not in CHANNELS:
        print(f"Unknown channel '{channel_key}'. Available: {', '.join(CHANNELS)}", file=sys.stderr)
        return False

    if len(message) > MAX_MSG_LEN:
        print(f"Message too long ({len(message)} > {MAX_MSG_LEN}), truncating.", file=sys.stderr)
        message = message[:MAX_MSG_LEN]

    mc = await connect()
    try:
        desired = CHANNELS[key]
        idx = await resolve_channel_index(mc, desired)
        if idx is None:
            print(
                f"Could not find channel '{desired['name']}' on the connected device.\n"
                f"Run: python meshcore_send.py --list-channels",
                file=sys.stderr,
            )
            return False

        print(f"[{key.upper()} slot={idx}] → {message!r}")
        res = await mc.commands.send_chan_msg(idx, message)
        if res.type == EventType.ERROR:
            print(f"Error: {res.payload}", file=sys.stderr)
            return False
        print("✓ Sent")
        return True
    finally:
        await mc.disconnect()


def main():
    p = argparse.ArgumentParser(description="Send a message to a MeshCore channel")
    p.add_argument("--channel", "-c", default=None, choices=list(CHANNELS.keys()) or None)
    p.add_argument("--list-channels", action="store_true")
    p.add_argument("message", nargs="*")
    args = p.parse_args()

    if args.list_channels:
        asyncio.run(list_channels())
        return

    if not args.message:
        p.error("message is required (or use --list-channels)")

    if args.channel is None:
        if CHANNELS:
            args.channel = list(CHANNELS.keys())[0]
        else:
            p.error("No channels configured. Edit meshcore.keys first.")

    asyncio.run(send_message(args.channel, " ".join(args.message)))


if __name__ == "__main__":
    main()