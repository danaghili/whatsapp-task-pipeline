# 6. Runtime view

Two flows, grounded in the extracted call/import structure and the real
function names.

## The life of a message

```mermaid
sequenceDiagram
    participant HA as Home Assistant
    participant L as listener_example
    participant TE as task_extract
    participant O as Ollama (chat/embed)
    participant P as Phone

    HA->>L: whatsapp_message_received (WebSocket event)
    L->>L: _debounce(sender, text) — 8s window, bursts joined
    L->>TE: handle_message(combined, sender)
    TE->>TE: trusted-sender gate (_load_trusted_senders)
    TE->>O: _classify → POST /api/chat (temperature 0, think off)
    O-->>TE: JSON opinion (recovered by _extract_json)
    alt HIGH confidence
        TE->>HA: _get_open_todos (REST)
        TE->>O: _embed new text + each open item
        TE->>TE: _cosine ≥ DEDUP_THRESHOLD → skip as duplicate
        TE->>HA: _add_todo → todo.add_item
    else MEDIUM confidence
        TE->>HA: _send_actionable → notify.* with ACCEPT_TASK_/SKIP_TASK_ buttons
        HA->>P: actionable notification (full task in payload)
        P->>HA: mobile_app_notification_action
        HA->>HA: automation adds item (Accept) or logs (Skip)
    else not a task / LOW
        TE->>TE: safe skip, logged
    end
```

## The reminder cycle (every ~30 min via scheduler)

```mermaid
sequenceDiagram
    participant S as Scheduler (launchd/cron)
    participant TR as task_reminders
    participant HA as Home Assistant
    participant P as Phone

    S->>TR: run_once()
    TR->>TR: _in_quiet_hours? → exit silently if so
    TR->>HA: _get_open_todos per distinct list
    TR->>TR: per item: grace (1h) / interval (2h) / escalation (24h) checks
    TR->>HA: _send_reminder — ONE consolidated notification, top 3 oldest
    HA->>P: "Partner asked (N)" (+ overdue tone past 24h)
    TR->>TR: _save_state (atomic tmp+rename), GC rows for checked-off items
```

Checking the item off in Home Assistant is the only dismiss — the next cycle
garbage-collects its timing row (docs/ARCHITECTURE.md § "The reminder loop").
