# Stay Agent (Google ADK)

A hotel platform agent built on Google ADK, around one rule:

> **Models interpret, tables decide, code executes, humans own the edge.**

Two flows share one set of controls:

- **Room truth** — floor plans in, *proposed* room attributes out, deterministic validation,
  escalation instead of rejection, human fix, append-only versioned commit with provenance.
- **Concierge** — search versioned attributes, read one live quote, preview with a nonce,
  confirm, execute idempotently, verify by reading back, hand over a receipt.

Everything external (CRS, rate engine, reservations, content index) is mocked in-process, so the
whole thing runs offline and deterministically — including the fault drills.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example stay_agent/.env     # then paste your key into GOOGLE_API_KEY
python scripts/make_fixtures.py     # floor plans, seeds, eval fixtures (already committed)

adk web                             # chat with the concierge in the ADK dev UI
adk run stay_agent                  # or in the terminal
```

Get a Gemini Developer API key at <https://aistudio.google.com/apikey>. ADK picks it up from
`stay_agent/.env`:

```
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GOOGLE_API_KEY=...
```

Vertex AI is the same code path — set `GOOGLE_GENAI_USE_VERTEXAI=TRUE` plus `GOOGLE_CLOUD_PROJECT`
and `GOOGLE_CLOUD_LOCATION` instead, and drop the key.

**No key?** Everything except the chat surface still runs: classification and extraction fall back
to committed fixtures, the deciders fall back to the rules table, and the full test suite passes.
Force that mode with `STAY_CLASSIFIER=fixture STAY_EXTRACTOR=fixture STAY_DECIDER_MODE=rules_only`.

## Try it without the chat UI

```bash
python scripts/run_pipeline.py PR-1 PR-2 PR-3 PR-4   # the four room-truth outcomes
python scripts/demo_corridor.py                      # search -> quote -> preview -> confirm
RATE_CHANGED_ONCE=1 python scripts/demo_corridor.py  # arm a drill and watch it refuse
python scripts/decider_experiment.py                 # accuracy / p50 / p95 / $ per 1k
uvicorn review_ui.app:app --reload --port 8080       # human review of escalated jobs
```

The four fixtures are chosen to hit the four interesting endings:

| Doc | Property | What it is | Ending |
| --- | --- | --- | --- |
| PR-1 | H-201 | clean plan | `AUTO_COMMIT`, new attribute version |
| PR-2 | H-204 | FAX-quality scan | `ESCALATE` — quality alone is enough |
| PR-3 | H-202 | contains a room the CRS has never heard of | `ESCALATE`, not waivable |
| PR-4 | H-201 | declared renovation | `AUTO_COMMIT` with a drift warning, prior version kept |

## How it fits together

```
intake -> classify -> extract (one branch per floor, parallel) -> validate -> gate -> commit
                                                                     |
                                                            escalate -> review UI -> human fix
                                                                     -> commit w/ approver
                                                                     -> golden label
```

```
slot_extractor -> search_rooms -> get_live_quote -> propose_action -> [guest says yes] -> confirm_action
```

Callbacks wrap every model and tool step:

| Hook | Callback | Job |
| --- | --- | --- |
| `before_model` | `redact_and_ground` | strip card-like numbers, inject the current attribute version |
| `after_model` | `price_guard` | a money figure the session never read is replaced, not shipped |
| `before_tool` | `registry_gate` | only registry-declared tools, with schema-valid args, may run |
| `after_tool` | `audit_and_receipt` | hashed args, tool, status, time, receipt |

### Data temperatures

| Kind | Example | Rule |
| --- | --- | --- |
| Prose | descriptions, policies | indexed, retrievable |
| Facts | floor, area, view, connecting rooms | versioned with provenance, never overwritten |
| Live | price, availability | read at the moment of use, never stored past quote expiry |

Room attributes may not contain a field starting with `price`, `rate`, `avail` or `occupanc` —
`attribute_schema.yaml` enforces it and a test proves it.

### The corridor

`propose_action` renders the preview text *in code* from typed quote data and mints a nonce with an
expiry. `confirm_action` then re-checks, in order: pending proposal, nonce match, expiry, rate
version, availability — before executing idempotently and reading the reservation back. A stale
rate returns `REFRESH`; lost availability returns `SOLD_OUT`; a replayed nonce cannot book twice.
Framework confirmation is UX only; the nonce check inside `confirm_action` is the authority.

### Deciders

One interface, three implementations, chosen per question by `registry/decision_table.yaml`:

```python
class TypedDecider(Protocol):
    async def decide(self, question: str, options: list[str], state: dict[str, Any]) -> Decision: ...
```

Gemini for open-ended replies, Gemma for cheap bounded classification, rules for anything with a
compliance shadow. A malformed or unavailable model answer falls back to rules rather than guessing.
`scripts/decider_experiment.py` scores all three against `tests/eval/golden_decisions.jsonl`.

### Fault drills

Set any of these to `1` in the environment (or `faults.set_flag(...)` in a test):

`RATE_CHANGED_ONCE`, `SOLD_OUT_AFTER_PREVIEW`, `CRS_TIMEOUT_ONCE`, `MODEL_PRICE_HALLUCINATION`,
`EXTRACTOR_HALLUCINATE_ROOM`, `CONNECTING_ASYMMETRY`, `DUPLICATE_CONFIRM`.

## Tests

```bash
pytest -q                                    # 65 offline tests, no network, no key
ruff check .
adk eval stay_agent tests/eval/stay.evalset.json   # S1..S12, needs a key
```

`tests/eval/stay.evalset.json` holds the twelve scenarios: happy booking, ambiguity, no match, rate
change, sold out, CRS timeout, price hallucination, upsell, cancellation, registry refusal,
provenance question, prompt injection.

## Layout

```
stay_agent/
  agent.py            root_agent — what `adk web` / `adk run` discover
  contracts.py        every typed boundary (Pydantic)
  config.py           model + mode selection, credential detection
  models.py           the one place that talks to Gemini
  concierge/          slot agent, tools, corridor, callbacks, prompts
  room_truth/         intake, classify, extract, validate, gate, commit, queue
  deciders/           port + gemini / gemma / rules
  registry/           actions, attribute schema, validation rules, decision table (YAML)
  mocks/              inventory, rate engine, reservations, content index, faults, floor plans
  tracing.py          OpenTelemetry setup and the spans ADK cannot know about
review_ui/            FastAPI surface for escalated jobs
deployment/           Agent Runtime (Agent Engine) deploy script
scripts/              fixtures, pipeline demo, corridor demo, decider experiment, evalset
tests/                offline pytest suite + ADK eval set
```

## Tracing

ADK emits OpenTelemetry spans for every agent, tool and model call. `STAY_TRACE` decides where
they go, and the app adds the spans ADK cannot know about — `concierge.search_rooms` (which
attribute version answered, under which slots), `concierge.get_live_quote` (rate version),
`corridor.propose` / `corridor.confirm` (status, receipt verified), and one per room-truth step
with the quality band and gate decision.

```bash
STAY_TRACE=console python scripts/run_pipeline.py PR-2       # spans to stdout
STAY_TRACE=cloud GOOGLE_CLOUD_PROJECT=ihgapp adk web         # spans to Cloud Trace
adk web --trace_to_cloud                                     # ADK's own equivalent
```

The questions worth asking after an incident — was there a live quote before that price, which
version answered this search, why did this floor plan escalate — are span attributes, not log
lines.

## Deploying to Agent Runtime

`root_agent` is a plain ADK agent, so it deploys to Agent Runtime (Agent Engine) as an `AdkApp`:

```bash
pip install -e ".[deploy,trace]"
export GOOGLE_CLOUD_PROJECT=ihgapp
export GOOGLE_CLOUD_LOCATION=us-central1
export GOOGLE_CLOUD_STAGING_BUCKET=gs://ihgapp-agent-staging
gcloud auth application-default login      # or a service account with Vertex AI User + Storage Admin

python deployment/deploy_agent_engine.py create
python deployment/deploy_agent_engine.py list
python deployment/deploy_agent_engine.py update --resource-id <id>
```

The deployed app runs against Vertex (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`), so no API key travels
with it, and `STAY_TRACE=cloud` puts its spans in the same project. Cloud Run is the same package
via `adk deploy cloud_run --project ihgapp --region us-central1 stay_agent`.

The mocks deploy with it: the deployment is real, the CRS, rate engine and reservation system
behind it are not. Swapping them means satisfying the contracts in `contracts.py` and nothing else.
