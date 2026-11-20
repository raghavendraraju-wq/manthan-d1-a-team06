# ReturnRight – test suite

Runnable pytest tests against **interface stubs**. Every domain test fails with
`NotImplementedError` until the team implements the stubs in `returnright/`.
The Prometheus alert tests pass today, because the alert rules file is included.

## Run

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-test.txt
pytest
```

The alert tests use `promtool`. If `promtool` is not on PATH, they run it from
the `prom/prometheus:v3.5.0` Docker image (override with `PROM_IMAGE`). With
neither available, they are skipped.

## Layout

| Path | What it is |
|---|---|
| `returnright/contracts.py` | Shared types: states, events, roles, exceptions, gateway and clock protocols |
| `returnright/*.py` (others) | Stubs to implement: `eligibility`, `pricing`, `events`, `engine`, `flags`, `access` |
| `tests/fakes.py` | Fake clock, mock gateway (dedupes on key; can decline or time out), fake email |
| `tests/builders.py` | Crafted event builders used by every sequence test |
| `monitoring/alerts.rules.yml` | Draft alert rules |
| `monitoring/alerts.test.yml` | promtool cases: crafted series in, expected alerts out |

## Tests by rule

| File | Covers |
|---|---|
| `test_rule_eligibility.py` | 30-day and 10-day windows, day 30 vs 31, day 10 vs 11, IST vs UTC midnight, non-returnable categories, days left, explanations |
| `test_rule_pricing.py` | Grade C at 50%, ₹10,000 vs ₹10,000.01, threshold after grading, threshold per item |
| `test_rule_state_machine.py` | Per-item transitions, finance gate, scan rules, cancel, expiry, re-grade, mixed baskets, Open/Completed, audit trail |
| `test_rule_idempotency.py` | Double click, new key for a refunded item, timeout before or after payment, reconcile, concurrent requests, store credit, decline |
| `test_rule_flags.py` | More than 3 returns in 30 days, per customer, window by arrival time, duplicates counted once, flag never blocks |
| `test_late_events.py` | Carrier event arriving after Received; return event dated outside the window (counts by arrival) |
| `test_malformed_events.py` | 21 malformed shapes refused; engine state, audit and gateway untouched |
| `test_replay.py` | Same event sequence into two fresh engines gives identical states, ledger, audit and refusals |
| `test_alert_rules.py` | Rules file valid; promtool cases pass; same input run twice gives identical alerts |
| `test_access.py` | Each role sees only its screen; only the right role can scan, grade or approve; one-time code rules |
| `test_e2e_flows.py` | The brief's 10 written end-to-end cases, following the prototype flows |

## Decisions these tests encode

- Return windows run from the delivery date to the end of that day in IST.
- The ₹10,000 finance check uses each item's final amount after grading, strictly more than ₹10,000.
- Idempotency follows ADR-001: the idempotency key and one refund per return item. Repeating the same key returns the stored result; a new key for an already refunded item raises `DuplicateRefund`.
- Finance approval starts the refund, so re-grading is allowed until that point.
- Flagged customers: more than 3 returns, counted by arrival time over 30 days, dashboard only.
- Alerts:
  - Refund success below 95% over 5 minutes is `severity: critical`.
  - Gateway p95 latency above 5 s for 5 minutes is `severity: warning`.
- A refused event writes no audit entry and is listed in `rejected_events()`.

## Still to decide (tests skipped)

- How a retry works after the gateway declines a refund, given the one-refund-per-item rule.
- How long a one-time code stays valid.
- How many days before an unshipped return expires. The test checks only that Expired is reachable from Approved.
