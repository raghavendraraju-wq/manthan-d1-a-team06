"""PostgreSQL implementation of returnright.store.ReturnStore.

One unit = one database transaction. `get` takes a row lock (SELECT ... FOR UPDATE),
so two requests for the same item queue up instead of racing; the state change
and its audit row commit together (ADR-002). The ledger's UNIQUE constraints turn
a second refund into DuplicateRefund (ADR-001).
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from returnright.contracts import (
    AuditEntry, Category, Clock, DuplicateRefund, Event, EventType, Grade, IllegalTransition, ItemState,
    LedgerEntry, RefundMethod, Role,
)
from returnright.store import ItemRecord

from ..db.models import AuditLog, LedgerEntry as LedgerRow, RejectedEvent, ReturnItem


def to_record(row: ReturnItem) -> ItemRecord:
    extra = dict(row.extra or {})
    return ItemRecord(
        item_id=row.item_id, return_id=row.return_id, customer_id=row.customer_id, order_no=row.order_no,
        category=Category(row.category), price=row.price, delivered_at=row.delivered_at,
        refund_method=RefundMethod(row.refund_method), requested_at=row.requested_at,
        state=ItemState(row.state), grade=Grade(row.grade) if row.grade else None, amount=row.amount,
        rejection_rule=row.rejection_rule, refund_key=row.refund_key, keys=list(row.refund_keys or []),
        gateway_ref=row.gateway_ref, extra=extra,
    )


def _jsonable(extra: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v)) for k, v in extra.items()}


class _SqlUnit:
    def __init__(self, session: Session, clock: Clock) -> None:
        self.s = session
        self.clock = clock

    def get(self, item_id: str) -> ItemRecord | None:
        row = self.s.execute(
            select(ReturnItem).where(ReturnItem.item_id == item_id).with_for_update()
        ).scalar_one_or_none()
        return to_record(row) if row else None

    def put(self, item: ItemRecord, audit: AuditEntry) -> None:
        now = self.clock.now()
        row = self.s.get(ReturnItem, item.item_id)
        if row is None:
            row = ReturnItem(item_id=item.item_id, arrived_at=now, version=0,
                             order_line_id=item.extra.get("order_line_id"))
            self.s.add(row)
        row.return_id = item.return_id
        row.customer_id = item.customer_id
        row.order_no = item.order_no
        row.category = item.category.value
        row.price = item.price
        row.delivered_at = item.delivered_at
        row.refund_method = item.refund_method.value
        row.requested_at = item.requested_at
        row.state = item.state.value
        row.grade = item.grade.value if item.grade else None
        row.amount = item.amount
        row.rejection_rule = item.rejection_rule
        row.refund_key = item.refund_key
        row.refund_keys = list(item.keys)
        row.gateway_ref = item.gateway_ref
        row.reason = item.extra.get("reason")
        row.extra = _jsonable(item.extra)
        row.version = (row.version or 0) + 1
        row.updated_at = now
        self.s.flush()
        self.s.add(AuditLog(
            item_id=audit.item_id, from_state=audit.from_state.value if audit.from_state else None,
            to_state=audit.to_state.value, event_type=audit.event_type.value, actor=audit.actor.value,
            at=audit.at, recorded_at=now,
        ))

    def add_ledger(self, entry: LedgerEntry) -> None:
        try:
            with self.s.begin_nested():
                self.s.add(LedgerRow(
                    return_item_id=entry.item_id, idempotency_key=entry.idempotency_key, amount=entry.amount,
                    method=entry.method.value, gateway_ref=entry.gateway_ref, created_at=self.clock.now(),
                ))
                self.s.flush()
        except IntegrityError as exc:
            raise DuplicateRefund(f"ledger already holds a refund for {entry.item_id}") from exc


class SqlStore:
    def __init__(self, sessions: sessionmaker[Session], clock: Clock) -> None:
        self.sessions = sessions
        self.clock = clock
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _lock_for(self, item_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(item_id, threading.Lock())

    @contextmanager
    def unit(self, item_id: str) -> Iterator[_SqlUnit]:
        # Two guards: an in-process lock per item (defect DEF-003: SQLite ignores FOR UPDATE), and the
        # PostgreSQL row lock taken in get(), which also covers several API processes.
        with self._lock_for(item_id), self.sessions() as session:
            session.begin()
            try:
                yield _SqlUnit(session, self.clock)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise IllegalTransition(f"conflicting change to {item_id}") from exc
            except BaseException:
                session.rollback()
                raise

    def get_item(self, item_id: str) -> ItemRecord | None:
        with self.sessions() as s:
            row = s.get(ReturnItem, item_id)
            return to_record(row) if row else None

    def items_in_return(self, return_id: str) -> list[ItemRecord]:
        with self.sessions() as s:
            rows = s.execute(select(ReturnItem).where(ReturnItem.return_id == return_id)
                             .order_by(ReturnItem.item_id)).scalars()
            return [to_record(r) for r in rows]

    def audit_log(self, item_id: str) -> list[AuditEntry]:
        with self.sessions() as s:
            rows = s.execute(select(AuditLog).where(AuditLog.item_id == item_id).order_by(AuditLog.id)).scalars()
            return [AuditEntry(r.item_id, ItemState(r.from_state) if r.from_state else None, ItemState(r.to_state),
                               EventType(r.event_type), r.at, Role(r.actor)) for r in rows]

    def ledger_entries(self, item_id: str | None = None) -> list[LedgerEntry]:
        with self.sessions() as s:
            q = select(LedgerRow).order_by(LedgerRow.id)
            if item_id is not None:
                q = q.where(LedgerRow.return_item_id == item_id)
            return [LedgerEntry(r.return_item_id, r.amount, RefundMethod(r.method), r.idempotency_key, r.gateway_ref)
                    for r in s.execute(q).scalars()]

    def record_rejection(self, event: Event | Any, error: str) -> None:
        if isinstance(event, Event):
            item_id, etype, actor = event.item_id, str(getattr(event.type, "value", event.type)), \
                str(getattr(event.actor, "value", event.actor))
            detail = repr(event.payload)
        else:
            raw = event if isinstance(event, dict) else {}
            item_id = str(raw.get("item_id"))[:100] if raw.get("item_id") is not None else None
            etype = str(raw.get("type"))[:40] if raw.get("type") is not None else None
            actor = str(raw.get("actor"))[:20] if raw.get("actor") is not None else None
            detail = repr(event)
        with self.sessions() as s, s.begin():
            s.add(RejectedEvent(received_at=self.clock.now(), item_id=item_id, event_type=etype, actor=actor,
                                error=error, detail=detail[:2000]))

    def rejected_events(self) -> list[tuple[dict, str]]:
        with self.sessions() as s:
            rows = s.execute(select(RejectedEvent).order_by(RejectedEvent.id)).scalars()
            return [({"item_id": r.item_id, "type": r.event_type, "actor": r.actor, "detail": r.detail,
                      "received_at": r.received_at}, r.error) for r in rows]

    def pending_refunds(self) -> list[tuple[str, str]]:
        with self.sessions() as s:
            rows = s.execute(select(ReturnItem.item_id, ReturnItem.refund_key)
                             .where(ReturnItem.state == ItemState.REFUND_PENDING.value)).all()
            return [(i, k) for i, k in rows if k]

    def arrivals(self) -> list[tuple[str, str, datetime]]:
        """(customer_id, return_id, first arrival) for returns with an item that was approved (decision D9)."""
        with self.sessions() as s:
            approved = select(AuditLog.item_id).where(AuditLog.to_state == ItemState.APPROVED.value)
            rows = s.execute(select(ReturnItem.customer_id, ReturnItem.return_id, ReturnItem.arrived_at)
                             .where(ReturnItem.item_id.in_(approved))).all()
        first: dict[tuple[str, str], datetime] = {}
        for customer, ret, at in rows:
            key = (customer, ret)
            first[key] = min(at, first.get(key, at))
        return [(c, r, at) for (c, r), at in first.items()]
