"""Rules: grade C refunds 50%; finance approves when an ITEM's final amount is over INR 10,000."""
from decimal import Decimal

import pytest

from returnright.contracts import Grade
from returnright.pricing import needs_finance, refund_amount


@pytest.mark.parametrize("grade, expected", [
    (Grade.A, Decimal("12499")),
    (Grade.B, Decimal("12499")),
    (Grade.C, Decimal("6249.50")),
])
def test_grade_sets_refund_amount(grade, expected):
    assert refund_amount(Decimal("12499"), grade) == expected


@pytest.mark.parametrize("amount, expected", [
    (Decimal("10000"), False),
    (Decimal("10000.01"), True),
    (Decimal("10001"), True),
])
def test_finance_threshold_boundary(amount, expected):
    assert needs_finance(amount) is expected


def test_threshold_uses_amount_after_grading():
    assert needs_finance(refund_amount(Decimal("20002"), Grade.C)) is True    # 10,001
    assert needs_finance(refund_amount(Decimal("20000"), Grade.C)) is False   # 10,000


def test_threshold_is_per_item_not_per_return():
    # Agreed: three INR 8,000 items (24,000 total) need no finance review.
    assert not any(needs_finance(refund_amount(Decimal("8000"), Grade.A)) for _ in range(3))
