"""Test doubles: a controllable clock, a mock payment gateway and an email sender."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from decimal import Decimal

from returnright.contracts import GatewayResult, GatewayTimeout


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)

    def set(self, when: datetime) -> None:
        self._now = when


class FakeGateway:
    """Behaves like the agreed mock gateway: dedupes on idempotency key.

    mode:
      "succeed"              pays and answers
      "decline"              answers declined, no payment
      "timeout_after_pay"    pays, then the answer is lost (GatewayTimeout); next call succeeds
      "timeout_before_pay"   no payment, GatewayTimeout; next call succeeds
    """

    def __init__(self) -> None:
        self.mode = "succeed"
        self.calls: list[tuple[str, Decimal, str]] = []
        self.payments: list[tuple[str, Decimal, str]] = []
        self._results: dict[str, GatewayResult] = {}
        self._lock = threading.Lock()

    def pay(self, idempotency_key: str, amount: Decimal, item_id: str) -> GatewayResult:
        with self._lock:
            self.calls.append((idempotency_key, amount, item_id))
            if idempotency_key in self._results:
                return self._results[idempotency_key]
            if self.mode == "decline":
                result = GatewayResult("declined", None)
                self._results[idempotency_key] = result
                return result
            if self.mode == "timeout_before_pay":
                self.mode = "succeed"
                raise GatewayTimeout(idempotency_key)
            ref = f"GW-{len(self.payments) + 1:04d}"
            self.payments.append((idempotency_key, amount, item_id))
            result = GatewayResult("succeeded", ref)
            self._results[idempotency_key] = result
            if self.mode == "timeout_after_pay":
                self.mode = "succeed"
                raise GatewayTimeout(idempotency_key)
            return result

    def status(self, idempotency_key: str) -> GatewayResult | None:
        with self._lock:
            return self._results.get(idempotency_key)


class FakeEmail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    def last_code(self) -> str:
        import re

        _, _, body = self.sent[-1]
        match = re.search(r"\b(\d{6})\b", body)
        assert match, "email body should contain a 6-digit code"
        return match.group(1)
