#!/usr/bin/env python3
"""
Minimal reference listener: Home Assistant WebSocket -> debounce -> handler.

This is a *slimmed* illustration of the integration seam. In the system this was
extracted from, one shared listener fans each message out to several independent
handlers (conversational replies, this task extractor, a calendar/meeting
extractor). That "one front door, N services" shape is the point:

  * one WebSocket connection, one reconnect loop, one debounce implementation —
    not re-implemented per service;
  * handlers are independent modules exposing `handle_message(text, sender)`;
  * a new service is a new module + one line here, and the others don't change;
  * each handler call is isolated, so one crashing service can't deafen the rest.

The message source here is Home Assistant's `whatsapp_message_received` event,
emitted by a WhatsApp bridge integration — but nothing below is WhatsApp
specific. Point it at any event that carries a sender and text.

Run it as a long-lived daemon (see deploy/ for a launchd example).
"""

import asyncio
import json
import os
import signal
import sys

import websockets

sys.path.insert(0, os.path.dirname(__file__))
from task_extract import handle_message as handle_task_message

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

WS_URL = HA_URL.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
RECONNECT_DELAY = 10

# Debounce: collect a burst of messages from the same sender into one unit
# before handing off, so "get milk" / "and bread" / "oh and stamps" are
# classified together rather than as three fragments.
DEBOUNCE_SECONDS = 8
_pending: dict[str, list[str]] = {}
_debounce_tasks: dict[str, asyncio.Task] = {}


async def _flush_debounced(sender_number: str):
    await asyncio.sleep(DEBOUNCE_SECONDS)
    messages = _pending.pop(sender_number, [])
    _debounce_tasks.pop(sender_number, None)
    if not messages:
        return

    combined = "\n".join(messages)
    print(f"[debounce] {len(messages)} msg(s) from {sender_number}: {combined[:80]!r}", flush=True)

    # The handler chain. Each handler is isolated: an exception in one must not
    # stop the others. Add more handlers here — order matters only if a handler
    # is meant to consume-and-stop (this one never does).
    for name, handler in (("task", handle_task_message),):
        try:
            handler(combined, sender_number)
        except Exception as e:  # noqa: BLE001 — isolation is the whole point
            print(f"[handler:{name}] error: {e}", flush=True)


def _debounce(sender_number: str, content: str):
    _pending.setdefault(sender_number, []).append(content)
    existing = _debounce_tasks.get(sender_number)
    if existing and not existing.done():
        existing.cancel()
    loop = asyncio.get_event_loop()
    _debounce_tasks[sender_number] = loop.create_task(_flush_debounced(sender_number))


def _handle_event(event):
    """Pull (sender_number, text) out of a whatsapp_message_received event.

    Ignores group chats and the account's own outbound messages. Field names
    follow the bridge integration's event schema; adapt to your source.
    """
    data = event.get("data", {})
    if data.get("type", "") not in ("chat", "message"):
        return
    raw = data.get("raw", {})
    from_me = raw.get("key", {}).get("fromMe", False) if isinstance(raw, dict) else False
    if from_me:
        return
    sender_jid = data.get("sender", "")
    if data.get("is_group", False) or "@g.us" in sender_jid:
        return
    content = data.get("content", "")
    if not content:
        return
    sender_number = data.get("sender_number", "") or sender_jid.split("@")[0]
    if not sender_number:
        return
    _debounce(sender_number, content)


async def listen():
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                if json.loads(await ws.recv()).get("type") != "auth_required":
                    continue
                await ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
                if json.loads(await ws.recv()).get("type") != "auth_ok":
                    print("Auth failed", flush=True)
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue
                print("Connected to Home Assistant WebSocket.", flush=True)

                await ws.send(json.dumps({
                    "id": 1,
                    "type": "subscribe_events",
                    "event_type": "whatsapp_message_received",
                }))
                if not json.loads(await ws.recv()).get("success"):
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue
                print("Subscribed to whatsapp_message_received.", flush=True)

                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "event":
                        _handle_event(msg["event"])

        except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
            print(f"Connection lost ({e}); reconnecting in {RECONNECT_DELAY}s", flush=True)
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:  # noqa: BLE001
            print(f"Unexpected error: {e}; reconnecting in {RECONNECT_DELAY}s", flush=True)
            await asyncio.sleep(RECONNECT_DELAY)


def main():
    if not HA_TOKEN:
        print("Error: HA_TOKEN not set.", file=sys.stderr)
        sys.exit(1)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown(sig, _frame):
        print(f"\nShutting down (signal {sig})...", flush=True)
        loop.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"Listener starting -> {WS_URL}", flush=True)
    loop.run_until_complete(listen())


if __name__ == "__main__":
    main()
