"""Refund amount rules.

Grade C refunds 50% of the price; A and B refund 100%.
Finance approval is needed when an item's final amount (after grading) is over INR 10,000.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .contracts import Grade

FINANCE_THRESHOLD_INR = Decimal("10000")
_PAISE = Decimal("0.01")


def refund_amount(price_inr: Decimal, grade: Grade) -> Decimal:
    if Grade(grade) is Grade.C:
        return (price_inr / 2).quantize(_PAISE, rounding=ROUND_HALF_UP)
    return price_inr


def needs_finance(final_amount_inr: Decimal) -> bool:
    """Strictly more than INR 10,000, judged per item."""
    return final_amount_inr > FINANCE_THRESHOLD_INR
