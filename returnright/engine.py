"""Per-item return state machine and refund service (stub).

The engine applies one event at a time. A rejected event raises
(IllegalTransition, Forbidden, MalformedEvent, DuplicateRefund), leaves state
unchanged, writes no audit entry, and is recorded in rejected_events().
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .contracts import AuditEntry, Clock, Event, ItemState, LedgerEntry, PaymentGateway

_MSG = "ReturnEngine is not built yet"


class ReturnEngine:
    def __init__(self, gateway: PaymentGateway, clock: Clock) -> None:
        self.gateway = gateway
        self.clock = clock

    def apply(self, event: Event) -> ItemState:
        """Apply one event; return the item's state afterwards."""
        raise NotImplementedError(_MSG)

    def apply_raw(self, raw: Any) -> ItemState:
        """Parse a raw event (events.parse_event) and apply it."""
        raise NotImplementedError(_MSG)

    def state(self, item_id: str) -> ItemState:
        raise NotImplementedError(_MSG)

    def return_status(self, return_id: str) -> str:
        """'Open' until every item is in a terminal state, then 'Completed'."""
        raise NotImplementedError(_MSG)

    def refund_amount(self, item_id: str) -> Decimal | None:
        raise NotImplementedError(_MSG)

    def rejection_rule(self, item_id: str) -> str | None:
        raise NotImplementedError(_MSG)

    def audit_log(self, item_id: str) -> list[AuditEntry]:
        raise NotImplementedError(_MSG)

    def ledger_entries(self, item_id: str | None = None) -> list[LedgerEntry]:
        raise NotImplementedError(_MSG)

    def rejected_events(self) -> list[tuple[Event | Any, str]]:
        """Refused events (or the raw input, if it never parsed), with the exception class name."""
        raise NotImplementedError(_MSG)

    def reconcile(self) -> int:
        """Settle REFUND_PENDING items by asking the gateway about their key. Returns how many settled."""
        raise NotImplementedError(_MSG)


def build_engine(*, gateway: PaymentGateway, clock: Clock) -> ReturnEngine:
    return ReturnEngine(gateway, clock)
