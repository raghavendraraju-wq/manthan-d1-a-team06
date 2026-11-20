"""Eligibility engine (stub).

Rules (agreed): standard window 30 days, electronics 10 days, counted from the
delivery date to the end of that day in IST. Innerwear and perishables are never
returnable. Every result carries a rule code and a customer-facing reason.
"""
from __future__ import annotations

from datetime import datetime

from .contracts import Category, Eligibility


def check(category: Category, delivered_at: datetime, requested_at: datetime) -> Eligibility:
    raise NotImplementedError("eligibility.check is not built yet")
