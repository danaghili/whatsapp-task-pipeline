# 4. Solution strategy

Rationale is cited to `DECISIONS.md` entries (the log began at adoption) or to
`docs/ARCHITECTURE.md` (the pre-adoption artifact). Nothing is reconstructed
guesswork.

## Give the model the smallest possible job

The LLM returns a JSON opinion (`is_task`, `confidence`, `tasks[]`); every
consequence — trusted-sender gate, de-dup, routing, HA writes — is
deterministic Python (`task_extract.handle_message`). Why: an unpredictable
component becomes safe when nothing "intelligent" touches Home Assistant
directly (docs/ARCHITECTURE.md § "Classification").

## One universal AI path, one seam

All AI traffic flows through `providers.py` — the OpenAI-style request format
every local server and cloud provider speaks, configured as base URL +
optional key + model name (DECISIONS.md D-0010). Ollama's think-off tuning
rides an options passthrough (`CHAT_EXTRA_BODY`) rather than a second,
Ollama-native mode — one way to configure the AI, not two. Chat and
embeddings are independently configured; no embeddings simply means the
duplicate check switches off (D-0003).

## Privacy as a startup policy, not a hope

The recorded intolerable event — private message text leaking — is enforced
in code at the single AI seam (D-0002, one-way door): local endpoints by
default; a non-local endpoint refuses to run without the one-time
`ACCEPT_CLOUD_TEXT` acknowledgment; every acknowledged run warns, names the
destination, and strips the sender's name from the outgoing request. No
free-text scrubber exists, deliberately — one that missed things would
manufacture false confidence. Logs are redacted by default for the same
reason (D-0005).

## Two biases pointing in opposite directions, on purpose

- The **prompt** is conservative: "if unsure, not a task" — keeps chatter off
  the list.
- The **pipeline** is fail-open: model unreachable, JSON unparseable, or
  de-dup failing never eats a task
  (docs/ARCHITECTURE.md § "JSON by contract, not by force").

## State lives where it belongs

| State | Owner |
|---|---|
| What tasks exist | Home Assistant to-do list |
| A pending Accept/Skip | The phone notification payload itself |
| Ping timing | Reminder daemon's sidecar JSON (`TASK_STATE_PATH`) |
| "Is this a task?" | Nowhere — recomputed per message |

Any component can crash and restart without data loss
(docs/ARCHITECTURE.md § "Where state lives").

## Tested at the boundary it mocks

The suite fakes exactly one thing — the network — and runs everything inside
for real (D-0008); the make-or-break proofs run against a REAL local model
(`tests/test_real_roundtrip.py`, gated by `WTP_REAL_TESTS=1`) because a mock
hides the dialect risks the provider swap took (INC-001 KH-1).
