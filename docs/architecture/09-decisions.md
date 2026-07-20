# 9. Architecture decisions

The decision log (`docs/DECISIONS.md`) began at adoption (2026-07-20) and
backdates nothing. It now carries:

- **D-0001** — the adoption itself, with the PM's confirmations (identity,
  separateness of the household deployment, the recorded intolerable event).
- **D-0002…D-0011** — the ten PM-ratified INC-001 decisions (provider
  approach, cloud guardrail Option A, de-dup independence, checker
  catch-list, redacted logging, Docker scope, packaging floor, test bar,
  success measure, config approach). D-0002 (cloud opt-in, floor:
  external-api) and D-0005 (redacted logs, floor: auth-security) are
  **one-way doors** with the PM's said-back trade-offs recorded verbatim.
- **D-0012…D-0014** — build-time resolutions of the increment's open
  questions (package/command naming, the WhatsApp integrations named by the
  README, the local/non-local boundary and passthrough encoding — the last
  verified on the real wire).

Where a "why" predates adoption, the reference set cites the pre-adoption
artifact directly (`docs/ARCHITECTURE.md`, code comments, commit `cf3e698`)
or carries the honest marker: **Rationale not captured — the artifact shows
the choice, not the alternatives weighed.**

No separate ADR files exist yet; the D-entries above carry the
context/decision/rejected-alternatives content ADRs would duplicate. ADRs
under `docs/architecture/decisions/` remain the vehicle for future decisions
that outgrow the log format.
