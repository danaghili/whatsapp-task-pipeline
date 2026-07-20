# 2. Constraints

| Constraint | Source |
|---|---|
| Python 3.10+ (uses `dict[str, ...]` annotations in `src/listener_example.py`) | README.md § Requirements |
| Home Assistant is the required hub: `todo` integration, Companion app actionable notifications, automation engine | Woven through all three modules (REST + WebSocket calls) |
| LLM endpoints speak the Ollama API dialect (`/api/chat` with `think` option, `/api/embeddings`) | `src/task_extract.py:143`, `src/task_extract.py:158` |
| All configuration via environment variables; no config files parsed by code | Config surface in [generated/api-surface.md](generated/api-surface.md) |
| Message source must emit a Home Assistant event carrying sender + text | `src/listener_example.py` (subscribes to `whatsapp_message_received`) |
| Only two third-party dependencies: `requests`, `websockets` | `requirements.txt` |
| No automated tests ship with the repo | README.md § "Notes, limits, honesty" (stated openly) |
