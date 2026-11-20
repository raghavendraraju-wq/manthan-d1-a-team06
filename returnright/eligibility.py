"""Eligibility engine.

Rules (agreed): standard window 30 days, electronics 10 days, counted from the
delivery date to the end of that day in IST. Innerwear and perishables are never
returnable. Every result carries a rule code and a customer-facing reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .contracts import IST, Category, Eligibility

STANDARD_DAYS = 30
ELECTRONICS_DAYS = 10

NON_RETURNABLE_REASONS = {
    Category.INNERWEAR: "Not returnable · innerwear can't be returned for hygiene reasons",
    Category.PERISHABLES: "Not returnable · perishables can't be returned",
}


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def check(category: Category, delivered_at: datetime, requested_at: datetime) -> Eligibility:
    category = Category(category)
    if category in NON_RETURNABLE_REASONS:
        return Eligibility(False, "NON_RETURNABLE_CATEGORY", NON_RETURNABLE_REASONS[category], None, None)

    electronics = category is Category.ELECTRONICS
    window = ELECTRONICS_DAYS if electronics else STANDARD_DAYS
    rule = "ELECTRONICS_WINDOW" if electronics else "STANDARD_WINDOW"
    last_day = delivered_at.astimezone(IST).date() + timedelta(days=window)
    request_day = requested_at.astimezone(IST).date()

    if request_day > last_day:
        reason = f"Not returnable · the {window}-day return window ended on {last_day:%d %b %Y}"
        return Eligibility(False, "WINDOW_EXPIRED", reason, last_day, 0)

    days_left = (last_day - request_day).days
    left = "last day today" if days_left == 0 else f"{_days(days_left)} left"
    if electronics:
        reason = f"Returnable · electronics have a {window}-day window, {left}"
    else:
        reason = f"Returnable · {window}-day window from delivery, {left}"
    return Eligibility(True, rule, reason, last_day, days_left)
