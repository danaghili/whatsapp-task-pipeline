# 10. Quality requirements

Derived from the goals the artifacts actually state (README.md, INC-001,
docs/ARCHITECTURE.md); no invented scenarios.

| Quality | Scenario the design answers | Mechanism |
|---|---|---|
| Reliability (no lost tasks) | AI down, JSON malformed, embeddings absent, de-dup fetch fails | Fail-open paths through `providers.py` + `task_extract.py`; every failure degrades toward a notification, never a silent drop |
| Privacy | A message from the household must not leave the network unnoticed | The D-0002 guardrail at the single AI seam: local defaults, refuse-without-ack, destination warnings, name-stripping; redacted logs (D-0005) |
| Recoverability | Any process dies mid-flight | State ownership split; atomic sidecar writes; reconnect loop |
| Considerate UX | 3 a.m. reboot; five open tasks | Quiet-hours gate + `RunAtLoad=false` (and the compose loop equivalent); one consolidated notification per cycle |
| Approachability (new since INC-001) | A stranger sets this up alone | `wtp-check`'s plain-language green/red report; the README walkthrough; `pip install .` / `docker compose up` (D-0009: under ~30 minutes to first task) |

**Test coverage:** 65 offline tests (gate, routing, de-dup threshold both
sides, fail-open paths, provider request shapes, guardrail, redaction,
checker matrix) plus a 10-test real-model suite (`WTP_REAL_TESTS=1`) proving
the wire-level claims. The adoption-era "no tests" gap (finding F-1) is
closed; see [11-risks.md](11-risks.md) for what remains.
