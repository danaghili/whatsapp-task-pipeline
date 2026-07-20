# WhatsApp Task Pipeline

Turn messages from a trusted person ("can you grab milk on the way home?") into
tracked to-do items — and get nagged until they're done. A small, local-first
home-automation pipeline: a message comes in, an LLM **you point it at** decides
whether it's actually a task, and if so it lands on a Home Assistant to-do list
with a reminder loop behind it. **By default, nothing leaves the house.**

> Built for my own household (my partner would text me things and I'd forget),
> generalised into a plug-and-play "trusted sender → task list" tool for the
> Home Assistant community. Works with any local AI server (Ollama, LM Studio,
> llama.cpp) or — as a deliberate, eyes-open choice — any OpenAI-style cloud
> provider.

## What it does

```
Partner texts you  ─►  is it from a trusted sender?  ─►  your LLM: is this a task?
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

- **Trusted-sender gate** — only messages from numbers you configure are ever
  classified; everyone else is ignored before any AI is touched.
- **Bring your own AI** — one universal setting shape (base URL + optional key +
  model name) works for every provider; local by default.
- **Confidence-based routing** — clear tasks are added silently; ambiguous ones
  ask you first via an actionable phone notification.
- **Semantic de-duplication** — embeddings stop "get milk" being added twice,
  while a re-ask of a _completed_ task still comes through.
- **Reminder loop** — consolidated nudges on a schedule, quiet overnight,
  escalating when overdue. Ticking the item off is the only "dismiss".
- **A config checker** (`wtp-check`) that validates your whole setup and names
  exactly what's wrong, in plain words, before anything confusing happens.

## What you need

| Prerequisite                                | Notes                                                                                                                                     |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Home Assistant**                          | With a [`todo`](https://www.home-assistant.io/integrations/todo/) list and the Companion app on your phone (for the Accept/Skip buttons). |
| **A WhatsApp → Home Assistant integration** | **Not provided by this tool** — see step 1.                                                                                               |
| **An AI to point at**                       | Local (recommended): [Ollama](https://ollama.com) on any machine on your network. Or any OpenAI-style endpoint, local or cloud.           |
| **Python 3.10+ or Docker**                  | Either install path works.                                                                                                                |

## Setup

Follow the steps in order; step 5 (`wtp-check`) verifies everything before you
start anything. Doing this fresh, expect the whole thing to take under half an
hour (most of it in step 1).

### Step 1 — the WhatsApp bridge (a prerequisite you install, not part of this tool)

This tool does **not** connect to WhatsApp itself. It listens for a Home
Assistant _event_ that says "a message arrived, from this number, saying this."
Getting WhatsApp messages into Home Assistant as events is the job of a
third-party integration you install in your own HA — commonly through
[HACS](https://hacs.xyz) (the Home Assistant Community Store):

- [FaserF/ha-whatsapp](https://github.com/FaserF/ha-whatsapp) — links to your
  WhatsApp via the "linked devices" mechanism; supports incoming-message events.
  Install: HACS → Integrations → ⋮ → Custom repositories → add the repo URL.
- [raulpetruta/ha-wa-bridge](https://github.com/raulpetruta/ha-wa-bridge) — an
  alternative that runs a small WhatsApp bridge in Docker.

Two things to note from your chosen integration's docs:

1. **The event name it fires** for incoming messages. Set it as
   `MESSAGE_EVENT` in your `.env` (default: `whatsapp_message_received`).
2. **The event's field layout.** The listener expects a sender and message text
   in the event data (see `_handle_event` in
   [`src/whatsapp_task_pipeline/listener.py`](src/whatsapp_task_pipeline/listener.py));
   if your integration structures its event differently, that one small
   function is the place to adapt — it's deliberately the only
   integration-specific code in the tool.

The checker can verify everything it can reach, but it can't confirm your
bridge actually delivers events until a real message arrives — so do send
yourself a test message at the end.

### Step 2 — install the tool

```bash
git clone https://github.com/danaghili/whatsapp-task-pipeline.git
cd whatsapp-task-pipeline

# Python route (needs Python 3.10+ — macOS's system python3 may be older;
# `brew install python@3.11` and use python3.11 below if so):
python3 -m venv .venv && source .venv/bin/activate
pip install .

# — or Docker route: nothing to install yet; docker compose builds in step 6.
```

### Step 3 — the secrets (a deliberate, you-typed-it edit)

```bash
cp .env.example .env
```

Open `.env` in any editor. Two values are secrets, and you add both by editing
the file yourself (the tool never writes secrets for you):

- **`HA_TOKEN`** — your Home Assistant access token. Create it in HA: click
  your name (bottom of the sidebar) → **Security** → **Long-lived access
  tokens** → Create token. Paste the value onto the `HA_TOKEN=` line.
- **`CHAT_API_KEY`** — only if you later choose a cloud provider (see "Using a
  cloud provider" below). For a local Ollama, leave it empty.

Both stay in this one local file: `.env` is gitignored, excluded from the
Docker image, and never logged.

While you're in the file, fill in the rest:

- `HA_URL` — your Home Assistant address.
- `NOTIFY_SERVICE` — your phone's notify service (HA → Developer tools →
  Actions → search `notify.mobile_app`).
- `TRUSTED_SENDERS` — the allowlist: each sender's number, name, and which
  to-do list their tasks land on. Only these numbers are ever processed.

**Creating the to-do list and finding its id** (the `"list"` value):

1. In HA: **Settings → Devices & services → Helpers → Create helper →
   To-do list**. Name it what you like — e.g. "Tasks Inbox".
2. HA derives the entity id from the name: "Tasks Inbox" becomes
   `todo.tasks_inbox`. To see the exact id, open **Developer tools →
   States** and search `todo.` — copy the id you find there.
3. Put that id as the sender's `"list"` value in `TRUSTED_SENDERS`. Each
   sender can have their own list or share one; `wtp-check` confirms every
   list you reference actually exists.

### Step 4 — the AI

**Use whatever chat model you want** — the classifier works with any capable
model; just set `CHAT_MODEL` to whatever you pull (or, for a cloud provider,
whatever your plan offers). The examples below are what this pipeline runs at
home; bigger models classify a little better, smaller ones respond faster.

The default `.env` points at an Ollama on the same machine. On whichever
machine runs Ollama:

```bash
ollama pull qwen3.6:27b        # example classifier (qwen3.6:35b if you have
                               #   the memory — or ANY model you prefer)
ollama pull nomic-embed-text   # powers the duplicate check
```

If Ollama runs on a different machine, set `CHAT_BASE_URL` /
`EMBED_BASE_URL` to it — **the URL must end in `/v1`** (Ollama's
OpenAI-compatible surface), e.g. `http://192.168.1.50:11434/v1`.

### Step 5 — check everything

```bash
wtp-check
```

(Run every `wtp-*` command from the folder holding your `.env` — they read
it automatically; anything already set in your shell or by a supervisor
wins. No `source .env` needed, ever.)

**macOS note:** the first time a freshly installed Python touches your LAN,
macOS asks for **Local Network** permission — approve it, or connections to
LAN addresses fail with "No route to host" while `localhost` and Tailscale
work fine. If you were never asked, grant it under System Settings →
Privacy & Security → Local Network for your terminal/Python.

Green line per check, red line with the exact fix for anything wrong — the
Home Assistant connection and token, the notify service, each to-do list, the
chat model, the embeddings model (including the classic silent trap where a
chat-only server can't do embeddings), your trusted-senders list, and the
privacy posture. Re-run until it's all green.

### Step 6 — start it

No Home Assistant automation is needed: the listener handles the Accept/Skip
buttons itself (it subscribes to the `mobile_app_notification_action` event and
resolves the tap against a small pending store — see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), "Accept/Skip"). Just start the
services:

```bash
# Python route — the listener is a long-lived process:
wtp-listen
# ...and schedule wtp-remind every ~30 min: launchd template in deploy/
# (macOS), or a cron/systemd timer (Linux).

# Docker route — both services, scheduled and supervised:
docker compose up -d
# (inside containers, "localhost" means the container — see the notes at the
#  top of docker-compose.yml for the two address changes that need)
```

### Step 7 — prove it end-to-end

Have your trusted sender text you something like "can you grab milk on the way
home?" — it should appear on the to-do list, and the reminder loop takes it
from there. (No messaging set up yet? Smoke-test the classifier directly:
`python -m whatsapp_task_pipeline.task_extract 441234567890 "can you grab milk"`.)

## Privacy, plainly

- **Local by default.** With the default configuration, message text reaches
  only your Home Assistant and your own AI endpoints. Nothing leaves your
  network. "Local" includes your Tailscale tailnet (`100.64.0.0/10` and
  `*.ts.net` names) — your own encrypted mesh is not the cloud.
- **Logs never hold your words.** By default the log records what happened and
  why — never message or task text — so a log you paste in a forum for help
  can't leak your household's messages. Set `LOG_VERBOSE=true` while debugging
  your own setup to see full content.
- **Sender numbers never reach any AI**, local or cloud. Only the message text
  and a name do — and the name is stripped for cloud endpoints.

### Using a cloud provider (a knowing exception)

You can point `CHAT_BASE_URL` at any OpenAI-style cloud provider. Be clear
about what that means: **your household's real message text will be sent to
that company's servers** — the one thing this tool otherwise exists to
prevent. The tool's job is to make sure that only ever happens on purpose:

- It **refuses to start** until you set `ACCEPT_CLOUD_TEXT=yes` in `.env` — a
  one-time, deliberate acknowledgment.
- Every start against a cloud endpoint prints a warning naming exactly where
  text is going.
- The sender's name is replaced with a neutral placeholder; the number is
  never sent. The message words themselves do go — no scrubber can honestly
  prevent that, so none is pretended.
- `wtp-check` flags the situation loudly.

A middle path many use: keep `CHAT_BASE_URL` in the cloud but point
`EMBED_BASE_URL` at a small local model — the duplicate check then stays fully
local. If embeddings aren't available at all, the duplicate check simply
switches off; tasks are never dropped.

## Configuration reference

Everything is environment-driven; [`.env.example`](.env.example) is the full
annotated list. The knobs you'll most likely touch:

| Variable                                        | Purpose                                                                                    |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `TRUSTED_SENDERS`                               | JSON map of number → `{name, list}`. The allowlist and routing table.                      |
| `CHAT_BASE_URL` / `CHAT_MODEL` / `CHAT_API_KEY` | Which chat AI, where, and (cloud only) its key.                                            |
| `EMBED_BASE_URL` / `EMBED_MODEL`                | De-dup embeddings — independently configurable.                                            |
| `CHAT_EXTRA_BODY`                               | Provider-specific tuning passthrough (e.g. Ollama's `{"think": false}`).                   |
| `MESSAGE_EVENT`                                 | The HA event your WhatsApp integration fires.                                              |
| `ACCEPT_CLOUD_TEXT`                             | The one-time cloud acknowledgment.                                                         |
| `DEDUP_THRESHOLD`                               | Similarity above which a task counts as a duplicate (default 0.80 — catches re-phrasings). |
| `LOG_VERBOSE`                                   | Restore full content in logs while debugging.                                              |

Reminder cadence (grace window, ping interval, escalation, quiet hours) lives
as constants at the top of
[`src/whatsapp_task_pipeline/task_reminders.py`](src/whatsapp_task_pipeline/task_reminders.py).

## Why it's built this way

The one idea worth stealing: **give the model the smallest possible job — form
an opinion — and keep every consequence in ordinary code you can read, test and
log.** The LLM never calls Home Assistant. It returns JSON; deterministic Python
does the gating, de-dup, routing and the actual writes. Every failure path
(model unreachable, JSON unparseable, de-dup lookup fails) errs on the side of
_not silently eating a task_ — a duplicate costs one tap; a dropped request
from your partner costs more.

The deeper write-up: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the
generated reference set in [`docs/architecture/`](docs/architecture/).

## Tests

```bash
pip install .[dev]
python -m pytest
```

The suite runs offline (the network is faked at the boundary): the
trusted-sender gate, the confidence routing, the de-dup threshold on both
sides, every fail-open path, the provider request shapes, the cloud guardrail,
the log redaction, and the checker's catch-list.

## License

MIT — see [LICENSE](LICENSE).
