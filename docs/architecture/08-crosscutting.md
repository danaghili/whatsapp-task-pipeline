# 8. Crosscutting concepts

## Configuration

Everything is environment-driven; the full extracted surface is listed in
[generated/api-surface.md](generated/api-surface.md). Configuration is a
`.env` file plus the `wtp-check` command — no web UI, no secret-writing
wizard (DECISIONS.md D-0011). `TRUSTED_SENDERS` remains the allowlist *and*
routing table. New knobs since INC-001: `CHAT_BASE_URL` / `CHAT_MODEL` /
`CHAT_API_KEY` / `CHAT_EXTRA_BODY`, `EMBED_BASE_URL` / `EMBED_MODEL` /
`EMBED_API_KEY`, `MESSAGE_EVENT`, `ACCEPT_CLOUD_TEXT`, `LOG_VERBOSE`.

## The privacy floor (the intolerable event, enforced)

- All AI traffic passes one seam (`providers.py`); the guardrail lives there
  (D-0002): local defaults, refuse-without-ack for non-local endpoints,
  per-start destination warnings, sender-name neutralisation outbound.
  Sender numbers never reach any model.
- Logs are redacted by default (D-0005): flow and errors, never message or
  task wording; `LOG_VERBOSE` is the explicit local opt-in. The redaction is
  visible (`<redacted N chars>`), not silent.
- Secrets are names, never values: environment-only, placeholder-only in
  `.env.example`, never printed by the checker, never logged even verbose.

## Failure handling: fail toward bothering the user

Every external call catches `requests.RequestException`, logs, and degrades
to the path that cannot silently lose a task (docs/ARCHITECTURE.md § "Failure
modes, by design"). Provider-layer failures return `None`, which the caller
treats as safe-skip (classification) or dedup-off-add-anyway (embeddings,
D-0003).

## Logging

Append-only line log via each module's `_log()` to `TASK_LOG_PATH`. Logging
failures are swallowed — logging must never take the pipeline down. Runtime
logs remain gitignored.

## State durability

Unchanged: the reminder sidecar is written atomically (temp + rename);
corrupted state resets; each component owns only its own slice
(docs/ARCHITECTURE.md § "Where state lives").

## Quiet hours & pacing

Unchanged: reminders self-gate to waking hours (7–23), one consolidated
notification per cycle, grace 1h / interval 2h / escalation 24h — constants
at the top of `task_reminders.py`.

## Testing discipline

Mock external only: the suite fakes the network seam and runs everything
inside for real; offline and fast (65 tests). The real-model make-or-break
suite (`WTP_REAL_TESTS=1`) proves the wire-level claims a mock cannot
(D-0008, INC-001 KH-1/KH-2).
