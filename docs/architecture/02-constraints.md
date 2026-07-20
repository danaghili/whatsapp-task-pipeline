# 2. Constraints

| Constraint | Source |
|---|---|
| Python 3.10+ (`requires-python` in `pyproject.toml`) | DECISIONS.md D-0007 — widest reach kept |
| Home Assistant is the required hub: `todo` integration, Companion app actionable notifications, automation engine | TSOW non-goal; woven through all modules |
| All AI endpoints speak the OpenAI-style HTTP format (`/chat/completions`, `/embeddings`); Ollama via its `/v1` surface | DECISIONS.md D-0010 — one universal path, no second mode |
| Cloud endpoints are opt-in only: a non-local endpoint refuses to run without `ACCEPT_CLOUD_TEXT` | DECISIONS.md D-0002 (one-way, floor: external-api) |
| All configuration via environment variables; secrets only ever in the local `.env` | config surface in [generated/api-surface.md](generated/api-surface.md); DECISIONS.md D-0011 |
| Logs are redacted by default — no message/task content without `LOG_VERBOSE` | DECISIONS.md D-0005 (one-way, floor: auth-security) |
| Two runtime dependencies: `requests`, `websockets` (single home: `pyproject.toml`) | D-0012 — requirements.txt removed |
| Message source is a Home Assistant event (name via `MESSAGE_EVENT`); the bridge integration is an external prerequisite | INC-001 out-of-scope; D-0013 |
| No PyPI publication (installable from the repo only, this increment) | DECISIONS.md D-0007 |
