"""Event parsing and validation.

parse_event turns a raw JSON-style dict into an Event, or raises MalformedEvent.
Raw shape: {"type": str, "item_id": str, "at": ISO-8601 with offset,
            "actor": role value, "payload": dict}

normalize_event validates an Event built in code (typed or string payload values)
and returns it with a typed payload. The engine runs it on every event.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, TypeVar

from .contracts import (
    Category, Decision, Event, EventType, Grade, MalformedEvent, RefundMethod, Role,
)

MAX_ID_LEN = 100
MAX_KEY_LEN = 200
_E = TypeVar("_E", bound=Enum)


def _enum(cls: type[_E], value: Any, field: str) -> _E:
    if isinstance(value, cls):
        return value
    if not isinstance(value, str):
        raise MalformedEvent(f"{field} must be one of {[m.value for m in cls]}")
    try:
        return cls(value)
    except ValueError:
        raise MalformedEvent(f"{field}: unknown value {value!r}") from None


def _text(value: Any, field: str, max_len: int = MAX_ID_LEN) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MalformedEvent(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > max_len:
        raise MalformedEvent(f"{field} is longer than {max_len} characters")
    return value


def _when(value: Any, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise MalformedEvent(f"{field} is not an ISO-8601 timestamp") from None
    if not isinstance(value, datetime):
        raise MalformedEvent(f"{field} must be a timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        raise MalformedEvent(f"{field} must carry a UTC offset")
    return value


def _price(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise MalformedEvent("price_inr must be a number")
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise MalformedEvent("price_inr must be a number") from None
    if not amount.is_finite() or amount <= 0:
        raise MalformedEvent("price_inr must be more than zero")
    if amount.as_tuple().exponent < -2:
        raise MalformedEvent("price_inr has more than 2 decimal places")
    return amount


_REQUEST_FIELDS = ("return_id", "customer_id", "order_no", "category", "price_inr", "delivered_at", "refund_method")


def _payload(type_: EventType, payload: Any) -> dict[str, Any]:
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise MalformedEvent("payload must be an object")
    p = dict(payload)

    if type_ is EventType.RETURN_REQUESTED:
        missing = [f for f in _REQUEST_FIELDS if f not in p]
        if missing:
            raise MalformedEvent(f"payload is missing {', '.join(missing)}")
        p["return_id"] = _text(p["return_id"], "return_id")
        p["customer_id"] = _text(p["customer_id"], "customer_id")
        p["order_no"] = _text(p["order_no"], "order_no")
        p["category"] = _enum(Category, p["category"], "category")
        p["price_inr"] = _price(p["price_inr"])
        p["delivered_at"] = _when(p["delivered_at"], "delivered_at")
        p["refund_method"] = _enum(RefundMethod, p["refund_method"], "refund_method")
    elif type_ is EventType.INSPECTED:
        if "grade" not in p or "decision" not in p:
            raise MalformedEvent("INSPECTED needs grade and decision")
        p["grade"] = _enum(Grade, p["grade"], "grade")
        p["decision"] = _enum(Decision, p["decision"], "decision")
    elif type_ in (EventType.REFUND_REQUESTED, EventType.FINANCE_APPROVED):
        p["idempotency_key"] = _text(p.get("idempotency_key"), "idempotency_key", MAX_KEY_LEN)
    return p


def normalize_event(event: Event) -> Event:
    if not isinstance(event, Event):
        raise MalformedEvent("not an Event")
    type_ = _enum(EventType, event.type, "type")
    return replace(
        event,
        type=type_,
        item_id=_text(event.item_id, "item_id"),
        at=_when(event.at, "at"),
        actor=_enum(Role, event.actor, "actor"),
        payload=_payload(type_, event.payload),
    )


def parse_event(raw: Any) -> Event:
    if not isinstance(raw, Mapping):
        raise MalformedEvent("event must be an object")
    for field in ("type", "item_id", "at", "actor"):
        if field not in raw:
            raise MalformedEvent(f"event is missing {field}")
    type_ = _enum(EventType, raw["type"], "type")
    return Event(
        type=type_,
        item_id=_text(raw["item_id"], "item_id"),
        at=_when(raw["at"], "at"),
        actor=_enum(Role, raw["actor"], "actor"),
        payload=_payload(type_, raw.get("payload")),
    )
