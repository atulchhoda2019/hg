---
name: guest-assistant-ui-testing
description: Run the hospitality guest assistant locally and test governed chat, confirmation safety, and isolated fault lanes through its UI.
---

# Local guest-assistant UI testing

Run from `guest-agent-flow` in the hg repository. The tested environment has Python at `/home/ubuntu/venv-baf311/bin/python`; verify the interpreter and installed dependencies before starting.

## Devin Secrets Needed

None for the default mocked app. Keep escalation and external tracing disabled.

## Setup

- Serve with `/home/ubuntu/venv-baf311/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8300`; open `http://localhost:8300/`.
- Explicitly set `ESCALATION_BACKEND=none` and `LANGSMITH_TRACING=false`.
- Use separate fresh `CHECKPOINT_PATH` SQLite files and `AUDIT_LOG` JSONL paths for each instance. Use new conversation IDs between independent scenarios, but deliberately retain the conversation for multi-turn checks.
- Restart after code updates. Fault flags and mocked write state are process-local, so reset the process as well as checkpoint state for a clean drill.
- Run rate and timeout lanes on separate ports, e.g. 8301 with `RATE_CHANGE_ONCE=1` and 8302 with `SOR_TIMEOUT_ONCE=1`; do not combine faults unintentionally.

## UI routes and assertions

- Brand, guest and dates are trusted context controls. Brand options supply compatible guests; guest mismatch adversarial checks need a deliberately mismatched request rather than normal dropdown selection.
- B-LUX/G-7001 requires preview then Confirm. B-EXPRESS/G-9001 drafts only; B-CLASSIC/G-9501 hands booking off; B-HARBOR/G-8001 executes and notifies.
- Read exact expected rates, dates, reservations and loyalty values from current fixtures before testing; assert both included and excluded offers/content.
- Compare named Family and King quotes at the same property to catch content leaking between room types or rate plans.
- In a rate drill, capture the original preview, first confirmation's refreshed preview, and second confirmation's receipt. Check new proposal ID/nonce, disabled old controls, no booking before reconfirmation, and only one new reservation afterward.
- When explicitly authorized to test nonce tampering, submit the bad nonce to `/confirm`, then use the still-pending UI confirmation. Refusal alone does not prove the correct nonce remains usable.
- Corroborate exactly-once behavior with a UI reservation read and isolated audit events; a receipt alone does not establish absence of duplicate writes.
- Check console and visible trace after scenarios, including no guest email values. Verify proposal, nonce, receipt, composer and Send containment at 320px and 400px using browser emulation.

Screenshots and recordings should use the latest loaded code. After a scope-changing fix, retire affected visual evidence rather than presenting old failures as current.
