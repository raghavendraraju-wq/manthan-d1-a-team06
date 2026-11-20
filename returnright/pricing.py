"""Refund amount rules (stub).

Grade C refunds 50% of the price; A and B refund 100%.
Finance approval is needed when an item's final amount (after grading) is over INR 10,000.
"""
from __future__ import annotations

from decimal import Decimal

from .contracts import Grade


def refund_amount(price_inr: Decimal, grade: Grade) -> Decimal:
    raise NotImplementedError("pricing.refund_amount is not built yet")


def needs_finance(final_amount_inr: Decimal) -> bool:
    raise NotImplementedError("pricing.needs_finance is not built yet")
