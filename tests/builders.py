"""Crafted event builders. Times are IST; 'now' matches the prototype (20 Nov 2026)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from returnright.contracts import (
    IST, Category, Decision, Event, EventType, Grade, RefundMethod, Role,
)

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=IST)
DELIVERED = datetime(2026, 11, 12, 15, 0, tzinfo=IST)


def requested(item, *, ret="RTN-20481", customer="C-0001", category=Category.APPAREL,
              price="1899", method=RefundMethod.ORIGINAL, delivered=DELIVERED, at=NOW):
    return Event(EventType.RETURN_REQUESTED, item, at, Role.CUSTOMER, {
        "return_id": ret, "customer_id": customer, "order_no": "RR-10482",
        "category": category, "price_inr": Decimal(price), "delivered_at": delivered,
        "refund_method": method,
    })


def picked_up(item, at=NOW):
    return Event(EventType.CARRIER_PICKED_UP, item, at, Role.SYSTEM)


def scanned(item, at=NOW, actor=Role.AGENT):
    return Event(EventType.QR_SCANNED, item, at, actor)


def inspected(item, grade="A", decision="accept", at=NOW, actor=Role.AGENT):
    return Event(EventType.INSPECTED, item, at, actor,
                 {"grade": Grade(grade), "decision": Decision(decision)})


def finance_approved(item, key, at=NOW, actor=Role.FINANCE):
    return Event(EventType.FINANCE_APPROVED, item, at, actor, {"idempotency_key": key})


def finance_rejected(item, at=NOW, actor=Role.FINANCE):
    return Event(EventType.FINANCE_REJECTED, item, at, actor)


def refund_requested(item, key, at=NOW):
    return Event(EventType.REFUND_REQUESTED, item, at, Role.SYSTEM, {"idempotency_key": key})


def cancelled(item, at=NOW):
    return Event(EventType.CUSTOMER_CANCELLED, item, at, Role.CUSTOMER)


def expiry_due(item, at=NOW):
    return Event(EventType.EXPIRY_DUE, item, at, Role.SYSTEM)


def to_received(engine, item, **req):
    engine.apply(requested(item, **req))
    engine.apply(picked_up(item))
    return engine.apply(scanned(item))


def to_inspected(engine, item, grade="A", **req):
    to_received(engine, item, **req)
    return engine.apply(inspected(item, grade=grade))


def raw(type_, item="ITM-1", at="2026-11-20T10:00:00+05:30", actor="agent", payload=None):
    """A well-formed raw event dict; tests break one field at a time."""
    return {"type": type_, "item_id": item, "at": at, "actor": actor, "payload": payload or {}}
