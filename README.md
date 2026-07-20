# WhatsApp Task Pipeline

Turn messages from a trusted person ("can you grab milk on the way home?") into
tracked to-do items — and get nagged until they're done. A small, local-first
home-automation pipeline: a message comes in, a **local** LLM decides whether
it's actually a task, and if so it lands on a Home Assistant to-do list with a
reminder loop behind it. Nothing leaves the house.

> Built for my own household (my partner would text me things and I'd forget).
> Generalised here into a reusable "trusted sender → task list" pipeline.

## What it does

```
Partner texts you  ─►  is it from a trusted sender?  ─►  local LLM: is this a task?
                                                              │
                        ┌─────────────────────────────────────┼─────────────────────┐
                        ▼                                     ▼                       ▼
                   HIGH confidence                     MEDIUM confidence        not a task
                   de-dup vs open items          phone notification with        (ignored)
                   then add to to-do list         Accept / Skip buttons
                                                          │
                                              Accept ─► add to list
                                              Skip   ─► dropped

           a reminder loop nags every 2h (grace: 1h) until you tick it off,
                     escalating tone after 24h, silent overnight
```

- **Trusted-sender gate** — only messages from configured numbers are ever classified.
- **Local classification** — an Ollama chat model (e.g. Qwen) returns a small JSON verdict; nothing is sent to a cloud API.
- **Confidence-based routing** — clear tasks are added silently; ambiguous ones ask you first via an actionable phone notification.
- **Semantic de-duplication** — embeddings + cosine similarity stop "get milk" being added twice, while a re-ask of a *completed* task still comes through.
- **Reminder loop** — consolidated nudges on a schedule, quiet overnight, escalating when overdue. Ticking the item off is the only "dismiss".

## Why it's built this way

The one idea worth stealing: **give the model the smallest possible job — form
an opinion — and keep every consequence in ordinary code you can read, test and
log.** The LLM never calls Home Assistant. It returns JSON; deterministic Python
does the gating, de-dup, routing and the actual writes. That's what makes an
unpredictable component safe to put in a home-automation loop.

A few deliberate choices fall out of that:

- **Fail toward bothering you.** Every failure path (model unreachable, JSON
  unparseable, de-dup lookup fails) errs on the side of *not silently eating a
  task*. A duplicate or an unnecessary "was this a task?" costs one tap; a
  dropped request from your partner costs more.
- **State lives where it belongs.** The to-do list is Home Assistant's. The
  pending Accept/Skip decision lives entirely inside the phone notification. The
  reminder daemon owns only timing metadata. Any component can crash and restart
  without losing anything.
- **One front door, N services.** The message listener is a thin shared entry
  point that fans each message out to independent handlers; adding a new one is
  a new module plus a line. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Repository layout

| Path | What it is |
|---|---|
| `src/task_extract.py` | Classifier + router + de-dup. The core. Exposes `handle_message(text, sender)`. |
| `src/task_reminders.py` | The reminder loop. Runs on a scheduler; self-gates to waking hours. |
| `src/listener_example.py` | Minimal reference listener (HA WebSocket → debounce → handler chain). |
| `homeassistant/automation.task_notification_response.yaml` | HA automation that routes the Accept/Skip buttons. |
| `deploy/com.example.task-reminders.plist` | launchd template for the reminder loop (macOS). |
| `.env.example` | All configuration, with placeholder values. |
| `docs/ARCHITECTURE.md` | The deeper technical write-up. |

## Requirements

- **Home Assistant** with a [`todo`](https://www.home-assistant.io/integrations/todo/)
  list entity and the mobile Companion app (for actionable notifications).
- A message source that emits a Home Assistant event carrying a sender + text.
  This was built against a WhatsApp bridge integration firing a
  `whatsapp_message_received` event, but the listener isn't WhatsApp-specific —
  point it at any event with a sender and body.
- **[Ollama](https://ollama.com/)** reachable on your network, with a chat model
  (e.g. `qwen3:32b`) and an embedding model (`nomic-embed-text`) pulled. These
  can be the same host or two different ones — see the note in `.env.example`.
- Python 3.10+.

## Setup

```bash
git clone https://github.com/danaghili/whatsapp-task-pipeline.git
cd whatsapp-task-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: HA_URL, HA_TOKEN, NOTIFY_SERVICE, TRUSTED_SENDERS, Ollama URLs
```

Then:

1. **Create a to-do list** in Home Assistant (e.g. `todo.tasks_inbox`) and match
   it in `TRUSTED_SENDERS`.
2. **Import the automation** in `homeassistant/` so the Accept/Skip buttons work.
3. **Run the listener**: `source .env && python src/listener_example.py`
   (daemonise it however you like — launchd, systemd, a container).
4. **Schedule the reminders**: adapt `deploy/com.example.task-reminders.plist`
   (macOS) or a cron / systemd timer, running `src/task_reminders.py` every
   ~30 min.

### Try the classifier without any messaging setup

```bash
source .env
python src/task_extract.py 441234567890 "can you grab milk on the way home?"
# -> classifies, de-dups, and adds to the list (if 441234567890 is a trusted sender)
```

## Configuration reference

Everything is environment-driven; see [`.env.example`](.env.example) for the
full annotated list. The knobs you'll most likely touch:

| Variable | Purpose |
|---|---|
| `TRUSTED_SENDERS` | JSON map of number → `{name, list}`. The allowlist and routing table. |
| `CLASSIFIER_MODEL` / `OLLAMA_CHAT_URL` | Which chat model, and where. |
| `EMBED_MODEL` / `OLLAMA_EMBED_URL` | De-dup embedding model and host. |
| `DEDUP_THRESHOLD` | Cosine similarity above which a task is a duplicate (default 0.85). |
| `NOTIFY_SERVICE` | Your phone's HA notify service. |

Reminder cadence (grace window, ping interval, escalation, quiet hours) lives as
constants at the top of `src/task_reminders.py`.

## Notes, limits, honesty

- The classifier is asked for JSON by prompt and parsed defensively — it is not
  *forced*. Malformed output causes a safe skip, never a bad write. If you want
  a hard guarantee, Ollama's structured-output mode takes a JSON schema and
  constrains decoding; it's a small change to `_classify`.
- The reference listener is deliberately slim. The production version it came
  from also handled conversational replies and calendar extraction through the
  same front door; that fan-out pattern is described in the architecture doc.
- No tests are included here — this is a showcase extraction, not a library.

## License

MIT — see [LICENSE](LICENSE).
