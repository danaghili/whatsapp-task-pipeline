# 3. Context & scope

The pipeline sits between a message source and a phone, with Home Assistant as
the hub for events, task storage, and notifications, and Ollama hosts providing
the two model capabilities.

```mermaid
graph LR
    sender(["Trusted sender<br/>(messages)"]) --> bridge["WhatsApp bridge<br/>(HA integration)"]
    bridge -- "whatsapp_message_received<br/>event (WebSocket)" --> pipeline["whatsapp-task-pipeline<br/>(this system)"]
    pipeline -- "todo.add_item / get_items<br/>notify.* (REST)" --> ha["Home Assistant"]
    pipeline -- "/api/chat" --> chat["Ollama chat host<br/>(classifier)"]
    pipeline -- "/api/embeddings" --> embed["Ollama embed host<br/>(de-dup)"]
    ha -- "actionable notification" --> phone(["Phone<br/>(Companion app)"])
    phone -- "mobile_app_notification_action" --> ha
    ha -- "Accept/Skip automation<br/>(homeassistant/automation.task_notification_response.yaml)" --> ha
```

## In scope (this repository)

- Classification, confidence routing, semantic de-duplication
  (`src/task_extract.py`)
- Reminder cadence over open items (`src/task_reminders.py`)
- Reference listener: WebSocket subscription + debounce + handler fan-out
  (`src/listener_example.py`)
- The Accept/Skip routing automation (`homeassistant/`) and a launchd template
  (`deploy/`)

## Out of scope (external systems)

- The WhatsApp bridge itself (any integration emitting the event works —
  README.md § Requirements)
- Home Assistant, its `todo` storage, and the Companion app
- The Ollama hosts and the models they serve
