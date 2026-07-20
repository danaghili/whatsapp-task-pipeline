# 10. Quality requirements

Derived from the goals the artifacts actually state (README.md,
docs/ARCHITECTURE.md); no invented scenarios.

| Quality | Scenario the design answers | Mechanism |
|---|---|---|
| Reliability (no lost tasks) | Ollama down, JSON malformed, de-dup fetch fails | Fail-open paths in `src/task_extract.py`; every failure degrades toward a notification, never a silent drop |
| Privacy | A message from the household must not reach a cloud API | All model calls target self-hosted Ollama URLs; no other outbound endpoints exist in the code |
| Recoverability | Any process dies mid-flight | State ownership split (docs/ARCHITECTURE.md § "Where state lives"); atomic sidecar writes; reconnect loop |
| Considerate UX | 3 a.m. reboot; five open tasks | Quiet-hours gate + `RunAtLoad=false`; one consolidated notification per cycle |

**Known quality gap:** no automated tests exist (stated openly in README.md).
Recorded as a risk in [11-risks.md](11-risks.md) and surfaced as an adoption
finding.
