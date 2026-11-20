"""Persona-level access (stub).

Staff see only their own role's screens. Customers reach their order with
order number + email, then a 6-digit one-time code sent to that email.
"""
from __future__ import annotations

from .contracts import Clock, EmailSender, Role, Screen

_MSG = "access is not built yet"


def can_access(role: Role, screen: Screen) -> bool:
    raise NotImplementedError(_MSG)


class OtpService:
    def __init__(self, clock: Clock, sender: EmailSender, orders: dict[str, str]) -> None:
        """orders maps order number -> email on the order."""
        self.clock = clock
        self.sender = sender
        self.orders = orders

    def issue(self, order_no: str, email: str) -> None:
        """Send a new code if order and email match. Sends nothing otherwise. A new code voids earlier ones."""
        raise NotImplementedError(_MSG)

    def verify(self, order_no: str, email: str, code: str) -> bool:
        """True once for the latest valid code; a used code never works again."""
        raise NotImplementedError(_MSG)
