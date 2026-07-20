#!/usr/bin/env python3
"""Reminder loop for the task to-do list(s).

Runs on a scheduler (e.g. launchd / cron / systemd timer) every ~30 min. The
script self-gates to waking hours, so the scheduler can fire unconditionally.

State model
-----------
The to-do list in Home Assistant is the single source of truth for *what* is
outstanding. This daemon owns only *timing* metadata (a small sidecar JSON of
created_at / last_pinged per item UID). Checking an item off the list is the
only "dismiss" — the next cycle garbage-collects its timing row. Any component
can crash and restart without losing task data.

Per open item:
    age < grace window:         skip (just added; give it a chance)
    grace <= age < escalation:  ping every PING_INTERVAL since last ping (normal tone)
    age >= escalation:          same cadence, "overdue" tone

One consolidated notification per cycle ("Partner asked (N)"), oldest first,
top three listed — never one ping per task.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .task_extract import _load_trusted_senders, _get_open_todos

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
NOTIFY_SERVICE = os.environ.get("NOTIFY_SERVICE", "notify.mobile_app_your_phone")

STATE_PATH = Path(os.path.expanduser(
    os.environ.get("TASK_STATE_PATH", "~/task_pipeline_state.json")
))
LOG_PATH = os.path.expanduser(os.environ.get("TASK_LOG_PATH", "~/task_pipeline.log"))

# Deep-link path for the reminder body tap (a Lovelace dashboard showing the
# to-do list). Android Companion app resolves a relative path against the HA URL.
REMINDER_CLICK_PATH = os.environ.get("REMINDER_CLICK_PATH", "/tasks")

GRACE_SECONDS = 60 * 60
PING_INTERVAL_SECONDS = 2 * 60 * 60
ESCALATION_SECONDS = 24 * 60 * 60

# Waking hours (inclusive of start hour, exclusive above end hour).
QUIET_START_HOUR = 7    # first waking hour
QUIET_END_HOUR = 23     # last waking hour


def _log(line):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except Exception:
        pass


def _in_quiet_hours():
    h = datetime.now().hour
    return h < QUIET_START_HOUR or h > QUIET_END_HOUR


def _load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        _log("[state] corrupted; resetting")
        return {}


def _save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)  # atomic; a mid-write crash can't corrupt the file


def _utcnow_ts():
    return datetime.now(timezone.utc).timestamp()


def _fmt_age(seconds):
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _send_reminder(entity_id, sender_name, items_to_ping, escalating):
    if not HA_TOKEN:
        _log("[notify] HA_TOKEN missing")
        return False
    n = len(items_to_ping)
    suffix = " (overdue)" if escalating else ""
    title = f"{sender_name} asked ({n}){suffix}"
    top = items_to_ping[:3]
    lines = []
    for it in top:
        summary = (it.get("summary") or "").strip()
        age_str = _fmt_age(it["_age_seconds"])
        lines.append(f"• {summary} ({age_str})")
    if n > 3:
        lines.append(f"…and {n - 3} more")
    body = "\n".join(lines)
    payload = {
        "title": title,
        "message": body,
        "data": {
            "tag": f"task_reminder_{entity_id}",
            "clickAction": REMINDER_CLICK_PATH,
            "channel": "Tasks",
        },
    }
    try:
        r = requests.post(
            f"{HA_URL}/api/services/{NOTIFY_SERVICE.replace('.', '/')}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        _log(f"[notify] {entity_id} n={n} escalating={escalating}")
        return True
    except requests.RequestException as e:
        _log(f"[notify] failed: {e}")
        return False


def _cycle_for_entity(entity_id, sender_name, state, force=False):
    items = _get_open_todos(entity_id)
    open_uids = set()

    now_ts = _utcnow_ts()
    items_to_ping = []
    escalating = False

    for item in items:
        uid = item.get("uid")
        if not uid:
            continue
        open_uids.add(uid)
        meta = state.get(uid)
        if not meta:
            meta = {"entity_id": entity_id, "created_at": now_ts, "last_pinged": 0.0}
            state[uid] = meta

        age = now_ts - meta.get("created_at", now_ts)
        if not force and age < GRACE_SECONDS:
            continue

        since_last_ping = now_ts - meta.get("last_pinged", 0.0)
        if not force and since_last_ping < PING_INTERVAL_SECONDS:
            continue

        item["_age_seconds"] = age
        items_to_ping.append(item)
        if age >= ESCALATION_SECONDS:
            escalating = True

    # Garbage-collect timing rows for items no longer open (i.e. checked off).
    for uid in list(state.keys()):
        if state[uid].get("entity_id") == entity_id and uid not in open_uids:
            state.pop(uid, None)

    if not items_to_ping:
        return

    items_to_ping.sort(key=lambda x: -x["_age_seconds"])

    if _send_reminder(entity_id, sender_name, items_to_ping, escalating):
        ts = _utcnow_ts()
        for it in items_to_ping:
            meta = state.get(it["uid"])
            if meta is not None:
                meta["last_pinged"] = ts


def run_once(force=False):
    if not force and _in_quiet_hours():
        _log("[cycle] skipped - quiet hours")
        return 0

    # Collapse trusted senders to the distinct to-do lists they feed.
    entities_by_id = {}
    for _, cfg in _load_trusted_senders().items():
        entities_by_id.setdefault(cfg["list"], cfg["name"])

    state = _load_state()
    for entity_id, sender_name in entities_by_id.items():
        _cycle_for_entity(entity_id, sender_name, state, force=force)
    _save_state(state)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle even in quiet hours; useful for smoke tests.",
    )
    args = ap.parse_args()
    if not HA_TOKEN:
        print("Error: HA_TOKEN not set.", file=sys.stderr)
        sys.exit(1)
    rc = run_once(force=args.once)
    sys.exit(rc)


if __name__ == "__main__":
    main()
