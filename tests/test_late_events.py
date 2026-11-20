"""Late events: (1) a carrier event that arrives after the item is already Received;
(2) a return event whose own timestamp is outside the 30-day flag window."""
from datetime import timedelta

import pytest

from returnright.contracts import EventType, IllegalTransition, ItemState as S
from returnright.flags import FlagDetector
from tests.builders import NOW, inspected, picked_up, refund_requested, to_received


@pytest.mark.parametrize("extra", [[], [inspected("I1")], [inspected("I1"), refund_requested("I1", "k-1")]])
def test_late_carrier_event_never_moves_state_backwards(engine, extra):
    to_received(engine, "I1")
    for e in extra:
        engine.apply(e)
    state_before = engine.state("I1")
    audit_before = len(engine.audit_log("I1"))

    with pytest.raises(IllegalTransition):
        engine.apply(picked_up("I1", at=NOW - timedelta(hours=6)))   # dated earlier, arrives now

    assert engine.state("I1") == state_before
    assert len(engine.audit_log("I1")) == audit_before
    event, error = engine.rejected_events()[-1]
    assert event.type == EventType.CARRIER_PICKED_UP and error == "IllegalTransition"


def test_late_return_event_counts_by_arrival_time(clock):
    """Event time is 40 days ago, but it arrives now: it counts (processing-time rule)."""
    d = FlagDetector(clock)
    for i in range(3):
        d.record_return("C-1", f"RTN-{i}", NOW)
    d.record_return("C-1", "RTN-late", NOW - timedelta(days=40))
    assert d.count("C-1") == 4
    assert d.is_flagged("C-1") is True


def test_recent_event_time_does_not_keep_old_arrival_in_window(clock):
    """Arrived 31 days ago: it no longer counts, whatever its own timestamp says."""
    d = FlagDetector(clock)
    for i in range(4):
        d.record_return("C-1", f"RTN-{i}", NOW + timedelta(days=31))   # future-dated event
    clock.advance(days=31)
    assert d.count("C-1") == 0
