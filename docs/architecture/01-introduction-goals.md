# 1. Introduction & goals

Turn messages from a trusted person into tracked Home Assistant to-do items,
with a reminder loop that nags until the item is checked off. A local LLM
classifies each message; deterministic Python does everything with
consequences. Extracted from a working household deployment and published as a
showcase (`README.md`, initial commit `cf3e698`).

## Goals (from README.md, in priority order)

1. **Never silently lose a task.** Every infrastructure failure path errs
   toward bothering the user rather than dropping a request
   (docs/ARCHITECTURE.md § "JSON by contract, not by force").
2. **Local-first privacy.** By default no message text leaves the network;
   cloud endpoints exist only behind a deliberate, one-time acknowledgment
   with the sender's name stripped (README.md § "Privacy, plainly";
   DECISIONS.md D-0002).
3. **Minimal LLM job.** The model only renders an opinion as JSON; routing,
   de-dup, and writes are ordinary testable code (README.md § "Why it's built
   this way").
4. **Plug-and-play for a stranger** (since INC-001). A Home Assistant user
   who is not the author reaches their first task in under ~30 minutes using
   only the README and `wtp-check` (DECISIONS.md D-0009).

## Stakeholders

| Who | Stake |
|---|---|
| The household PM | Receives tasks + reminders on their phone; owns the deployment |
| The trusted sender(s) | Their requests must reliably become tasks |
| Home Assistant users adapting the showcase | Need clear config surface and honest docs |
