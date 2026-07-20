# 4. Solution strategy

Rationale below is cited to `docs/ARCHITECTURE.md` (an artifact that predates
adoption) or marked as not captured. Nothing here is reconstructed guesswork.

## Give the model the smallest possible job

The LLM returns a JSON opinion (`is_task`, `confidence`, `tasks[]`); every
consequence — trusted-sender gate, de-dup, routing, HA writes — is
deterministic Python (`src/task_extract.py:handle_message`). Why: an
unpredictable component becomes safe when nothing "intelligent" touches Home
Assistant directly (docs/ARCHITECTURE.md § "Classification", README.md § "Why
it's built this way").

## Two biases pointing in opposite directions, on purpose

- The **prompt** is conservative: "if unsure, not a task" — keeps chatter off
  the list (`src/task_extract.py` PROMPT).
- The **pipeline** is fail-open: model unreachable, JSON unparseable, or
  de-dup failing never eats a task
  (docs/ARCHITECTURE.md § "JSON by contract, not by force").

## JSON by contract, not by force

The model is asked for JSON, not constrained to it; `_extract_json` strips
fences and grabs the outermost brace pair, and a parse failure is a safe skip.
Ollama's schema-constrained decoding is the named upgrade path if logs ever
show parse failures (docs/ARCHITECTURE.md § 4, README.md § "Notes, limits,
honesty").

## State lives where it belongs

| State | Owner |
|---|---|
| What tasks exist | Home Assistant to-do list |
| A pending Accept/Skip | The phone notification payload itself |
| Ping timing | Reminder daemon's sidecar JSON (`TASK_STATE_PATH`) |
| "Is this a task?" | Nowhere — recomputed per message |

Any component can crash and restart without data loss
(docs/ARCHITECTURE.md § "Where state lives").

## One front door, N services

A single listener owns the WebSocket, reconnect loop, and debounce; handlers
are independent modules exposing `handle_message(text, sender)`
(docs/ARCHITECTURE.md § "One front door, N services"). The stated trade-off:
the listener is a single point of failure, mitigated by supervisor restart.
