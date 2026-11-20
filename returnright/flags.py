"""Flagged-customer detector (stub).

A customer with more than 3 returns in the last 30 days is flagged for review.
Agreed: returns count by when the event ARRIVED (processing time, clock.now()
at record time), not by the event's own timestamp. The flag is dashboard-only
and never blocks a return. Recording the same return_id twice counts once.
"""
from __future__ import annotations

from datetime import datetime

from .contracts import Clock

_MSG = "FlagDetector is not built yet"


class FlagDetector:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def record_return(self, customer_id: str, return_id: str, event_time: datetime) -> None:
        raise NotImplementedError(_MSG)

    def count(self, customer_id: str) -> int:
        raise NotImplementedError(_MSG)

    def is_flagged(self, customer_id: str) -> bool:
        raise NotImplementedError(_MSG)
