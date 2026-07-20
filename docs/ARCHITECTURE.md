# Architecture

A deeper look at how the pipeline works and why it's shaped this way.

## Topology

Three roles, each doing what it's best at. In the original deployment these were
three machines (a Home Assistant host, a small always-on server running the
Python, and a box running local models); they can equally be one.

```
 Home Assistant                     Application host                  Model host (Ollama)
┌──────────────────────┐          ┌───────────────────────────┐    ┌──────────────────┐
│ message bridge        │  event  │ listener.py               │    │ chat model       │
│  └─ emits event       │ ──────► │  └─ debounce + handler    │ ─► │ (classifier)     │
│                       │  (WS)   │     chain                 │    └──────────────────┘
│ todo.tasks_inbox      │  REST   │     └─ task_extract.py    │    ┌──────────────────┐
│ notify.mobile_app_*   │ ◄────── │        + actions.py       │ ─► │ embedding model  │
│                       │         │                           │    │ (de-duplication) │
│ notification-action   │  event  │ (Accept/Skip resolved     │    └──────────────────┘
│  (Accept/Skip tap)    │ ──────► │  in the listener by tid)  │
│                       │  (WS)   │ task_reminders.py         │
│                       │         │  (scheduler, every 30m)   │
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

**Medium confidence** → an actionable notification. The code mints a random task
id (`tid`), **stages the full task in a small pending store keyed by that tid**,
and sends a notification whose Accept/Skip buttons carry the tid in their action
strings (`ACCEPT_TASK_<tid>`). Tapping a button makes the Companion app fire a
`mobile_app_notification_action` event; the listener receives it, reads the tid
back out of the action string, resolves it against the pending store, and does
the add (Accept) or a logged no-op (Skip) itself. See the "Accept/Skip" section
below for why this is handled tool-side rather than by an HA automation.

Body-tapping the notification deep-links to the sender's chat so the original
message can be read before deciding.

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

## Accept/Skip: why the tool handles it, not an HA automation

The obvious way to route the Accept/Skip buttons is a Home Assistant automation
that reads the task out of the notification's `data` on the
`mobile_app_notification_action` event. That does not work — and fails
*silently*, which is worse than failing loudly.

The **Android Companion app returns only a fixed set of fields** on that event.
A raw capture of a real tap (Pixel):

```
action, action_1_key, action_1_title, action_2_key, action_2_title,
clickAction, device_id, message, server_id, tag, title, webhook_id
```

Any custom payload attached to the notification — a nested object *or* flat
top-level keys — is **absent**. An automation reading
`trigger.event.data.<custom>.text` therefore gets an empty string, its
"length > 0" guard fails, and it adds nothing while looking like it worked. (An
earlier design shipped exactly this bug; it only surfaced under a real-device
test, because the high-confidence direct-add path masks it day to day.)

The one field we fully control that reliably round-trips is the **action
string**, which carries the tid. So the flow is:

1. On send, `_send_actionable` **stages** the full task (`text`, `due_hint`,
   `sender`, `entity_id`) in a small on-disk pending store keyed by `tid`
   (`actions.py`), *before* posting the notification — so an instant tap can't
   race the write.
2. The notification carries no custom data — just title, message, tag, the two
   action buttons (each with the tid), and the `clickAction` deep-link.
3. On tap, the **listener** receives `mobile_app_notification_action`, reads the
   tid from the action string, and `actions.handle_action` pops the staged task
   and performs the add (Accept) or logs the dismissal (Skip).

Properties this buys:

- **It works**, on Android and iOS, with no custom-payload round-trip.
- **Multi-list**: the staged entry remembers which sender's list to use — a
  single hard-coded automation couldn't.
- **Idempotent**: the store entry is popped before the add, so a duplicate event
  delivery can't add twice.
- **Survives a restart** between send and tap (the store is on disk, atomic
  write). The one real limitation: the listener must be running *at the moment
  of the tap* to receive the event — fine for a long-lived daemon, and the
  reason the pending store also has a TTL so never-tapped entries are pruned.

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
| A pending Accept/Skip | The listener's pending store (keyed by tid) | Only the tid round-trips from the phone; the store holds the rest. Atomic write survives a restart. |
| Ping timing | Reminder daemon's sidecar JSON | Pure derived metadata; safe to rebuild from the list. |
| The opinion "is this a task?" | Nowhere — recomputed per message | The model is stateless; no memory to corrupt. |

Because no single component is authoritative for more than its own slice, any of
them can die and restart without data loss: the listener reconnects, the daemon
re-reads the list, the pending store is on disk.

## Failure modes, by design

| Failure | Behaviour |
|---|---|
| Chat model unreachable | Log, return False; message falls through to other handlers. |
| JSON won't parse | Treated as low confidence → no action; raw reply logged. |
| De-dup fetch / embed fails | Skip de-dup, add anyway (missing a task is worse than a dupe). |
| HA add fails | Logged; the reminder loop never invents tasks, so nothing is fabricated. |
| Listener crashes | Supervisor restarts it; both subscriptions re-established, pending store on disk. |
| Listener down at the moment of a tap | The action event is missed (not queued) — the only real gap; keep the listener supervised. The staged entry is later pruned by TTL. |
| Host reboots at night | Reminder job's `RunAtLoad=false` + quiet-hours gate = no 3 a.m. nudge. |

## What I'd reach for next

- **Schema-constrained decoding** if the classifier ever showed parse failures
  in the logs — a hard guarantee in place of the current soft contract.
- **Per-sender tuning** (different lists, thresholds, quiet hours) — the config
  map already supports multiple senders; the reminder constants would move from
  module-level to per-list.
