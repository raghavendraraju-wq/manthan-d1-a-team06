"""Shared types for ReturnRight. These are the contract the tests rely on.

Only data types, enums, exceptions and protocols live here: no business logic.
The logic modules (eligibility, pricing, engine, events, flags, access) are
stubs that raise NotImplementedError until the team builds them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

IST = timezone(timedelta(hours=5, minutes=30), "IST")


class ItemState(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    INSPECTED = "inspected"
    PENDING_FINANCE = "pending_finance"
    REFUND_PENDING = "refund_pending"
    REFUND_FAILED = "refund_failed"
    REFUNDED = "refunded"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATES = {ItemState.REFUNDED, ItemState.REJECTED, ItemState.CANCELLED, ItemState.EXPIRED}


class Category(str, Enum):
    APPAREL = "apparel"
    FOOTWEAR = "footwear"
    ELECTRONICS = "electronics"
    HOME = "home"
    INNERWEAR = "innerwear"
    PERISHABLES = "perishables"


class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class Decision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


class RefundMethod(str, Enum):
    ORIGINAL = "original_payment"
    STORE_CREDIT = "store_credit"


class Role(str, Enum):
    CUSTOMER = "customer"
    AGENT = "agent"
    FINANCE = "finance"
    MANAGER = "manager"
    SYSTEM = "system"


class EventType(str, Enum):
    RETURN_REQUESTED = "RETURN_REQUESTED"    # customer; payload: return_id, customer_id, order_no, category, price_inr, delivered_at, refund_method
    CARRIER_PICKED_UP = "CARRIER_PICKED_UP"  # system (mock carrier)
    QR_SCANNED = "QR_SCANNED"                # agent
    INSPECTED = "INSPECTED"                  # agent; payload: grade, decision
    FINANCE_APPROVED = "FINANCE_APPROVED"    # finance; payload: idempotency_key (approval starts the refund)
    FINANCE_REJECTED = "FINANCE_REJECTED"    # finance
    REFUND_REQUESTED = "REFUND_REQUESTED"    # system, for refunds <= INR 10,000; payload: idempotency_key
    CUSTOMER_CANCELLED = "CUSTOMER_CANCELLED"  # customer
    EXPIRY_DUE = "EXPIRY_DUE"                # system; N days is still TBD


class Screen(str, Enum):
    CUSTOMER_WIZARD = "customer_wizard"
    SCAN_AND_GRADE = "scan_and_grade"
    APPROVAL_QUEUE = "approval_queue"
    DASHBOARD = "dashboard"


@dataclass(frozen=True)
class Event:
    type: EventType
    item_id: str
    at: datetime            # timezone-aware
    actor: Role
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    rule: str               # STANDARD_WINDOW | ELECTRONICS_WINDOW | NON_RETURNABLE_CATEGORY | WINDOW_EXPIRED
    reason: str             # customer-facing explanation
    last_day: date | None   # last eligible day (IST), None if never returnable
    days_left: int | None


@dataclass(frozen=True)
class AuditEntry:
    item_id: str
    from_state: ItemState | None
    to_state: ItemState
    event_type: EventType
    at: datetime
    actor: Role


@dataclass(frozen=True)
class LedgerEntry:
    item_id: str
    amount: Decimal
    method: RefundMethod
    idempotency_key: str
    gateway_ref: str | None  # None for store credit


@dataclass(frozen=True)
class GatewayResult:
    status: str             # "succeeded" | "declined"
    ref: str | None


class ReturnRightError(Exception):
    pass


class MalformedEvent(ReturnRightError):
    pass


class IllegalTransition(ReturnRightError):
    pass


class Forbidden(ReturnRightError):
    pass


class DuplicateRefund(ReturnRightError):
    """A second refund for a return item that already has one (per-item unique rule)."""


class GatewayTimeout(ReturnRightError):
    pass


class Clock(Protocol):
    def now(self) -> datetime: ...


class PaymentGateway(Protocol):
    def pay(self, idempotency_key: str, amount: Decimal, item_id: str) -> GatewayResult:
        """Raises GatewayTimeout when no answer arrives in time. Must dedupe on the key."""
        ...

    def status(self, idempotency_key: str) -> GatewayResult | None: ...


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...
