# 6. Runtime view

Two flows, grounded in the extracted call/import structure and the real
function names.

## The life of a message

```mermaid
sequenceDiagram
    participant HA as Home Assistant
    participant L as listener
    participant TE as task_extract
    participant P as providers
    participant AI as AI endpoint (local default)
    participant Ph as Phone

    Note over L: startup: providers.enforce_startup_policy()<br/>non-local endpoint + no ack → refuse
    HA->>L: MESSAGE_EVENT (WebSocket)
    L->>L: _debounce(sender, text) — 8s window, bursts joined
    L->>TE: handle_message(combined, sender)
    TE->>TE: trusted-sender gate (_load_trusted_senders)
    TE->>P: providers.chat(prompt) — sender name neutralised if non-local
    P->>AI: POST /chat/completions (temperature 0, CHAT_EXTRA_BODY merged)
    AI-->>TE: reply text (JSON recovered by _extract_json)
    alt HIGH confidence
        TE->>HA: _get_open_todos (REST)
        TE->>P: providers.embed(new + each open item)
        P->>AI: POST /embeddings
        TE->>TE: _cosine ≥ DEDUP_THRESHOLD → skip as duplicate
        TE->>HA: _add_todo → todo.add_item
    else MEDIUM confidence
        TE->>HA: _send_actionable → notify.* with ACCEPT_TASK_/SKIP_TASK_ buttons
        HA->>Ph: actionable notification (full task in payload)
        Ph->>HA: mobile_app_notification_action
        HA->>HA: automation adds item (Accept) or logs (Skip)
    else not a task / LOW / embeddings unreachable de-dup off
        TE->>TE: safe skip or add-without-dedup — never a dropped task
    end
```

## The reminder cycle (every ~30 min via scheduler)

Unchanged by INC-001 (no AI involvement — it only reads the list and pings
the phone):

```mermaid
sequenceDiagram
    participant S as Scheduler (launchd/cron/compose loop)
    participant TR as task_reminders
    participant HA as Home Assistant
    participant Ph as Phone

    S->>TR: run_once()
    TR->>TR: _in_quiet_hours? → exit silently if so
    TR->>HA: _get_open_todos per distinct list
    TR->>TR: per item: grace (1h) / interval (2h) / escalation (24h) checks
    TR->>HA: _send_reminder — ONE consolidated notification, top 3 oldest
    HA->>Ph: "Partner asked (N)" (+ overdue tone past 24h)
    TR->>TR: _save_state (atomic tmp+rename), GC rows for checked-off items
```

Checking the item off in Home Assistant is the only dismiss — the next cycle
garbage-collects its timing row (docs/ARCHITECTURE.md § "The reminder loop").
