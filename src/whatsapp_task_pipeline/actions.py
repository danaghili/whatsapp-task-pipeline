"""Tool-side handling of the Accept / Skip buttons on medium-confidence
actionable notifications.

Why this lives in the tool, not a Home Assistant automation
-----------------------------------------------------------
The Home Assistant **Android** Companion app returns only a fixed set of
fields in the ``mobile_app_notification_action`` event: ``action``, ``tag``,
``title``, ``message``, ``clickAction``, the action buttons, and some device
metadata. Any *custom* payload attached to the notification — a nested object
**or** flat top-level keys — is dropped. An HA automation reading
``trigger.event.data.<custom>`` therefore gets nothing and silently adds
nothing. (Verified on a Pixel by capturing the raw event: the only task-
bearing fields that survive are ``message`` and the ``tag``/``action`` string.)

The one field we fully control that reliably round-trips is the **action
string**, which carries a unique task id (``tid``). So the tool — which already
holds the full task at the moment it sends the notification — stashes it in a
small on-disk pending store keyed by ``tid``, subscribes to the action event,
and on Accept/Skip looks the task back up by ``tid`` and performs the add
itself. Only the ``tid`` has to survive the round-trip, and it does.

Consequences of the design
---------------------------
* No custom notification payload, no fragile message-text parsing, and it
  works for any number of trusted senders → different to-do lists.
* The store is persisted (atomic write), so a listener restart between send
  and tap does not lose the pending task. The one real limitation: the
  listener must be running at the moment of the tap to receive the event —
  acceptable for a long-lived daemon, and documented.
"""

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

# Action-string prefixes. The tid is everything after the prefix.
ACCEPT_PREFIX = "ACCEPT_TASK_"
SKIP_PREFIX = "SKIP_TASK_"

PENDING_PATH = Path(
    os.path.expanduser(os.environ.get("TASK_PENDING_PATH", "~/task_pipeline_pending.json"))
)

# Un-actioned entries older than this are pruned on every access, so a stream
# of never-tapped notifications can't grow the store without bound.
PENDING_TTL_SECONDS = int(
    os.environ.get("TASK_PENDING_TTL_SECONDS", str(14 * 24 * 3600))
)


def _now() -> float:
    return time.time()


def _load() -> dict:
    if not PENDING_PATH.is_file():
        return {}
    try:
        data = json.loads(PENDING_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(store: dict) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True))
    tmp.replace(PENDING_PATH)  # atomic; a mid-write crash can't corrupt it


def _prune(store: dict) -> dict:
    cutoff = _now() - PENDING_TTL_SECONDS
    return {k: v for k, v in store.items() if float(v.get("created_at", 0)) >= cutoff}


def stage(
    tid: str,
    *,
    text: str,
    due_hint: Optional[str],
    sender: str,
    sender_number: str,
    entity_id: str,
) -> None:
    """Record a pending task keyed by ``tid`` at notification-send time."""
    store = _prune(_load())
    store[tid] = {
        "text": text,
        "due_hint": due_hint,
        "sender": sender,
        "sender_number": sender_number,
        "entity_id": entity_id,
        "created_at": _now(),
    }
    _save(store)


def parse_action(action: str) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(verb, tid)`` for a recognised action string, else ``(None, None)``.

    verb is ``"accept"`` or ``"skip"``.
    """
    if action.startswith(ACCEPT_PREFIX):
        return "accept", action[len(ACCEPT_PREFIX):]
    if action.startswith(SKIP_PREFIX):
        return "skip", action[len(SKIP_PREFIX):]
    return None, None


def handle_action(
    action: str,
    *,
    add_todo: Callable[[str, str, Optional[str], str], bool],
    log: Callable[[str], None],
) -> bool:
    """Resolve an Accept/Skip action string against the pending store.

    ``add_todo(entity_id, text, due_hint, sender) -> bool`` is injected so this
    module carries no HTTP or config concern and is trivially unit-testable.

    Returns True if the action was one of ours (recognised), regardless of
    whether the tid was still pending — a second tap, or a tap after the TTL,
    is handled idempotently as a logged no-op rather than a double add.
    """
    verb, tid = parse_action(action)
    if not verb:
        return False
    store = _prune(_load())
    task = store.pop(tid, None)
    _save(store)  # pop first: a duplicate delivery can't add twice
    if task is None:
        log(f"[action] {verb} for unknown/expired tid={tid} — ignored")
        return True
    if verb == "accept":
        add_todo(task["entity_id"], task["text"], task.get("due_hint"), task.get("sender", "Unknown"))
    else:
        log(f"[action] skipped tid={tid}")
    return True
