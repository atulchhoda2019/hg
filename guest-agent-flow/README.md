# guest-agent-flow

A governed guest assistant for hotel brands: one LangGraph state machine per conversational
turn, over a fully mocked hospitality data plane (CRS availability and rates, property content,
loyalty, reservations, reservation SOR). It is the hospitality twin of the benefits runtime in
`atulchhoda2019/ibmp` — same governance, hotel nouns — and is standalone: its own intent
catalog, decision table, brand bundles and fixtures.

Models interpret, tables decide. The classifier only proposes an intent; `decision_table.yaml`
(`guest-table-v6`) picks the graph, the posture, the autonomy rung, the budgets and the evidence
the turn may read. Nothing the model emits creates a route, a tool call, a price or a booking.

## Run it

```bash
pip install -r requirements.txt
python -m pytest -q                  # 40 tests
python scripts/validate_config.py    # served-config build gate
uvicorn app.main:app --port 8300
```

The chat UI is at `http://localhost:8300/`. The brand, the guest and the stay dates come from
the context panel, not from the message; search results render as bookable offers; a booking
renders as a typed proposal with a Confirm button carrying its proposal id and nonce; the right
panel replays `/trace` for the turn that just ran.

```bash
curl -s localhost:8300/turn -H 'content-type: application/json' -d '{
  "conversationId":"C-1","brandId":"B-LUX","guestRef":"G-7001",
  "utterance":"family suite near the park, under $300, with breakfast",
  "uiContext":{"check_in":"2026-06-12","check_out":"2026-06-14"}}'
# -> {"kind":"answer", "offers":[...], "citations":[...]}

curl -s localhost:8300/turn -H 'content-type: application/json' -d '{
  "conversationId":"C-1","brandId":"B-LUX","guestRef":"G-7001",
  "utterance":"book offer OF-RIV-FAM"}'
# -> {"kind":"preview", "proposal":{...}, "nonce":"..."}

curl -s localhost:8300/confirm -H 'content-type: application/json' -d '{
  "conversationId":"C-1","proposalId":"PRP-...","nonce":"..."}'
# -> {"kind":"receipt", "receipt":{"verified_confirmation_number":"RES-...", ...}}

curl -s localhost:8300/trace/C-1     # redacted per-node audit trail
```

## The graph

```
ingress -> planner -> gate_plan -> [read_evidence | read_facts | read_calc]
        -> assemble -> reason -> gate_output
        -> READ:  respond            (gate_output may send one failed read to `escalate` first)
        -> WRITE: corridor_propose -> corridor_preview -> corridor_wait_confirmation
                  -> corridor_revalidate -> corridor_execute -> corridor_verify -> respond
```

`corridor_wait_confirmation` is a LangGraph `interrupt()`, so the nonce is checked *inside* the
graph and `/confirm` resumes the same run from the SQLite checkpoint instead of starting a second
one. Killing the process between preview and confirm loses nothing. A confirmation that does not
match keeps the run parked rather than discarding the hold, and three rejections or an expiry end
the corridor with no write at all.

## Filter first, then retrieve

"family suite near the park, under $300, with breakfast" is a set of typed predicates, not a
search string. Brand, market, stay window, party size, room type, live availability, price
ceiling, amenity and landmark are applied as hard filters against CRS inventory *before* any
property content is read, so a beautifully written page for a sold-out or out-of-budget room can
never reach the answer. Rates, taxes, totals, points and points-and-cash splits are Decimal
arithmetic in `app/mocks/calculator.py`; the model never computes money.

## Governance

| Rung | Behaviour | Brand |
| --- | --- | --- |
| 0 | search only; booking is handed off | `B-CLASSIC` |
| 1 | draft only: the typed, validated booking is filled in and handed to the brand's page | `B-EXPRESS` |
| 2 | propose -> preview -> nonce -> revalidate -> execute -> verify -> receipt | `B-LUX` |
| 3 | the same corridor without the human pause, plus notify | `B-HARBOR` |

The effective rung is `min(brand bundle rung, row rung_max)`, so a table row can never grant a
brand more autonomy than its bundle bought. A guest whose membership belongs to another brand is
refused at `gate_plan`, and property content is filtered by brand, property and the version
effective on the stay date before anything is ranked.

`gate_output` rejects a draft with an uncited sentence, a citation that is not in the envelope, a
number no envelope payload supports, or PII in the text; one retry, then the frontier if the turn
is eligible, then a scripted handoff. Missing required evidence abstains instead of guessing.

At the moment of writing, `corridor_revalidate` re-reads the rate, the room and the cancellation
terms. If the price or the policy moved, the proposal is rebuilt, an autonomous rung drops back to
asking, and the guest confirms the new price — a moved rate is never silently paid. The write is
keyed by proposal id, an unknown outcome is reconciled by that key rather than retried, and the
receipt is only issued after the reservation is read back out of the system of record.

## Frontier escalation (deepagents)

The ladder is failed check -> one composer retry -> frontier -> human, and `escalate` is the
frontier rung: a deepagents loop that gets one attempt at the same answer. It is advisory by
construction.

- Only a READ turn escalates. A booking never reaches it; the corridor is not model-driven.
- Its only tools are `list_evidence` and `read_evidence_item`, closures over the envelope the
  graph already assembled and authorized. No CRS, no PMS, no SOR, no corridor tool.
- The table still picked the graph, and the frontier answer re-enters `gate_output` on the same
  citation/number/PII rules; a rejection or a provider error falls through to the human.

Off unless `ESCALATION_BACKEND=deepagents`, and it needs the extra install:

```bash
pip install -r requirements-escalation.txt
ESCALATION_BACKEND=deepagents \
ESCALATION_MODEL=compat:nvidia/nemotron-3-ultra-550b-a55b:free \
ESCALATION_BASE_URL=https://openrouter.ai/api/v1 \
ESCALATION_API_KEY=sk-or-... uvicorn app.main:app --port 8300
```

`ESCALATION_MODEL` takes any tool-calling model as `openai:gpt-4.1` / `anthropic:claude-...`
(the provider's own endpoint and env var) or `compat:<model>` with `ESCALATION_BASE_URL` for any
OpenAI-compatible server (Ollama, Groq, OpenRouter, vLLM).

## Fault drills

| flag | what it does |
| --- | --- |
| `RATE_CHANGE_ONCE=1` | the nightly rate moves between preview and revalidate |
| `SOR_TIMEOUT_ONCE=1` | the booking commits but the response is lost, so it must be reconciled |
| `MODEL_UNCITED_CLAIM=1\|always` | the composer invents a rate that the gate must cut |
| `MODEL_TIMEOUT_ONCE=1` | the composer drops one call |
| `CONTENT_EMPTY=1\|always` | property content comes back empty and the turn must abstain |

All five are exercised in `tests/`.

## Tracing

Tracing is off unless both `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` are set (optionally
`LANGSMITH_PROJECT`); without them every span is a no-op and nothing leaves the box. One root run
per turn (`turn:<conversation_id>`, tagged with the brand, config versions as metadata), with
every node and tool call as child spans, payloads redacted exactly as in the audit log.
`/trace/{conversation_id}` returns the root run URL of the last turn as `langsmith_run_url`, and
the chat UI links it above the node list.

## Demo fixtures

| Guest | Brand | Tier | Notes |
| --- | --- | --- | --- |
| `G-7001` | `B-LUX` | Platinum | 148000 points, party of 4, reservation `RES-77410` |
| `G-7002` | `B-LUX` | Silver | small balance: the points route is refused before any write |
| `G-8001` | `B-HARBOR` | Gold | execute-and-notify |
| `G-9001` | `B-EXPRESS` | Blue | draft only |
| `G-9501` | `B-CLASSIC` | Blue | search only |
