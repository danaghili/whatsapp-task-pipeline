# 11. Risks & technical debt

Ordered by how much they'd hurt. Items marked (finding) are also in the
adoption findings brief for PM disposition.

1. **No tests around the routing core (finding).** `handle_message` carries
   the trusted-sender gate, confidence routing, and de-dup decisions with zero
   automated coverage. The README states this openly ("showcase extraction,
   not a library"), but the planned evolution into a configurable tool makes
   this the first debt to pay.
2. **Listener is a single point of failure.** If it dies, every handler goes
   deaf at once. Acknowledged trade-off, mitigated by supervisor restart
   (docs/ARCHITECTURE.md § "One front door, N services").
3. **Soft JSON contract.** Classification output is prompt-requested, not
   schema-constrained; the named upgrade is Ollama structured outputs
   (README.md § "Notes, limits, honesty"). Failure mode is safe (skip) but
   silent degradation would only show in logs.
4. **De-dup cost scales with open items.** `_is_duplicate` re-embeds every
   open item on every check (`src/task_extract.py:_is_duplicate` loop) — one
   HTTP call per item. Fine at household scale; a caching layer would be
   needed for bigger lists. Rationale not captured — the artifact shows the
   choice, not the alternatives weighed.
5. **Ollama-dialect coupling.** `/api/chat` + `/api/embeddings` are
   Ollama-specific; the agreed next increment (LLM-provider abstraction)
   addresses exactly this.
6. **Reminder timing starts at first sight.** A task's `created_at` is when
   the reminder daemon first sees it, not when it was added — a restart after
   long downtime resets ages. Harmless at household scale; worth knowing.
   Rationale not captured.
