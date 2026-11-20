"""Rule: return windows. Standard 30 days, electronics 10 days, from the delivery
date to the end of that day in IST. Innerwear and perishables are never returnable."""
from datetime import date, datetime, timezone

import pytest

from returnright.contracts import IST, Category
from returnright.eligibility import check
from tests.builders import DELIVERED  # delivered 12 Nov 2026, 15:00 IST


@pytest.mark.parametrize("requested_at, eligible", [
    (datetime(2026, 12, 12, 23, 59, 59, tzinfo=IST), True),   # day 30, last second
    (datetime(2026, 12, 13, 0, 0, 0, tzinfo=IST), False),     # day 31, first second
])
def test_standard_window_boundary_day_30_vs_31(requested_at, eligible):
    result = check(Category.APPAREL, DELIVERED, requested_at)
    assert result.eligible is eligible
    assert result.rule == ("STANDARD_WINDOW" if eligible else "WINDOW_EXPIRED")
    assert result.last_day == date(2026, 12, 12)


@pytest.mark.parametrize("requested_at, eligible", [
    (datetime(2026, 11, 22, 23, 59, 59, tzinfo=IST), True),   # day 10
    (datetime(2026, 11, 23, 0, 0, 0, tzinfo=IST), False),     # day 11
])
def test_electronics_window_boundary_day_10_vs_11(requested_at, eligible):
    result = check(Category.ELECTRONICS, DELIVERED, requested_at)
    assert result.eligible is eligible
    assert result.rule == ("ELECTRONICS_WINDOW" if eligible else "WINDOW_EXPIRED")


@pytest.mark.parametrize("utc_time, eligible", [
    (datetime(2026, 12, 12, 18, 29, 59, tzinfo=timezone.utc), True),   # 23:59:59 IST
    (datetime(2026, 12, 12, 18, 30, 0, tzinfo=timezone.utc), False),   # 00:00 IST next day
])
def test_window_end_is_judged_in_ist_not_utc(utc_time, eligible):
    assert check(Category.APPAREL, DELIVERED, utc_time).eligible is eligible


@pytest.mark.parametrize("category", [Category.INNERWEAR, Category.PERISHABLES])
def test_non_returnable_categories_even_on_delivery_day(category):
    result = check(category, DELIVERED, DELIVERED)
    assert result.eligible is False
    assert result.rule == "NON_RETURNABLE_CATEGORY"
    assert result.last_day is None


@pytest.mark.parametrize("category, expected_days_left", [
    (Category.APPAREL, 22),       # matches the prototype on 20 Nov
    (Category.ELECTRONICS, 2),
])
def test_days_left_matches_prototype(category, expected_days_left):
    now = datetime(2026, 11, 20, 10, 0, tzinfo=IST)
    assert check(category, DELIVERED, now).days_left == expected_days_left


@pytest.mark.parametrize("category, at", [
    (Category.APPAREL, datetime(2026, 11, 20, tzinfo=IST)),
    (Category.APPAREL, datetime(2026, 12, 20, tzinfo=IST)),
    (Category.ELECTRONICS, datetime(2026, 11, 20, tzinfo=IST)),
    (Category.INNERWEAR, datetime(2026, 11, 20, tzinfo=IST)),
])
def test_every_decision_explains_itself(category, at):
    assert check(category, DELIVERED, at).reason.strip()
