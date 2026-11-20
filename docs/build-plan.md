# ReturnRight – build plan (draft for review)

Status: **draft, nothing built yet.** Sources: Track A brief (Drive), `docs/architecture.md` (whiteboard export),
the click-through prototype, and the 143 tests in `tests/`. Items marked **❓** need a decision before or during the step
that uses them. Each one has a recommendation.

## 1. Target layout

```
returnright/          domain package (pure Python, no I/O) – the tests' contract
backend/              FastAPI app: api/, db/ (SQLAlchemy + Alembic), adapters/ (Postgres store, gateway client, mailer), jobs/, seed/
gateway/              mock payment gateway – separate small FastAPI service with its own SQLite file
frontend/             React 19 + Vite SPA
monitoring/           alerts.rules.yml (+ tests), prometheus.yml, Grafana provisioning + dashboard JSON
tests/                existing domain tests (unchanged) + tests/api/, tests/gateway/
e2e/                  Playwright: the brief's 10 cases through the UI
docs/                 architecture.md, build-plan.md, runbook.md, adr/, defect-log.md
docker-compose.yml    web, api, gateway, db, prometheus, grafana
.github/workflows/    ci.yml
```

## 2. Key design: one engine, two stores

The tests drive `ReturnEngine` in memory. The app must keep the same rules but persist them in PostgreSQL, with the
audit row in the same transaction (ADR-002) and PENDING saved before the gateway call (ADR-001). Plan:

- `ReturnEngine` holds **only transition logic** and talks to a `ReturnStore` port:
  `load_item_for_update`, `save_transition(item, audit)`, `add_ledger`, `record_rejection`, `pending_refunds`.
- `InMemoryStore` (default, used by `build_engine`) keeps the tests exactly as they are.
- `PostgresStore` (backend) does each `apply()` in one transaction: `SELECT … FOR UPDATE` on the item row, state change and
  audit row together. Refund flow: **txn 1** item → Refund pending with its key, commit → gateway call (no transaction open)
  → **txn 2** ledger row + Refunded + audit. A crash between the two leaves Refund pending, and the reconcile job settles it.
- DB constraints back the code: `ledger.return_item_id UNIQUE`, `ledger.idempotency_key UNIQUE`, `audit_log` append-only.

## 3. Rules fixed by the tests / README (no decision needed)

Windows to end of day IST (30 d / 10 d); innerwear and perishables never returnable; grade C = 50 %; finance when an item's
final amount > ₹10,000; re-grade allowed until the refund starts; same key returns the stored result; new key on a refunded
item → `DuplicateRefund`; flags by arrival time, > 3 in 30 d, never block; refused events write no audit and go to
`rejected_events()`; only agent scans/grades, only finance approves; OTP: 6 digits, a new one voids the old, one use only.

## 4. Placeholders from your context packet (applied as config, easy to change)

| Item | Value | Where |
|---|---|---|
| Unshipped return expires after | **14 days** in Approved | `EXPIRY_DAYS`, expiry job |
| One-time code valid for | **10 minutes** | `OTP_TTL_MINUTES` |
| Retry after a gateway decline | **1 new key allowed** after Refund failed, then manual follow-up | engine rule + un-skip test |
| Tokens | **JWT held in memory** in the SPA (not localStorage); a page reload means signing in again | backend + SPA |

## 5. Steps (commit after each verified step)

| # | Step | Done when |
|---|---|---|
| 0 | Toolchain + `CLAUDE.md` + `.gitignore` | Python ✔ Git ✔; **Node and Docker Desktop still to install (need admin)** |
| 1 | Domain: eligibility, pricing, events, flags, access, engine (+ store port, `InMemoryStore`) | `pytest` → 138 pass, 5 skip (alerts need promtool/Docker) |
| 2 | Mock gateway service: `POST /payments`, `GET /payments/{key}`, admin modes succeed/decline/timeout_after_pay/timeout_before_pay/error + latency | gateway tests pass; dedupes on key across restarts |
| 3 | DB: SQLAlchemy models, Alembic migration, ledger schema (draws the board's missing ledger box), Faker seed (2,000 orders / 40 products / 500 customers + demo order RR-10482 + staff accounts) | migration + seed run against PG 18 |
| 4 | API: auth (staff login, customer OTP → JWT), customer, agent, finance, manager, `/metrics`, reconcile + expiry + mock-carrier jobs | API tests (pytest + httpx) pass, incl. role guard on every route |
| 5 | SPA: React 19 + Vite following the prototype screens and tokens | the 7-step wizard works at phone width (390 px); all four staff/customer views work |
| 6 | Compose: web (nginx serving the SPA, proxying `/api`), api, gateway, db, prometheus, grafana; runbook | clean `docker compose up` on a fresh clone; alerts fire when the gateway is broken |
| 7 | CI: lint, pytest, promtool, Playwright, build images | green run |
| 8 | Playwright: the 10 e2e cases through the UI; defect log; `readme.html` | 10/10 pass |

## 6. Decisions (all accepted as recommended, 20 Nov 2026)

| # | Question | Decision |
|---|---|---|
| D1 | The board's API is per **return** (`/returns/{id}/approve`), but state, finance and refunds are per **item**. | Per-item actions: `POST /returns/{id}/items/{item_id}/approve`. Inspection stays one call per return (one form). |
| D2 | Who starts a ≤ ₹10k refund? (Tests use a system `REFUND_REQUESTED`; the prototype shows "goes straight to refund".) | The server raises it right after an accepted inspection that lands in Inspected; key = UUID made when the agent's form loads. |
| D3 | How does the **mock carrier** fire pickup? | Demo-mode job picks up Approved items after `CARRIER_DELAY` (default 2 min), plus `POST /carrier/pickup` for tests. |
| D4 | "Download label (PDF)" | A printable label page (QR as server-side SVG) that uses the browser's Save as PDF. No PDF library. |
| D5 | Staff passwords for the 3 demo accounts | Seeded from env (`DEMO_STAFF_PASSWORD`, default `demo-pass`), hashed with argon2/bcrypt. |
| D6 | OTP delivery in Compose | **Mailpit** container (catches mail, web UI on :8025). Without it, the code is only logged. |
| D7 | Dashboard categories: the prototype shows Accessories / Home & kitchen / Beauty; `contracts.Category` has footwear, home, innerwear, perishables. | Follow `contracts.Category` (6 categories); relabel the dashboard. |
| D8 | Extra tools beyond the board: Alembic, Mailpit, ruff (lint), React Router, Python `qrcode`, argon2. The board says "tools from the prerequisites guide only". | Confirm these are allowed, or point me to the prerequisites guide. |
| D9 | Should returns rejected at request (ineligible) count toward the > 3 flag? | Count only return requests with at least one approved item. |
| D10 | Image versions | `postgres:18`, `python:3.13-slim`, `node:<current LTS>-alpine` for the build stage, `nginx:stable-alpine`, `prom/prometheus:v3.5.0` (as the tests), `grafana/grafana` pinned. |

## 7. Risks

- Node and Docker are not installed here (both need an admin prompt). Steps 5–8 can't be verified on this machine until they are.
- The engine refactor (store port) must keep all 143 tests unchanged. They are the contract.
