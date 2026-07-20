# 12. Glossary

| Term | Meaning here |
|---|---|
| Trusted sender | A number present in `TRUSTED_SENDERS`; the only senders ever classified. The map also routes each sender to their to-do list. |
| Task | A message (or fragment) the classifier judges to be a request to *do* something, rendered as short imperative text. |
| Confidence routing | HIGH → silent add after de-dup; MEDIUM → Accept/Skip phone notification; LOW / not-a-task → ignored. |
| Debounce | The 8-second window in which a burst of messages from one sender is joined into a single unit before classification. |
| De-dup | Embedding-based comparison (cosine similarity ≥ `DEDUP_THRESHOLD`, default 0.85) of a new task against *open* items only. |
| Actionable notification | An HA Companion-app notification carrying Accept/Skip buttons and the full pending task in its payload. |
| Grace window | 1 hour after a task first appears during which no reminder fires. |
| Escalation | After 24 hours open, reminders switch to an "overdue" tone. |
| Quiet hours | Outside 07:00–23:00 the reminder cycle exits without notifying. |
| Sidecar state | The reminder daemon's JSON file holding only per-item timing (`created_at`, `last_pinged`), never task content. |
| Showcase extraction | This repo's origin: generalised from a private working deployment, placeholders substituted, no invented features. |
