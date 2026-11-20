# ReturnRight – working rules

Retail returns and refunds portal (Track A). React 19 + Vite SPA, FastAPI on Python 3.13, PostgreSQL 18,
mock payment gateway as a separate service, Prometheus + Grafana, Docker Compose.
Read `docs/architecture.md` (system), `docs/build-plan.md` (steps, open decisions) before changing anything.

## How we work

- **Ask before assuming or deciding.** Decisions D1–D10 are recorded in `docs/build-plan.md` §6; anything not there, ask.
- Plan first, then build one step; commit after each **verified** step (tests run, not just written).
- The tests in `tests/` are the contract. Never edit a test to make it pass; if a test looks wrong, ask.
- Don't add a tool or library outside the agreed stack without asking (build plan D8).

## Commands

```powershell
.venv\Scripts\activate          # Python 3.13 venv at repo root
pytest                          # domain + API tests (alert tests need promtool or Docker)
docker compose up --build       # full stack
```

## State-machine rules (server-enforced, per item)

- States: Requested → Approved → In transit → Received → Inspected → (Pending finance) → Refund pending → Refunded;
  side exits Rejected, Cancelled, Expired, Refund failed. Terminal: Refunded, Rejected, Cancelled, Expired.
- A return is **Open** until every item is terminal, then **Completed**.
- Ineligible at request → Rejected with a rule code (`NON_RETURNABLE_CATEGORY`, `WINDOW_EXPIRED`).
- Windows: 30 d standard, 10 d electronics, from delivery date to **end of that day in IST**. Innerwear and perishables are never returnable.
- Scan (agent only) works only in Approved or In transit; a scan with no carrier event skips straight to Received.
- Customer may cancel only before Received. Expiry (system) only from Approved, after `EXPIRY_DAYS` (placeholder 14).
- Grade C refunds 50 %; A/B 100 %. Finance gate: an **item's final amount > ₹10,000** (strictly) → Pending finance. Never per return.
- Re-grade allowed until the refund starts (it can move an item in or out of Pending finance).
- Only the right role may send each event (agent: scan/inspect; finance: approve/reject; customer: request/cancel;
  system: carrier, refund ≤ ₹10k, expiry). Wrong role → `Forbidden`.
- A refused event changes nothing, writes **no** audit row, and is recorded in `rejected_events()`.
- Every state change writes exactly one audit row, in the **same DB transaction**. The UI never sets state; it requests a transition.

## Refund rules (ADR-001 – do not weaken)

- Idempotency key per refund action **and** one refund per return item (DB unique on both).
- Save Refund pending + key **before** calling the gateway; never hold a DB transaction open across the call.
- Same key again → return the stored result, no error, no new payment. New key on a refunded item → `DuplicateRefund`.
- Timeout → stay Refund pending; retry with the same key or let the reconcile job settle it via the gateway's status lookup.
- Decline → Refund failed; one retry with a new key is allowed (placeholder), then manual follow-up.
- Store credit: same states, same ledger row and guards, no gateway call, `gateway_ref = NULL`.

## Other rules

- Flagged customer: > 3 returns in 30 days, counted by **arrival** time, same return counted once. Dashboard only; never blocks.
- Customer access: order no. + email (case/space-insensitive) → 6-digit code by email, single use, a new code voids earlier ones,
  valid `OTP_TTL_MINUTES` (placeholder 10). JWTs live in memory in the SPA, never localStorage.
- Staff see only their role's screen (`access.can_access`); enforce on the API too, not just in the SPA.
- Metrics names are a contract with `monitoring/alerts.rules.yml`: `returnright_refunds_total{outcome}` and
  `returnright_gateway_request_duration_seconds` (histogram).
- Money is `Decimal`, INR, never float. Timestamps are timezone-aware; stored in UTC, judged in IST where the rules say so.
- UI follows the prototype tokens (`--rr-*`), 44 px touch targets, works at 390 px width, accessible colours.
