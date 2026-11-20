"""Rule: more than 3 returns in 30 days flags the customer.
Agreed: counted by ARRIVAL time (processing time); flag is dashboard-only."""
from datetime import timedelta

from returnright.contracts import ItemState as S
from returnright.flags import FlagDetector
from tests.builders import NOW, requested


def _record(detector, n, customer="C-1", event_time=NOW, start=0):
    for i in range(start, start + n):
        detector.record_return(customer, f"RTN-{i}", event_time)


def test_three_returns_not_flagged_fourth_is(clock):
    d = FlagDetector(clock)
    _record(d, 3)
    assert d.is_flagged("C-1") is False
    _record(d, 1, start=3)
    assert d.is_flagged("C-1") is True


def test_flag_is_per_customer(clock):
    d = FlagDetector(clock)
    _record(d, 4, customer="C-1")
    _record(d, 1, customer="C-2", start=10)
    assert d.is_flagged("C-2") is False


def test_returns_drop_out_after_30_days_of_arrival(clock):
    d = FlagDetector(clock)
    _record(d, 4)
    clock.advance(days=29)
    assert d.is_flagged("C-1") is True
    clock.advance(days=2)            # 31 days after arrival
    assert d.is_flagged("C-1") is False


def test_same_return_recorded_twice_counts_once(clock):
    d = FlagDetector(clock)
    _record(d, 3)
    d.record_return("C-1", "RTN-0", NOW)   # replayed event
    assert d.count("C-1") == 3
    assert d.is_flagged("C-1") is False


def test_flagged_customer_is_not_blocked(engine):
    for i in range(5):
        assert engine.apply(requested(f"I{i}", ret=f"RTN-{i}", customer="C-9")) == S.APPROVED
