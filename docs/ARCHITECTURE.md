# Architecture

A deeper look at how the pipeline works and why it's shaped this way.

## Topology

Three roles, each doing what it's best at. In the original deployment these were
three machines (a Home Assistant host, a small always-on server running the
Python, and a box running local models); they can equally be one.

```
 Home Assistant                     Application host                  Model host (Ollama)
┌──────────────────────┐          ┌───────────────────────────┐    ┌──────────────────┐
│ message bridge        │  event  │ listener_example.py       │    │ chat model       │
│  └─ emits event       │ ──────► │  └─ debounce + handler    │ ─► │ (classifier)     │
│                       │  (WS)   │     chain                 │    └──────────────────┘
│ todo.tasks_inbox      │  REST   │     └─ task_extract.py    │    ┌──────────────────┐
│ notify.mobile_app_*   │ ◄────── │                           │ ─► │ embedding model  │
│ automation            │         │ task_reminders.py         │    │ (de-duplication) │
│  (Accept/Skip router) │         │  (scheduler, every 30m)   │    └──────────────────┘
└──────────────────────┘          └───────────────────────────┘
```

## The life of a message

### 1. Ingestion and debounce

A message bridge integration in Home Assistant emits an event for each inbound
message. The listener holds a persistent WebSocket subscription to that event
and reconnects on drop.

People send bursts — "get milk" / "and bread" / "oh and stamps" as three
separate texts. A **debounce** collects messages from the same sender for a few
seconds and joins them into one unit, so the classifier reasons over the whole
thought instead of three fragments.

### 2. One front door, N services

The listener does no task logic itself. It owns the shared plumbing — the single
WebSocket, the reconnect loop, the debounce — and fans each combined message out
to independent handlers, each exposing `handle_message(text, sender)`.

Why this shape:

- **One connection, not N.** Separate daemons would each hold their own socket,
  their own reconnect problem, their own drifting debounce.
- **Cheap to extend.** A new capability is a new module plus one line in the
  chain. In the system this was extracted from, the same front door also fed a
  conversational-reply handler and a calendar/meeting extractor; adding this
  task pipeline changed none of them.
- **Isolation.** Each handler call is wrapped, so one throwing an exception can't
  deafen the others.
- **Non-consuming by default.** A handler engaging with a message doesn't stop
  later handlers seeing it — one message can be both a task *and* a calendar note
  ("book the dentist for Tuesday 3pm").

The trade-off, stated plainly: the listener is a single point of failure — if it
dies, every service goes deaf at once. That's the honest cost of the shared
front door, mitigated by running it under a supervisor that restarts it.

### 3. Classification

`handle_message` first applies the **trusted-sender gate**: a lookup in the
`TRUSTED_SENDERS` map. Unknown sender → return immediately. This map is also the
routing table (which list, whose name), so onboarding a person is a config
change, not a code change.

Then one HTTP call to the local chat model, with two deliberate settings:

- `temperature: 0` — take the most likely tokens, no creativity.
- `think: false` — reasoning/scratchpad mode measurably *hurts* small local
  models on short structured-output tasks. (This matched repeated benchmarking
  in the original project; the voice models there ran think-off for the same
  reason.)

The prompt defines "task", the confidence levels, and a JSON shape to emit.

### 4. JSON by contract, not by force

The model is *asked* for JSON, not *constrained* to it. `_extract_json` assumes
the reply may be chatty: it strips markdown fences, grabs the outermost brace
pair, and parses. Parse failure → treated as low confidence → safe skip, with
the raw reply logged.

Note the two biases pointing in opposite directions, on purpose:

- the **prompt** tells the model to be *conservative* ("if unsure, not a task") —
  this keeps ordinary chatter off the list;
- the **pipeline** is *fail-open* — infrastructure failures never silently eat a
  real task.

Those aren't in tension: one filters noise, the other refuses to lose signal.

A stronger option exists and is a small change: Ollama's structured-output mode
takes a JSON schema and constrains *decoding* so invalid output is impossible.
The soft prompt-contract approach is used here because at `temperature 0` with a
clear prompt the model is very well-behaved, and the logs would show
`json parse fail` lines the moment it wasn't.

### 5. Routing: the two paths

**High confidence** → de-duplicate → `POST todo/add_item`.

**Medium confidence** → an actionable notification. This is the neat part. The
code mints a random task id and embeds it in the action names
(`ACCEPT_TASK_<tid>`), with the full task payload carried in the notification's
`data`. Tapping a button makes the Companion app fire a
`mobile_app_notification_action` event back into Home Assistant; a small
automation pattern-matches the prefix and performs the add (Accept) or a logged
no-op (Skip).

The consequence worth noticing: **the pending decision lives entirely inside the
notification.** The application host doesn't hold any "awaiting answer" state; it
can restart freely. Body-tapping the notification deep-links to the sender's chat
so the original message can be read before deciding.

### 6. De-duplication

New task text is embedded with a local embedding model; each *open* item on the
target list is embedded the same way and compared by cosine similarity. Above
the threshold (default 0.85) → duplicate, skip.

Two details matter:

- The comparison set is **only open items**, fetched live. A re-ask of a task you
  already completed correctly reads as new.
- Chat and embedding models may live on **different hosts**. A chat-only Ollama
  server returns 404 on `/api/embeddings`; keeping the endpoints separate and
  explicit avoids a failure that is otherwise silent. (This bit us once in the
  original build — hence the loud comment in the code.)

If de-dup fails for any reason it reports "not a duplicate" and the task is added
anyway — a duplicate costs one tap, a dropped task costs more.

## The reminder loop

A scheduler fires `task_reminders.py` every ~30 minutes. It fires
unconditionally; the script self-gates to waking hours (so a 3 a.m. reboot can't
trigger a nudge). Each cycle:

1. Pull open items from Home Assistant. **The to-do list is the source of
   truth** — the daemon stores no task data.
2. A sidecar JSON holds only *timing* per item UID: `created_at`, `last_pinged`.
   It's written atomically (temp file + rename) so a mid-write crash can't
   corrupt it.
3. Per item: under the grace window (1h) → skip; otherwise ping if the interval
   (2h) has elapsed since the last ping. Items past the escalation age (24h)
   flip the whole notification to an "overdue" tone.
4. Send **one consolidated** notification per cycle ("Partner asked (3)"),
   oldest first, top three listed — never one ping per task.
5. Garbage-collect: any timing row whose item is no longer open (i.e. ticked
   off) is dropped. **Checking the item off is the only dismiss** — no separate
   protocol needed.

## Where state lives (the load-bearing decision)

| State | Owner | Why it's there |
|---|---|---|
| What tasks exist | Home Assistant to-do list | The user already manages it; it survives everything else restarting. |
| A pending Accept/Skip | The phone notification payload | No server-side "awaiting answer" to persist or lose. |
| Ping timing | Reminder daemon's sidecar JSON | Pure derived metadata; safe to rebuild from the list. |
| The opinion "is this a task?" | Nowhere — recomputed per message | The model is stateless; no memory to corrupt. |

Because no single component is authoritative for more than its own slice, any of
them can die and restart without data loss: the listener reconnects, the daemon
re-reads the list, the notification waits on the phone.

## Failure modes, by design

| Failure | Behaviour |
|---|---|
| Chat model unreachable | Log, return False; message falls through to other handlers. |
| JSON won't parse | Treated as low confidence → no action; raw reply logged. |
| De-dup fetch / embed fails | Skip de-dup, add anyway (missing a task is worse than a dupe). |
| HA add fails | Logged; the reminder loop never invents tasks, so nothing is fabricated. |
| Listener crashes | Supervisor restarts it; subscription re-established, no state lost. |
| Host reboots at night | Reminder job's `RunAtLoad=false` + quiet-hours gate = no 3 a.m. nudge. |

## What I'd reach for next

- **Schema-constrained decoding** if the classifier ever showed parse failures
  in the logs — a hard guarantee in place of the current soft contract.
- **Per-sender tuning** (different lists, thresholds, quiet hours) — the config
  map already supports multiple senders; the reminder constants would move from
  module-level to per-list.
