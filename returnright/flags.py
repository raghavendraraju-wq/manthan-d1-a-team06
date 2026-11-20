"""Flagged-customer detector.

A customer with more than 3 returns in the last 30 days is flagged for review.
Agreed: returns count by when the event ARRIVED (processing time, clock.now()
at record time), not by the event's own timestamp. The flag is dashboard-only
and never blocks a return. Recording the same return_id twice counts once.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from .contracts import Clock

WINDOW = timedelta(days=30)
FLAG_ABOVE = 3


class FlagDetector:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._arrivals: dict[str, dict[str, datetime]] = {}
        self._lock = threading.Lock()

    def record_return(self, customer_id: str, return_id: str, event_time: datetime) -> None:
        # event_time is deliberately ignored: the window runs on arrival time.
        with self._lock:
            self._arrivals.setdefault(customer_id, {}).setdefault(return_id, self.clock.now())

    def restore(self, customer_id: str, return_id: str, arrived_at: datetime) -> None:
        """Reload a persisted arrival (app start-up)."""
        with self._lock:
            self._arrivals.setdefault(customer_id, {}).setdefault(return_id, arrived_at)

    def count(self, customer_id: str) -> int:
        now = self.clock.now()
        with self._lock:
            arrivals = list(self._arrivals.get(customer_id, {}).values())
        return sum(1 for at in arrivals if now - at <= WINDOW)

    def is_flagged(self, customer_id: str) -> bool:
        return self.count(customer_id) > FLAG_ABOVE

    def flagged_customers(self) -> dict[str, int]:
        with self._lock:
            customers = list(self._arrivals)
        counts = {c: self.count(c) for c in customers}
        return {c: n for c, n in counts.items() if n > FLAG_ABOVE}
