# 11. Risks & technical debt

Ordered by how much they'd hurt. Updated at the close of INC-001 — two
adoption-era risks are resolved and marked as such (honesty rule: resolved
risks are recorded, not deleted).

1. **Listener is a single point of failure.** If it dies, every handler goes
   deaf at once. Acknowledged trade-off, mitigated by supervisor restart
   (docs/ARCHITECTURE.md § "One front door, N services"). Unchanged.
2. **The event field-mapping is bridge-specific.** `listener._handle_event`
   expects one integration family's event shape; a different bridge needs
   that one function adapted (documented in the README as the deliberate
   adaptation point; DECISIONS.md D-0013). The full message-source
   abstraction remains deferred (TSOW non-goal).
3. **Soft JSON contract.** Classification output is prompt-requested, not
   schema-constrained; failure mode is safe (skip) and now test-pinned, but
   silent degradation still only shows in logs. The named upgrade remains
   schema-constrained decoding.
4. **De-dup cost scales with open items.** `_is_duplicate` re-embeds every
   open item per check — one HTTP call per item. Fine at household scale;
   a cache would be needed for bigger lists. Rationale not captured — the
   artifact shows the choice, not the alternatives weighed.
5. **Reminder timing starts at first sight.** A task's `created_at` is when
   the reminder daemon first sees it; a restart after long downtime resets
   ages. Harmless at household scale. Rationale not captured.
6. **The 30-minute yardstick is measured, not yet stranger-proven.** The
   walkthrough's mechanics are proven against a real Home Assistant
   (docs/hardening/inc-001-real-ha-verification.md); still open: a fresh-eyes
   stranger with a stopwatch, a real WhatsApp bridge, and real phone taps.
7. **Paraphrase de-dup misses at the default threshold.** Real-model
   measurement: paraphrases score 0.81–0.83 against nomic-embed-text, under
   the 0.85 default — a re-phrased ask lands as a second item (one tap to
   dismiss; the designed fail direction). Same-wording asks (0.97+) are
   caught. Threshold is user-tunable (`DEDUP_THRESHOLD`); the default is a
   PM decision pending.

**Resolved since adoption:**

- ~~No tests around the routing core~~ (adoption finding F-1) — closed by the
  65-test offline suite + the real-model make-or-break suite (INC-001 AC-1.3,
  D-0008).
- ~~Ollama-dialect coupling~~ (adoption risk #5) — closed by the universal
  provider layer (D-0010), verified against real Ollama through its
  OpenAI-compatible surface.
- ~~Logs carry raw message text~~ (adoption finding F-2) — closed by
  redacted-by-default logging (D-0005, AC-1.7).
