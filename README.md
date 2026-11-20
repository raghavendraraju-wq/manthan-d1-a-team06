# ReturnRight – test suite

Runnable pytest tests against **interface stubs**. Every domain test fails with
`NotImplementedError` until the team implements the stubs in `returnright/`.
The Prometheus alert tests pass today, because the alert rules file is included.

## Run the app (Docker)

Needs Docker Desktop (running) and nothing else. First start takes a few minutes (images, then seeding ~2,000 orders).

```powershell
docker compose up --build -d      # build and start everything
docker compose ps                 # wait until api shows (healthy)
start http://localhost:8088       # the app
```

| What | URL |
|---|---|
| App (customer + staff) | http://localhost:8088 |
| Mailbox with the one-time codes | http://localhost:8025 |
| Mock gateway switches (make refunds fail or time out) | http://localhost:8081/admin |
| Prometheus (alerts, targets) | http://localhost:9090 |
| API docs | http://localhost:8000/docs |

Demo walk-through:

1. **Customer**: open the app, "Return an item", order `RR-10482`, email `priya.s@example.com`. Read the 6-digit code at http://localhost:8025 (or `curl "http://localhost:8000/api/demo/last-code?email=priya.s@example.com"`). Pick items, reason, refund method, submit. You get a QR label and a status timeline.
2. **Warehouse agent**: Staff sign-in, `arjun@returnright.test` / `demo-pass`. Enter the return code (e.g. `RTN-20xxx` from step 1), grade, Save inspection. An item whose refund is over ₹10,000 goes to finance.
3. **Finance**: `meera@returnright.test` / `demo-pass`. Approve and pay (or Reject).
4. **Manager**: `vikram@returnright.test` / `demo-pass`. Dashboard with 7/30/90 d period.

Useful commands:

```powershell
docker compose logs -f api        # follow API logs
docker compose down               # stop, keep data
docker compose down -v            # stop and wipe the database (re-seeds next start)
$env:WEB_PORT=9000; docker compose up -d   # if 8088 is taken
```

Without `DEMO_MODE=1` (set in `docker-compose.yml`) the demo logins and `/api/demo/*` helpers are off; do not ship that setting.

## Run the tests

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
