# 8. Crosscutting concepts

## Configuration

Everything is environment-driven; the full extracted surface (18 reads across
the three modules) is listed in
[generated/api-surface.md](generated/api-surface.md). `TRUSTED_SENDERS` is the
allowlist *and* routing table: a JSON map of number → `{name, list}`
(`src/task_extract.py:_load_trusted_senders`).

## Failure handling: fail toward bothering the user

Every external call (`_classify`, `_embed`, `_get_open_todos`, `_add_todo`,
`_send_actionable`) catches `requests.RequestException`, logs, and degrades to
the path that cannot silently lose a task (docs/ARCHITECTURE.md § "Failure
modes, by design"). De-dup failure reports "not a duplicate" so the task is
added anyway.

## Logging

Append-only line log via each module's `_log()` to `TASK_LOG_PATH`
(default `~/task_pipeline.log`). Logging failures are swallowed — logging must
never take the pipeline down. Runtime logs are gitignored (they contain real
task text and numbers).

## State durability

The reminder sidecar is written atomically (`_save_state`: temp file +
`Path.replace`) so a mid-write crash can't corrupt it; corrupted state is
detected and reset (`_load_state`).

## Security posture

- Trusted-sender allowlist gates all classification; unknown senders are
  ignored before any model call (`handle_message`).
- Group chats and the account's own outbound messages are filtered
  (`listener_example._handle_event`).
- HA access uses a long-lived bearer token from the environment; no secret
  values exist in the repo.

## Quiet hours & pacing

Reminders self-gate to waking hours (`QUIET_START_HOUR`=7,
`QUIET_END_HOUR`=23), one consolidated notification per cycle, grace 1h /
interval 2h / escalation 24h — constants at the top of
`src/task_reminders.py`.
