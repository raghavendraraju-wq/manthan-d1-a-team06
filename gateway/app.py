"""Mock payment gateway: a separate small service (architecture: "Mock payment gateway").

Contract
  POST /payments              {"idempotency_key", "amount", "item_id"} -> {"status", "ref", ...}
                              Same key + same amount/item -> the stored result, never a second payment.
                              Same key + different amount/item -> 409.
  GET  /payments/{key}        stored result, or 404 if the gateway never saw the key (reconcile uses this).
  GET  /admin/behaviour       current behaviour
  PUT  /admin/behaviour       {"mode", "latency_ms", "times"} to break the gateway on purpose
  GET  /admin                 small page with the same switches
  GET  /healthz

Modes (like tests/fakes.FakeGateway):
  succeed             pays and answers
  decline             answers "declined", no payment
  timeout_after_pay   pays, then answers 504 (the answer is "lost")
  timeout_before_pay  answers 504 without paying
  error               answers 500 without paying
"times" applies the mode to that many new payments, then it reverts to succeed (null = until changed).
"latency_ms" delays every POST /payments, for the gateway-latency alert.

Payments live in SQLite (GATEWAY_DB), so dedupe survives a restart.
Run: uvicorn gateway.app:create_app --factory --port 8081
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

Mode = Literal["succeed", "decline", "timeout_after_pay", "timeout_before_pay", "error"]


class PaymentIn(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    item_id: str = Field(min_length=1, max_length=100)

    @field_validator("idempotency_key", "item_id")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class Behaviour(BaseModel):
    mode: Mode = "succeed"
    latency_ms: int = Field(default=0, ge=0, le=60_000)
    times: int | None = Field(default=None, ge=1)


class PaymentStore:
    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS payments (
                   idempotency_key TEXT PRIMARY KEY,
                   amount TEXT NOT NULL,
                   item_id TEXT NOT NULL,
                   status TEXT NOT NULL CHECK (status IN ('succeeded', 'declined')),
                   ref TEXT,
                   created_at TEXT NOT NULL)"""
        )
        self.lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        row = self._db.execute(
            "SELECT idempotency_key, amount, item_id, status, ref, created_at FROM payments WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("idempotency_key", "amount", "item_id", "status", "ref", "created_at"), row))

    def add(self, p: PaymentIn, status: str) -> dict:
        ref = None
        if status == "succeeded":
            (paid,) = self._db.execute("SELECT COUNT(*) FROM payments WHERE status = 'succeeded'").fetchone()
            ref = f"GW-{paid + 1:06d}"
        self._db.execute(
            "INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)",
            (p.idempotency_key, str(p.amount), p.item_id, status, ref, datetime.now(timezone.utc).isoformat()),
        )
        return self.get(p.idempotency_key)

    def paid_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM payments WHERE status = 'succeeded'").fetchone()[0]


def create_app(db_path: str | None = None, admin_token: str | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("GATEWAY_DB", "gateway.db")
    admin_token = admin_token if admin_token is not None else os.environ.get("GATEWAY_ADMIN_TOKEN", "")
    store = PaymentStore(db_path)
    state = {"behaviour": Behaviour()}

    app = FastAPI(title="ReturnRight mock payment gateway", version="1.0")
    app.state.store = store

    def admin_only(x_admin_token: str | None = Header(default=None)) -> None:
        if admin_token and x_admin_token != admin_token:
            raise HTTPException(status_code=401, detail="admin token required")

    def _next_mode() -> Mode:
        b: Behaviour = state["behaviour"]
        mode = b.mode
        if b.times is not None and mode != "succeed":
            left = b.times - 1
            state["behaviour"] = Behaviour(latency_ms=b.latency_ms) if left == 0 else b.model_copy(update={"times": left})
        return mode

    @app.post("/payments")
    async def pay(p: PaymentIn):
        latency = state["behaviour"].latency_ms
        if latency:
            await asyncio.sleep(latency / 1000)
        with store.lock:
            existing = store.get(p.idempotency_key)
            if existing is not None:
                if Decimal(existing["amount"]) != p.amount or existing["item_id"] != p.item_id:
                    raise HTTPException(status_code=409, detail="idempotency key reused with different details")
                return JSONResponse({**existing, "replayed": True})
            mode = _next_mode()
            if mode == "error":
                raise HTTPException(status_code=500, detail="simulated gateway error")
            if mode == "timeout_before_pay":
                raise HTTPException(status_code=504, detail="simulated timeout (not paid)")
            result = store.add(p, "declined" if mode == "decline" else "succeeded")
            if mode == "timeout_after_pay":
                raise HTTPException(status_code=504, detail="simulated timeout (paid, answer lost)")
            return JSONResponse({**result, "replayed": False})

    @app.get("/payments/{key}")
    def payment_status(key: str):
        found = store.get(key)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown idempotency key")
        return found

    @app.get("/admin/behaviour", dependencies=[Depends(admin_only)])
    def get_behaviour():
        return {**state["behaviour"].model_dump(), "payments_made": store.paid_count()}

    @app.put("/admin/behaviour", dependencies=[Depends(admin_only)])
    def set_behaviour(b: Behaviour):
        state["behaviour"] = b
        return b

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page():
        return ADMIN_PAGE

    return app


ADMIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mock gateway</title>
<style>
body{font:16px Inter,system-ui,sans-serif;margin:0;background:#F6F7F9;color:#1A1D23}
main{max-width:560px;margin:0 auto;padding:32px 16px;display:flex;flex-direction:column;gap:16px}
label{display:flex;flex-direction:column;gap:6px;font-weight:500}
select,input,button{font:inherit;min-height:44px;padding:8px 12px;border-radius:8px;border:1px solid #767E89;background:#fff}
button{background:#1F5FBF;color:#fff;border:0;font-weight:600;cursor:pointer}
pre{background:#fff;border:1px solid #DDE1E6;border-radius:8px;padding:12px;white-space:pre-wrap}
</style></head><body><main>
<h1>Mock payment gateway</h1>
<p>Break the gateway on purpose to test retries, reconcile and alerts.</p>
<label>Mode<select id="mode">
<option>succeed</option><option>decline</option><option>timeout_after_pay</option>
<option>timeout_before_pay</option><option>error</option></select></label>
<label>Latency (ms)<input id="latency" type="number" min="0" max="60000" value="0"></label>
<label>Apply to the next N payments (blank = until changed)<input id="times" type="number" min="1"></label>
<label>Admin token (if set)<input id="token" type="password" autocomplete="off"></label>
<button type="button" id="save">Apply</button>
<pre id="out" role="status" aria-live="polite"></pre>
</main><script>
const out = document.getElementById('out');
const headers = () => ({'Content-Type': 'application/json', 'X-Admin-Token': document.getElementById('token').value});
async function show(r){ out.textContent = r.ok ? JSON.stringify(await r.json(), null, 2) : 'Error ' + r.status; }
document.getElementById('save').addEventListener('click', async () => {
  const t = document.getElementById('times').value;
  show(await fetch('admin/behaviour', {method: 'PUT', headers: headers(), body: JSON.stringify({
    mode: document.getElementById('mode').value,
    latency_ms: Number(document.getElementById('latency').value || 0),
    times: t ? Number(t) : null})}));
});
fetch('admin/behaviour', {headers: headers()}).then(show);
</script></body></html>"""
