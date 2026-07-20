# 7. Deployment view

Three roles; originally three machines, equally valid as one
(docs/ARCHITECTURE.md § Topology).

```mermaid
graph TB
    subgraph ha_host["Home Assistant host"]
        bridge["WhatsApp bridge integration"]
        todo["todo.* list entity"]
        auto["Accept/Skip automation"]
        notify["notify.mobile_app_*"]
    end
    subgraph app_host["Application host"]
        listener["listener_example.py<br/>(long-lived daemon)"]
        reminders["task_reminders.py<br/>(launchd/cron, every 30 min)"]
    end
    subgraph model_host["Model host(s)"]
        chat["Ollama chat model<br/>(OLLAMA_CHAT_URL)"]
        embed["Ollama embed model<br/>(OLLAMA_EMBED_URL)"]
    end
    listener -- WebSocket --> ha_host
    listener --> chat
    listener --> embed
    reminders -- REST --> ha_host
```

- The listener runs as a supervised daemon (any supervisor; reconnect loop is
  built in). The reminder script is fired unconditionally by a scheduler and
  self-gates to waking hours — `deploy/com.example.task-reminders.plist` is
  the macOS template, with `RunAtLoad=false` so a reboot can't fire a 3 a.m.
  nudge (comment in the plist itself).
- Chat and embedding endpoints are configured separately because a chat-only
  Ollama host 404s `/api/embeddings` — a silent failure otherwise
  (comment at `src/task_extract.py:42`).
- Secrets: `HA_TOKEN` arrives via the environment (`.env`, gitignored); the
  repo ships only `.env.example` placeholders.
