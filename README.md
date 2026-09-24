# hg

Governed guest AI for hotel brands.

- [`guest-agent-flow/`](guest-agent-flow) — the runtime inside the agent call: one LangGraph
  state machine per turn, deterministic routing from a versioned decision table, filter-first
  availability search, a nonce-bound booking corridor, and an advisory-only frontier rung.
- [`stay-agent-adk/`](stay-agent-adk) — the same governance on Google ADK with Gemini: floor-plan
  room-truth extraction into versioned attributes with provenance, deterministic validation that
  escalates instead of rejecting, a human review surface, and a concierge whose only money path is
  a live quote inside a nonce-bound corridor.
