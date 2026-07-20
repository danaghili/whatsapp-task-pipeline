#!/usr/bin/env python3
"""
Task classifier and router for inbound messages from trusted senders.

Public entry point:  handle_message(combined_text, sender_number) -> bool

Given a (debounced) message from a trusted sender, ask a local LLM whether it
contains a task for the household to act on. High-confidence tasks are added
straight to a Home Assistant to-do list (after a semantic de-duplication check);
medium-confidence ones are sent to the phone as an actionable notification with
Accept / Skip buttons, and the tool itself performs the add on Accept (the task
is staged by tid in a pending store and resolved when the action event arrives
— see actions.py for why this is done tool-side rather than in an HA automation).

Design notes
------------
* The model's only job is to render an *opinion* as JSON. Every consequence
  (the trusted-sender gate, de-dup, high-vs-medium routing, the actual HA calls)
  lives in ordinary, testable code below. Nothing "intelligent" ever touches
  Home Assistant directly.
* JSON is requested by prompt contract and recovered defensively, not enforced.
  A malformed reply can only cause a safe skip, never a bad write. If you want a
  hard guarantee, Ollama's structured-output mode accepts a JSON schema and
  constrains decoding — a small change to `_classify`.
* Returning True means "we engaged with this message", NOT "stop processing".
  A single message can legitimately be both a task and, say, a calendar note, so
  the caller is free to show it to other handlers too.
"""

import json
import os
import re
import time
import uuid
from typing import Optional, Tuple

import requests

# --- Configuration (all via environment; see .env.example) -------------------

# All AI traffic (chat classification + de-dup embeddings) goes through the
# provider layer — one universal request style for local and cloud endpoints,
# with the cloud guardrail enforced there. See providers.py.
from . import providers
from . import actions

# 0.80 catches real-world paraphrases ("buy some milk" vs "pick up milk"
# measures 0.81 on nomic-embed-text) while same-wording re-asks score 0.97+.
# Measured against the real model in the INC-001 verification run (D-0016).
DEDUP_THRESHOLD = float(os.environ.get("DEDUP_THRESHOLD", "0.80"))

# Redacted-by-default logging (INC-001 D5, fixes adoption finding F-2): the
# default log records the flow and errors, never message or task wording — a
# log a stranger shares for help can't leak their household's words. Setting
# LOG_VERBOSE=true restores full content for local debugging.
LOG_VERBOSE = os.environ.get("LOG_VERBOSE", "").strip().lower() in ("1", "true", "yes")

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

# The HA notify service to target for actionable notifications, e.g.
# "notify.mobile_app_your_phone".
NOTIFY_SERVICE = os.environ.get("NOTIFY_SERVICE", "notify.mobile_app_your_phone")

LOG_PATH = os.path.expanduser(os.environ.get("TASK_LOG_PATH", "~/task_pipeline.log"))

# Trusted senders: only messages from these numbers are classified. Supplied as
# a JSON object mapping E.164-ish number -> {name, list}. Everyone else is
# ignored. Adding a person is a config change, not a code change.
#
#   TRUSTED_SENDERS='{"441234567890": {"name": "Partner", "list": "todo.tasks_inbox"}}'
def _load_trusted_senders() -> dict:
    raw = os.environ.get("TRUSTED_SENDERS", "").strip()
    if not raw:
        # Safe, obviously-placeholder default so the module imports cleanly.
        return {"000000000000": {"name": "Partner", "list": "todo.tasks_inbox"}}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        _log("[config] TRUSTED_SENDERS is not valid JSON; no senders active")
        return {}


PROMPT = """You are classifying a message from {sender_name} to the household.

Return ONLY a JSON object, no prose, no markdown fence:
{{
  "is_task": true | false,
  "confidence": "high" | "medium" | "low",
  "tasks": [
    {{"text": "<short imperative, e.g. 'pick up milk'>", "due_hint": "<text or null>"}}
  ]
}}

Rules:
- A "task" is a request asking the recipient to DO something — pick up, grab, take, call, book, send, fix.
- HIGH confidence: clear imperative or direct request ("can you grab milk", "please take the bins out").
- MEDIUM confidence: ambiguous or soft-task ("maybe order food tonight?", "should we get someone to look at the boiler?").
- LOW confidence or is_task=false: statements, social, status updates, questions about what the recipient is doing, follow-up chasers ("did you do X yet?").
- Multi-task messages -> split into multiple tasks. Each task.text is short and imperative.
- due_hint optional: copy the time/date phrase verbatim if mentioned, else null.
- If unsure between low and not-a-task -> set is_task=false.

The message:
{message}
"""


def _log(line: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except Exception:
        pass


def _redact(content: str) -> str:
    """Message/task wording as it may appear in the log.

    Full content only under LOG_VERBOSE; by default just an honest length
    marker, so the log still shows THAT something happened and how big it
    was, without holding the household's words (INC-001 FR-1.6).
    """
    if LOG_VERBOSE:
        return repr(content)
    return f"<redacted {len(content)} chars>"


def _extract_json(text: str) -> Optional[dict]:
    """Recover a JSON object from a possibly-chatty model reply.

    Strips markdown fences, then grabs the outermost brace pair. Returns None on
    failure so the caller can treat it as a safe skip.
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _classify(message: str, sender_name: str) -> Optional[dict]:
    # The name the model sees: real for local endpoints, a neutral placeholder
    # when chat is non-local — the guardrail strips the structured "who"
    # (INC-001 D2). Determinism (temperature 0) and the Ollama think-off
    # tuning both live in the provider layer / its passthrough.
    visible_name = providers.outbound_sender_name(sender_name)
    raw = providers.chat(PROMPT.format(sender_name=visible_name, message=message))
    if raw is None:
        _log("[classify] chat provider unavailable — safe skip")
        return None
    parsed = _extract_json(raw)
    if not parsed:
        _log(f"[classify] json parse fail; raw={_redact(raw[:200])}")
    return parsed


def _embed(text: str) -> Optional[list]:
    return providers.embed(text)


def _cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _get_open_todos(entity_id: str) -> list:
    """Fetch open (needs_action) items from a HA to-do list via REST."""
    if not HA_TOKEN:
        return []
    try:
        r = requests.post(
            f"{HA_URL}/api/services/todo/get_items?return_response=true",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            json={"entity_id": entity_id, "status": "needs_action"},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        service_response = body.get("service_response", {}) if isinstance(body, dict) else {}
        bucket = service_response.get(entity_id, {})
        return bucket.get("items", []) or []
    except requests.RequestException as e:
        _log(f"[dedup] fetch open todos failed: {e}")
        return []


def _is_duplicate(new_text: str, entity_id: str) -> Tuple[bool, float, Optional[str]]:
    """Return (is_dup, best_similarity, matched_uid).

    Compares only against *open* items on the same list, so a re-ask of a
    previously-completed task correctly reads as new. On any failure it reports
    "not a duplicate" — a duplicate costs one tap, a dropped task costs more.
    """
    open_items = _get_open_todos(entity_id)
    if not open_items:
        return (False, 0.0, None)
    new_vec = _embed(new_text)
    if not new_vec:
        return (False, 0.0, None)
    best = 0.0
    best_uid = None
    for item in open_items:
        summary = item.get("summary") or ""
        if not summary:
            continue
        vec = _embed(summary)
        if not vec:
            continue
        sim = _cosine(new_vec, vec)
        if sim > best:
            best, best_uid = sim, item.get("uid")
    return (best >= DEDUP_THRESHOLD, best, best_uid)


def _add_todo(entity_id: str, text: str, due_hint: Optional[str], sender_name: str) -> bool:
    if not HA_TOKEN:
        _log("[add] HA_TOKEN missing — cannot add todo")
        return False
    item = text.strip()
    desc_parts = [f"From {sender_name}"]
    if due_hint:
        desc_parts.append(f"Hint: {due_hint}")
    payload = {
        "entity_id": entity_id,
        "item": item,
        "description": " · ".join(desc_parts),
    }
    try:
        r = requests.post(
            f"{HA_URL}/api/services/todo/add_item",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        _log(f"[add] {entity_id} += {_redact(item)}")
        return True
    except requests.RequestException as e:
        _log(f"[add] failed: {e}")
        return False


def _send_actionable(
    text: str,
    due_hint: Optional[str],
    sender_name: str,
    sender_number: str,
    entity_id: str,
) -> bool:
    """Send a phone notification with Accept / Skip actions.

    The task is staged in the pending store keyed by a random task id (`tid`),
    which is embedded in the action strings. When the operator taps a button,
    the listener receives the `mobile_app_notification_action` event and calls
    `handle_notification_action`, which resolves the tid back to this task and
    performs the add (Accept) or a logged no-op (Skip).

    We do NOT put the task in the notification's `data`: the Android Companion
    app drops custom payload on the action callback (nested or flat), so only
    the tid — carried by the action string — reliably round-trips. See
    actions.py for the captured-event evidence.

    Body-tap deep-links to the sender's chat via the wa.me universal link so the
    original message can be read before deciding.
    """
    if not HA_TOKEN:
        return False
    tid = uuid.uuid4().hex[:10]
    # Stage the task BEFORE sending, so an instant tap can't race the write.
    actions.stage(
        tid,
        text=text,
        due_hint=due_hint,
        sender=sender_name,
        sender_number=sender_number,
        entity_id=entity_id,
    )
    title = f"Possible task from {sender_name}"
    body = text if not due_hint else f"{text}\n({due_hint})"
    action_buttons = [
        {"action": f"{actions.ACCEPT_PREFIX}{tid}", "title": "✓ Accept"},
        {"action": f"{actions.SKIP_PREFIX}{tid}", "title": "✗ Skip"},
    ]
    chat_url = f"https://wa.me/{sender_number}"
    payload = {
        "title": title,
        "message": body,
        "data": {
            "tag": f"task_{tid}",
            "actions": action_buttons,
            "clickAction": chat_url,
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
        _log(f"[actionable] tid={tid} text={_redact(text)}")
        return True
    except requests.RequestException as e:
        _log(f"[actionable] failed: {e}")
        return False


def handle_notification_action(action: str) -> bool:
    """Resolve an Accept/Skip tap. Called by the listener on each
    `mobile_app_notification_action` event.

    Returns True if the action string was one of ours. Wires the pending-store
    resolution in actions.py to this module's `_add_todo` and `_log`.
    """
    return actions.handle_action(action, add_todo=_add_todo, log=_log)


def handle_message(combined_text: str, sender_number: str) -> bool:
    """Entry point. Classify a trusted sender's message and route any tasks.

    Returns True if we engaged with the message (added, queued, or recognised a
    task we chose to de-dup away), False if it wasn't for us (unknown sender,
    classifier unreachable, or not a task).
    """
    trusted = _load_trusted_senders()
    cfg = trusted.get(sender_number)
    if not cfg:
        return False

    sender_name = cfg["name"]
    entity_id = cfg["list"]
    msg = (combined_text or "").strip()
    if not msg:
        return False

    parsed = _classify(msg, sender_name)
    if not parsed:
        return False

    is_task = bool(parsed.get("is_task"))
    confidence = (parsed.get("confidence") or "low").lower()
    tasks = parsed.get("tasks") or []

    if not is_task or confidence == "low" or not tasks:
        _log(f"[skip] is_task={is_task} conf={confidence} n_tasks={len(tasks)}")
        return False

    routed_any = False
    for t in tasks:
        text = (t.get("text") or "").strip()
        if not text:
            continue
        due_hint = t.get("due_hint") or None

        if confidence == "high":
            dup, sim, dup_uid = _is_duplicate(text, entity_id)
            if dup:
                _log(f"[dedup] sim={sim:.2f} matched={dup_uid} skipping {_redact(text)}")
                routed_any = True  # we engaged; just didn't add
                continue
            if _add_todo(entity_id, text, due_hint, sender_name):
                routed_any = True
        else:  # medium
            if _send_actionable(text, due_hint, sender_name, sender_number, entity_id):
                routed_any = True

    return routed_any


def main() -> None:
    """CLI smoke entry: classify one message as if it just arrived."""
    import sys

    if len(sys.argv) < 3:
        print("Usage: wtp-classify <sender_number> <message...>", file=sys.stderr)
        sys.exit(1)
    try:
        # The cloud guardrail applies to every AI-calling process (INC-001
        # FR-1.4): refuse before any request could leave the network.
        providers.enforce_startup_policy()
    except providers.CloudNotAcknowledgedError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    sender = sys.argv[1]
    text = " ".join(sys.argv[2:])
    routed = handle_message(text, sender)
    print(f"routed={routed}")


if __name__ == "__main__":
    main()
