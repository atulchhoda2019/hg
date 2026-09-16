# hg

Governed guest AI for hotel brands.

- [`guest-agent-flow/`](guest-agent-flow) — the runtime inside the agent call: one LangGraph
  state machine per turn, deterministic routing from a versioned decision table, filter-first
  availability search, a nonce-bound booking corridor, and an advisory-only frontier rung.
