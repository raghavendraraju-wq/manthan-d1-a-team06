"""Persona-level access.

Staff see only their own role's screens. Customers reach their order with
order number + email, then a 6-digit one-time code sent to that email.
"""
from __future__ import annotations

import hmac
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import Clock, EmailSender, Role, Screen

OWN_SCREEN = {
    Role.CUSTOMER: Screen.CUSTOMER_WIZARD,
    Role.AGENT: Screen.SCAN_AND_GRADE,
    Role.FINANCE: Screen.APPROVAL_QUEUE,
    Role.MANAGER: Screen.DASHBOARD,
}

OTP_TTL = timedelta(minutes=10)      # placeholder agreed 20 Nov 2026
OTP_MAX_ATTEMPTS = 5


def can_access(role: Role, screen: Screen) -> bool:
    return OWN_SCREEN.get(role) == screen


def normalize_email(email: str) -> str:
    return email.strip().lower()


@dataclass
class _Code:
    email: str
    code: str
    issued_at: datetime
    used: bool = False
    failures: int = 0


class OtpService:
    def __init__(self, clock: Clock, sender: EmailSender, orders: Mapping[str, str],
                 ttl: timedelta = OTP_TTL, max_attempts: int = OTP_MAX_ATTEMPTS) -> None:
        """orders maps order number -> email on the order."""
        self.clock = clock
        self.sender = sender
        self.orders = orders
        self.ttl = ttl
        self.max_attempts = max_attempts
        self._codes: dict[str, _Code] = {}
        self._lock = threading.Lock()

    def _matches(self, order_no: str, email: str) -> bool:
        on_order = self.orders.get(order_no.strip())
        return on_order is not None and normalize_email(on_order) == normalize_email(email)

    def issue(self, order_no: str, email: str) -> bool:
        """Send a new code if order and email match. Sends nothing otherwise. A new code voids earlier ones.

        Returns whether a code was sent (callers must not reveal this to the customer)."""
        if not self._matches(order_no, email):
            return False
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            self._codes[order_no.strip()] = _Code(normalize_email(email), code, self.clock.now())
        minutes = int(self.ttl.total_seconds() // 60)
        self.sender.send(
            normalize_email(email),
            f"Your ReturnRight code for order {order_no.strip()}",
            f"Your one-time code is {code}. It works once and expires in {minutes} minutes.",
        )
        return True

    def verify(self, order_no: str, email: str, code: str) -> bool:
        """True once for the latest valid code; a used code never works again."""
        with self._lock:
            entry = self._codes.get(order_no.strip())
            if entry is None or entry.used or entry.email != normalize_email(email):
                return False
            if self.clock.now() - entry.issued_at > self.ttl or entry.failures >= self.max_attempts:
                return False
            if not hmac.compare_digest(entry.code, str(code).strip()):
                entry.failures += 1
                return False
            entry.used = True
            return True
