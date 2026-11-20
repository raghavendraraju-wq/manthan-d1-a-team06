# ReturnRight – architecture

Exported from the architecture whiteboard (https://claude.ai/artifact/KrbsfwYB2ivfcm4zsG1R5c) on 20 Nov 2026.
Click-through prototype: https://claude.ai/artifact/7WF8AmEKrKrzbzhZokURwP

**In one line:** a React portal and a FastAPI core, with idempotent refunds through a mock gateway.

## System context

| Actor / system | Relationship to ReturnRight |
|---|---|
| Customer | Requests returns, follows status |
| Warehouse agent | Scans the QR, grades items |
| Finance reviewer | Approves refunds over ₹10,000 |
| Ops manager | Monitors the dashboard |
| Mock payment gateway | Receives refund + idempotency key; dedupes on the key |
| Prometheus + Grafana | Scrape `/metrics` |
| Mock carrier | Sends the pickup event (Approved → In transit) |

## Containers (Docker Compose, one `docker compose up` on a fresh clone)

| Container | Contents |
|---|---|
| React 19 + Vite SPA | Login + role-based views; customer wizard + timeline; agent QR scan and grading; finance approvals; manager dashboard |
| FastAPI backend (Python 3.13) | Modules below |
| PostgreSQL 18 | orders · returns · items · refund ledger · audit_log |
| Local file store | Photos, labels + QR |
| Mock payment gateway | Separate FastAPI service |
| Prometheus + Grafana | Refund success rate, gateway latency, alert |
| Seed script (Faker) | 2,000 orders · 40 products · 500 customers |
| GitHub Actions CI | lint · pytest · Playwright · images |

### Backend modules

| Module | Responsibility |
|---|---|
| Returns API + auth | Order lookup, request, upload; login, role guard |
| Eligibility engine | 30 d standard, 10 d electronics; explains every decision |
| Return state machine (per item) | Requested → Approved → In transit → Received → Inspected → (Pending finance) → Refunded. The return stays Open until all its items end |
| Label + QR service | Mock label, QR lookup; mock carrier event → In transit |
| Inspection & grading | A/B/C per item, partial accept; refund = sum of accepted items |
| Finance approval queue | Over ₹10k waits in Pending finance, then goes to the refund service |
| Refund service | Original method or store credit; PENDING first; same-key retry; reconcile job |
| Audit log writer | Every state change, in the same transaction |
| Dashboard & flags | Rate, reasons, cycle time; more than 3 returns in 30 d flagged |
| /metrics exporter | Refund outcome, latency |

## Per-item return state machine

Each item moves on its own. A return completes when all of its items have ended.

```
Requested → Approved → In transit → Received → Inspected (A/B/C) ─ >₹10k ─→ Pending finance ─ approved ─→ Refund pending → Refunded
               │  └───── scan, no carrier event ─────┘        └──────────── ≤ ₹10k ────────────────────────┘        │
               │                                                                                                    └─ declined ⇄ Refund failed (retry or manual follow-up)
               └─→ Expired (not shipped in N days, TBD)
Cancelled: customer cancels before Received (from Requested, Approved, In transit); label voided
Rejected:  ineligible at request, failed inspection or wrong item, or finance rejects (goods disposition TBD)
```

### Guards (accepted)

- The server rejects backward or illegal moves; each move writes audit_log in the same transaction.
- A QR scan works only in Approved or In transit, with an optimistic lock.
- Re-grading is allowed until the refund starts. A re-grade after finance approval goes back to Pending finance.
- Return window: 30 d / 10 d from the delivery date, to end of day IST.
- The ₹10,000 check uses each item's final amount after grading.
- Refund pending survives timeouts; the reconcile job settles it (ADR-001).
- Store credit uses the same refund row and guards.
- A return shows Open until every item ends, then Completed. Flagged customers are dashboard-only.

## ADR-001 · Refund idempotency

**Decision:** use both guards.
- An idempotency key (UUID per refund action), unique. A retry returns the stored result.
- One refund per return item, unique on return_item_id.

**Flow:** save PENDING → lock row → gateway call with key → gateway dedupes → ledger + Refunded + audit in one transaction.
**Timeout:** stay PENDING, retry with the same key; the reconcile job settles it.
**Out of scope v1:** corrections, or a second refund for an item.

## ADR-002 · State changes and audit (proposed)

**Decision:** every status change goes through the return state machine on the server; illegal jumps are rejected.
**Audit:** the audit_log row is written in the same transaction as the state change.
**Finance gate:** refunds over ₹10,000 move to Pending finance before any gateway call.
**Consequence:** the UI never sets status directly; it only requests a transition.

## API contract (draft, from the board)

| Method | Endpoint | Role | Purpose |
|---|---|---|---|
| POST | /auth/login | staff | Sign in, returns role token |
| GET | /orders/lookup | customer | Find order by order no. + email |
| POST | /returns | customer | Request return with items, reason, photos |
| GET | /returns/{id} | customer, staff | Status timeline + eligibility explanation |
| POST | /returns/{id}/label | system | Create mock label + QR |
| POST | /returns/{id}/inspect | agent | Grade each item A/B/C, partial accept |
| POST | /returns/{id}/approve | finance | Release refund over ₹10,000 |
| POST | /returns/{id}/refund | system | Idempotent refund, Idempotency-Key header |
| GET | /dashboard | manager | Rates, reasons, cycle time, flagged customers |
| GET | /metrics | Prometheus | Refund outcome and latency |

## Board's own gap list

- Auth & roles: login + role check for agent, finance and manager; customer access via order no. + email token.
- Architecture artifacts still to draw: **ledger schema**.

## Prototype facts the build follows

- Design tokens: `--rr-color-primary #1F5FBF`, warning `#A04A0D`, success `#1E6B3A`, bg `#F6F7F9`; Inter body, Space Grotesk display; 44/48 px touch targets; skip link, focus outline 3 px.
- Portal home → Customer "Return an item" or Staff "Sign in".
- Customer wizard, 7 steps: Find order → Verify code → Items → Reason & photo → Refund method → Review → Label & status.
  Reasons: Wrong size or fit · Arrived damaged · Not as described · Changed my mind.
- Staff demo accounts: arjun@returnright.test (agent), meera@returnright.test (finance), vikram@returnright.test (manager).
- Agent: enter/scan return code → all items marked Received → grade A/B/C + Accept/Reject per item → Save inspection.
  ≤ ₹10k goes straight to refund; > ₹10k goes to finance.
- Finance: queue + detail (return, customer, paid · grade, refund to, amount, idempotency key, flagged note, photo + agent notes);
  Approve and pay / Reject; timeout → "Retry with same key".
- Manager: period 7/30/90 d; tiles (return rate, avg cycle time, refund success rate, waiting for finance);
  return rate by category; top reasons; average time in each step; flagged customers table.
