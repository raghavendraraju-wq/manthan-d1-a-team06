"""Storage port for the return engine.

The engine holds only transition logic; a store keeps items, the audit trail, the
refund ledger and refused events. A *unit* is one atomic change to one item:
it locks the item for its duration, and its writes land together on success or
not at all if the block raises (ADR-002: state change and audit row together).

InMemoryStore backs the tests; the backend provides a PostgreSQL store with the
same methods (row lock = SELECT ... FOR UPDATE, unit = one transaction).
"""
from __future__ import annotations

import copy
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from .contracts import (
    AuditEntry, Category, DuplicateRefund, Event, Grade, ItemState, LedgerEntry, RefundMethod,
)


@dataclass
class ItemRecord:
    item_id: str
    return_id: str
    customer_id: str
    order_no: str
    category: Category
    price: Decimal
    delivered_at: datetime
    refund_method: RefundMethod
    requested_at: datetime
    state: ItemState | None = None
    grade: Grade | None = None
    amount: Decimal | None = None          # final refund amount after grading
    rejection_rule: str | None = None
    refund_key: str | None = None          # key of the refund attempt in flight / last made
    keys: list[str] = field(default_factory=list)   # every key used for this item, oldest first
    gateway_ref: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # app data from the request (reason, photo, ...)


class Unit(Protocol):
    def get(self, item_id: str) -> ItemRecord | None: ...
    def put(self, item: ItemRecord, audit: AuditEntry) -> None: ...
    def add_ledger(self, entry: LedgerEntry) -> None:
        """Raises DuplicateRefund if the item or the key already has a ledger entry."""
        ...


class ReturnStore(Protocol):
    def unit(self, item_id: str) -> Any:
        """Context manager yielding a Unit; commits on normal exit, discards on exception."""
        ...

    def get_item(self, item_id: str) -> ItemRecord | None: ...
    def items_in_return(self, return_id: str) -> list[ItemRecord]: ...
    def audit_log(self, item_id: str) -> list[AuditEntry]: ...
    def ledger_entries(self, item_id: str | None = None) -> list[LedgerEntry]: ...
    def record_rejection(self, event: Event | Any, error: str) -> None: ...
    def rejected_events(self) -> list[tuple[Event | Any, str]]: ...
    def pending_refunds(self) -> list[tuple[str, str]]:
        """(item_id, refund_key) for every item in REFUND_PENDING."""
        ...


class _MemoryUnit:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self._items: dict[str, ItemRecord] = {}
        self._audit: list[AuditEntry] = []
        self._ledger: list[LedgerEntry] = []

    def get(self, item_id: str) -> ItemRecord | None:
        if item_id in self._items:
            return copy.deepcopy(self._items[item_id])
        return self._store.get_item(item_id)

    def put(self, item: ItemRecord, audit: AuditEntry) -> None:
        self._items[item.item_id] = copy.deepcopy(item)
        self._audit.append(audit)

    def add_ledger(self, entry: LedgerEntry) -> None:
        existing = self._store.ledger_entries() + self._ledger
        if any(e.item_id == entry.item_id or e.idempotency_key == entry.idempotency_key for e in existing):
            raise DuplicateRefund(f"ledger already holds a refund for {entry.item_id}")
        self._ledger.append(entry)

    def _commit(self) -> None:
        with self._store._guard:
            self._store._items.update(self._items)
            for entry in self._audit:
                self._store._audit[entry.item_id].append(entry)
            self._store._ledger.extend(self._ledger)


class InMemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, ItemRecord] = {}
        self._audit: dict[str, list[AuditEntry]] = defaultdict(list)
        self._ledger: list[LedgerEntry] = []
        self._rejected: list[tuple[Event | Any, str]] = []
        self._guard = threading.RLock()
        self._locks: dict[str, threading.Lock] = {}

    def _lock_for(self, item_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(item_id, threading.Lock())

    @contextmanager
    def unit(self, item_id: str) -> Iterator[_MemoryUnit]:
        with self._lock_for(item_id):
            unit = _MemoryUnit(self)
            yield unit
            unit._commit()

    def get_item(self, item_id: str) -> ItemRecord | None:
        with self._guard:
            item = self._items.get(item_id)
            return copy.deepcopy(item) if item else None

    def items_in_return(self, return_id: str) -> list[ItemRecord]:
        with self._guard:
            return [copy.deepcopy(i) for i in self._items.values() if i.return_id == return_id]

    def audit_log(self, item_id: str) -> list[AuditEntry]:
        with self._guard:
            return list(self._audit.get(item_id, []))

    def ledger_entries(self, item_id: str | None = None) -> list[LedgerEntry]:
        with self._guard:
            return [e for e in self._ledger if item_id is None or e.item_id == item_id]

    def record_rejection(self, event: Event | Any, error: str) -> None:
        with self._guard:
            self._rejected.append((event, error))

    def rejected_events(self) -> list[tuple[Event | Any, str]]:
        with self._guard:
            return list(self._rejected)

    def pending_refunds(self) -> list[tuple[str, str]]:
        with self._guard:
            return [(i.item_id, i.refund_key) for i in self._items.values()
                    if i.state is ItemState.REFUND_PENDING and i.refund_key]
