"""HTTP client for the mock payment gateway; implements returnright.contracts.PaymentGateway.

Any answer that doesn't say for certain whether money moved (timeout, 5xx, 409,
connection failure) raises GatewayTimeout, so the engine keeps the item in
REFUND_PENDING and a same-key retry or the reconcile job settles it (ADR-001).

`observe(outcome, seconds)` is called once per pay() with outcome
succeeded | declined | timeout | error; the metrics exporter hooks in here.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal

import httpx

from returnright.contracts import GatewayResult, GatewayTimeout

Observer = Callable[[str, float], None]


class HttpGateway:
    def __init__(self, base_url: str, *, timeout_s: float = 8.0, client: httpx.Client | None = None,
                 observe: Observer | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._observe = observe or (lambda outcome, seconds: None)

    def pay(self, idempotency_key: str, amount: Decimal, item_id: str) -> GatewayResult:
        started = time.perf_counter()
        outcome = "error"
        try:
            try:
                response = self._client.post(
                    "/payments", json={"idempotency_key": idempotency_key, "amount": str(amount), "item_id": item_id})
            except httpx.TimeoutException as exc:
                outcome = "timeout"
                raise GatewayTimeout(idempotency_key) from exc
            except httpx.HTTPError as exc:
                raise GatewayTimeout(idempotency_key) from exc
            if response.status_code == 504:
                outcome = "timeout"
                raise GatewayTimeout(idempotency_key)
            if response.status_code != 200:
                raise GatewayTimeout(f"{idempotency_key}: gateway answered {response.status_code}")
            result = self._result(response.json())
            outcome = result.status
            return result
        finally:
            self._observe(outcome, time.perf_counter() - started)

    def status(self, idempotency_key: str) -> GatewayResult | None:
        try:
            response = self._client.get(f"/payments/{idempotency_key}")
        except httpx.HTTPError as exc:
            raise GatewayTimeout(idempotency_key) from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GatewayTimeout(f"{idempotency_key}: gateway answered {response.status_code}")
        return self._result(response.json())

    @staticmethod
    def _result(body: dict) -> GatewayResult:
        status = body.get("status")
        if status not in ("succeeded", "declined"):
            raise GatewayTimeout(f"unexpected gateway status {status!r}")
        return GatewayResult(status, body.get("ref"))
