"""Per-item return state machine and refund service.

The engine applies one event at a time. A rejected event raises
(IllegalTransition, Forbidden, MalformedEvent, DuplicateRefund), leaves state
unchanged, writes no audit entry, and is recorded in rejected_events().

Refunds follow ADR-001: the item is saved as REFUND_PENDING with its idempotency
key *before* the gateway is called, no store unit is held across the call, and
the result is settled in a second unit (ledger + state + audit together).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from . import eligibility, pricing
from .contracts import (
    TERMINAL_STATES, AuditEntry, Clock, Decision, DuplicateRefund, Event, EventType, Forbidden,
    GatewayResult, GatewayTimeout, IllegalTransition, ItemState as S, LedgerEntry, MalformedEvent,
    PaymentGateway, RefundMethod, ReturnRightError, Role,
)
from .events import normalize_event, parse_event
from .flags import FlagDetector
from .store import InMemoryStore, ItemRecord, ReturnStore

# Who may send each event. Anyone else gets Forbidden.
EVENT_ROLE = {
    EventType.RETURN_REQUESTED: Role.CUSTOMER,
    EventType.CARRIER_PICKED_UP: Role.SYSTEM,
    EventType.QR_SCANNED: Role.AGENT,
    EventType.INSPECTED: Role.AGENT,
    EventType.FINANCE_APPROVED: Role.FINANCE,
    EventType.FINANCE_REJECTED: Role.FINANCE,
    EventType.REFUND_REQUESTED: Role.SYSTEM,
    EventType.CUSTOMER_CANCELLED: Role.CUSTOMER,
    EventType.EXPIRY_DUE: Role.SYSTEM,
}

# Simple moves: event -> (states it may start from, state it ends in).
SIMPLE_MOVES = {
    EventType.CARRIER_PICKED_UP: ({S.APPROVED}, S.IN_TRANSIT),
    EventType.QR_SCANNED: ({S.APPROVED, S.IN_TRANSIT}, S.RECEIVED),
    EventType.CUSTOMER_CANCELLED: ({S.REQUESTED, S.APPROVED, S.IN_TRANSIT}, S.CANCELLED),
    EventType.EXPIRY_DUE: ({S.APPROVED}, S.EXPIRED),
}

REGRADE_FROM = {S.RECEIVED, S.INSPECTED, S.PENDING_FINANCE}

# Placeholder agreed 20 Nov 2026: after a decline, one new key may be tried, then manual follow-up.
MAX_REFUND_KEYS = 2


class ReturnEngine:
    def __init__(self, gateway: PaymentGateway, clock: Clock, store: ReturnStore | None = None,
                 flags: FlagDetector | None = None, max_refund_keys: int = MAX_REFUND_KEYS) -> None:
        self.gateway = gateway
        self.clock = clock
        self.store = store if store is not None else InMemoryStore()
        self.flags = flags if flags is not None else FlagDetector(clock)
        self.max_refund_keys = max_refund_keys

    # ------------------------------------------------------------------ entry points

    def apply(self, event: Event) -> S:
        """Apply one event; return the item's state afterwards."""
        try:
            event = normalize_event(event)
            return self._dispatch(event)
        except ReturnRightError as exc:
            self.store.record_rejection(event, type(exc).__name__)
            raise

    def apply_raw(self, raw: Any) -> S:
        """Parse a raw event (events.parse_event) and apply it."""
        try:
            event = parse_event(raw)
        except MalformedEvent:
            self.store.record_rejection(raw, MalformedEvent.__name__)
            raise
        return self.apply(event)

    # ------------------------------------------------------------------ queries

    def get_item(self, item_id: str) -> ItemRecord:
        item = self.store.get_item(item_id)
        if item is None:
            raise KeyError(item_id)
        return item

    def state(self, item_id: str) -> S:
        return self.get_item(item_id).state

    def return_status(self, return_id: str) -> str:
        """'Open' until every item is in a terminal state, then 'Completed'."""
        items = self.store.items_in_return(return_id)
        if not items:
            raise KeyError(return_id)
        return "Completed" if all(i.state in TERMINAL_STATES for i in items) else "Open"

    def refund_amount(self, item_id: str) -> Decimal | None:
        return self.get_item(item_id).amount

    def rejection_rule(self, item_id: str) -> str | None:
        return self.get_item(item_id).rejection_rule

    def audit_log(self, item_id: str) -> list[AuditEntry]:
        return self.store.audit_log(item_id)

    def ledger_entries(self, item_id: str | None = None) -> list[LedgerEntry]:
        return self.store.ledger_entries(item_id)

    def rejected_events(self) -> list[tuple[Event | Any, str]]:
        """Refused events (or the raw input, if it never parsed), with the exception class name."""
        return self.store.rejected_events()

    def reconcile(self) -> int:
        """Settle REFUND_PENDING items by asking the gateway about their key. Returns how many settled."""
        settled = 0
        for item_id, key in self.store.pending_refunds():
            try:
                result = self.gateway.status(key)
            except GatewayTimeout:
                continue
            if result is None:
                continue   # gateway never saw this key: a retry with the same key will pay
            at = self.clock.now()
            before = self.state(item_id)
            after = self._settle(item_id, key, result, EventType.REFUND_REQUESTED, Role.SYSTEM, at)
            if before is S.REFUND_PENDING and after is not S.REFUND_PENDING:
                settled += 1
        return settled

    # ------------------------------------------------------------------ transitions

    @staticmethod
    def _move(unit, item: ItemRecord, to: S, event: Event) -> None:
        before = item.state
        item.state = to
        unit.put(item, AuditEntry(item.item_id, before, to, event.type, event.at, event.actor))

    def _dispatch(self, event: Event) -> S:
        if event.actor is not EVENT_ROLE[event.type]:
            raise Forbidden(f"{event.actor.value} may not send {event.type.value}")
        if event.type in (EventType.REFUND_REQUESTED, EventType.FINANCE_APPROVED):
            return self._refund(event)
        if event.type is EventType.RETURN_REQUESTED:
            return self._request(event)

        with self.store.unit(event.item_id) as unit:
            item = unit.get(event.item_id)
            if item is None:
                raise IllegalTransition(f"unknown item {event.item_id}")
            if event.type is EventType.INSPECTED:
                self._inspect(unit, item, event)
            elif event.type is EventType.FINANCE_REJECTED:
                self._require(item, {S.PENDING_FINANCE}, event)
                item.rejection_rule = "FINANCE_REJECTED"
                self._move(unit, item, S.REJECTED, event)
            else:
                allowed, to = SIMPLE_MOVES[event.type]
                self._require(item, allowed, event)
                self._move(unit, item, to, event)
            return item.state

    @staticmethod
    def _require(item: ItemRecord, allowed: set[S], event: Event) -> None:
        if item.state not in allowed:
            raise IllegalTransition(f"{event.type.value} not allowed in {item.state.value}")

    def _request(self, event: Event) -> S:
        p = event.payload
        with self.store.unit(event.item_id) as unit:
            if unit.get(event.item_id) is not None:
                raise IllegalTransition(f"item {event.item_id} already has a return")
            extra = {k: v for k, v in p.items() if k not in {
                "return_id", "customer_id", "order_no", "category", "price_inr", "delivered_at", "refund_method"}}
            item = ItemRecord(
                item_id=event.item_id, return_id=p["return_id"], customer_id=p["customer_id"],
                order_no=p["order_no"], category=p["category"], price=p["price_inr"],
                delivered_at=p["delivered_at"], refund_method=p["refund_method"],
                requested_at=event.at, extra=extra,
            )
            self._move(unit, item, S.REQUESTED, event)
            verdict = eligibility.check(item.category, item.delivered_at, event.at)
            if verdict.eligible:
                self._move(unit, item, S.APPROVED, event)
            else:
                item.rejection_rule = verdict.rule
                self._move(unit, item, S.REJECTED, event)
        if item.state is S.APPROVED:
            # Decision D9: only returns with an approved item count toward the flag.
            self.flags.record_return(item.customer_id, item.return_id, event.at)
        return item.state

    def _inspect(self, unit, item: ItemRecord, event: Event) -> None:
        self._require(item, REGRADE_FROM, event)
        grade, decision = event.payload["grade"], event.payload["decision"]
        item.grade = grade
        if decision is Decision.REJECT:
            item.amount = None
            item.rejection_rule = "INSPECTION_REJECTED"
            self._move(unit, item, S.REJECTED, event)
            return
        item.amount = pricing.refund_amount(item.price, grade)
        to = S.PENDING_FINANCE if pricing.needs_finance(item.amount) else S.INSPECTED
        self._move(unit, item, to, event)

    # ------------------------------------------------------------------ refunds (ADR-001)

    def _refund(self, event: Event) -> S:
        key = event.payload["idempotency_key"]
        with self.store.unit(event.item_id) as unit:
            item = unit.get(event.item_id)
            if item is None:
                raise IllegalTransition(f"unknown item {event.item_id}")
            state = item.state

            if state is S.REFUNDED:
                if key in item.keys:
                    return state                      # same key: stored result, no error
                raise DuplicateRefund(f"{item.item_id} is already refunded")
            if state is S.REFUND_PENDING:
                if key != item.refund_key:
                    if key in item.keys:
                        return state
                    raise DuplicateRefund(f"{item.item_id} already has a refund in progress")
                amount, retry = item.amount, True     # same key: ask the gateway again
            elif state is S.REFUND_FAILED:
                if key in item.keys:
                    return state
                if len(item.keys) >= self.max_refund_keys:
                    raise IllegalTransition(f"{item.item_id}: refund retries used up, needs manual follow-up")
                retry = False
            elif state is S.INSPECTED and event.type is EventType.REFUND_REQUESTED:
                retry = False
            elif state is S.PENDING_FINANCE and event.type is EventType.FINANCE_APPROVED:
                retry = False
            else:
                raise IllegalTransition(f"{event.type.value} not allowed in {state.value}")

            if not retry:
                item.keys.append(key)
                item.refund_key = key
                self._move(unit, item, S.REFUND_PENDING, event)
                if item.refund_method is RefundMethod.STORE_CREDIT:
                    # Store credit: same row and guards, no gateway.
                    unit.add_ledger(LedgerEntry(item.item_id, item.amount, item.refund_method, key, None))
                    self._move(unit, item, S.REFUNDED, event)
                    return item.state
            amount = item.amount

        # The PENDING row is committed; no unit is held across the gateway call.
        try:
            result = self.gateway.pay(key, amount, event.item_id)
        except GatewayTimeout:
            return self.state(event.item_id)
        return self._settle(event.item_id, key, result, event.type, event.actor, event.at)

    def _settle(self, item_id: str, key: str, result: GatewayResult,
                event_type: EventType, actor: Role, at) -> S:
        settle_event = Event(event_type, item_id, at, actor)
        with self.store.unit(item_id) as unit:
            item = unit.get(item_id)
            if item.state is not S.REFUND_PENDING or item.refund_key != key:
                return item.state                     # already settled by a racing retry/reconcile
            if result.status == "succeeded":
                item.gateway_ref = result.ref
                unit.add_ledger(LedgerEntry(item_id, item.amount, item.refund_method, key, result.ref))
                self._move(unit, item, S.REFUNDED, settle_event)
            elif result.status == "declined":
                self._move(unit, item, S.REFUND_FAILED, settle_event)
            return item.state


def build_engine(*, gateway: PaymentGateway, clock: Clock, store: ReturnStore | None = None,
                 flags: FlagDetector | None = None) -> ReturnEngine:
    return ReturnEngine(gateway, clock, store=store, flags=flags)
