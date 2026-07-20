# 12. Glossary

| Term | Meaning here |
|---|---|
| Trusted sender | A number present in `TRUSTED_SENDERS`; the only senders ever classified. The map also routes each sender to their to-do list. |
| Task | A message (or fragment) the classifier judges to be a request to *do* something, rendered as short imperative text. |
| Confidence routing | HIGH → silent add after de-dup; MEDIUM → Accept/Skip phone notification; LOW / not-a-task → ignored. |
| Provider layer | `providers.py` — the single seam all AI traffic crosses: universal request style + the cloud guardrail. |
| Universal (OpenAI-style) format | The HTTP request shape (`/chat/completions`, `/embeddings`) that local servers and cloud providers alike accept: base URL + optional key + model name. |
| Passthrough (`CHAT_EXTRA_BODY`) | A JSON object merged verbatim into chat requests for provider-specific tuning — e.g. Ollama's `{"think": false}`. Empty by default; empty never breaks anything. |
| Local endpoint | Loopback, private-network (RFC 1918), link-local, Tailscale (`100.64.0.0/10`, `*.ts.net` — D-0017), `.local`/`.lan`/`.home`/`.internal`, or single-label hosts. Everything else is non-local and triggers the guardrail. |
| Cloud acknowledgment (`ACCEPT_CLOUD_TEXT`) | The one-time, deliberate switch without which a non-local AI endpoint refuses to run — the "conscious, never accidental" half of decision D-0002. |
| Debounce | The 8-second window in which a burst of messages from one sender is joined into a single unit before classification. |
| De-dup | Embedding-based comparison (cosine ≥ `DEDUP_THRESHOLD`, default 0.80 (D-0016)) of a new task against *open* items only. No embeddings → check off, nothing dropped. |
| Actionable notification | An HA Companion-app notification with Accept/Skip buttons; only the task id survives the tap (in the action string), so the task itself waits in the pending store (D-0018). |
| Redacted logging | The default: logs hold flow and errors, never message/task wording (`<redacted N chars>`); `LOG_VERBOSE` restores content locally. |
| wtp-check | The config checker command: one green/red line per check, plain-language fixes, secrets reported by validity only. |
| Grace window / escalation / quiet hours | 1h before a task's first reminder; overdue tone after 24h; no reminders outside 07:00–23:00. |
| Sidecar state | The reminder daemon's JSON file holding only per-item timing, never task content. |
| Pending store | `TASK_PENDING_PATH` — staged medium-confidence tasks keyed by tid, popped before add (idempotent), TTL-pruned. Holds task text locally, like the to-do list itself. |
| Showcase extraction | This repo's origin: generalised from a private working deployment; since INC-001, also a plug-and-play tool. |
