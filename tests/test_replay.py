"""Replay: the same input twice gives the same result.
Domain: the same event sequence into two fresh engines ends identically.
Alerts: see test_alert_rules.py (promtool runs the same series twice)."""
from datetime import timedelta

import pytest

from returnright.contracts import Category, ReturnRightError
from returnright.engine import build_engine
from returnright.flags import FlagDetector
from tests.builders import (
    NOW, finance_approved, inspected, picked_up, refund_requested, requested, scanned,
)
from tests.fakes import FakeClock, FakeGateway


def _sequence():
    return [
        requested("KURTA", ret="RTN-1"),
        requested("HP", ret="RTN-1", category=Category.ELECTRONICS, price="12499"),
        requested("INNER", ret="RTN-1", category=Category.INNERWEAR, price="799"),
        picked_up("KURTA"), picked_up("HP"),
        scanned("KURTA"), scanned("HP"),
        picked_up("KURTA"),                         # late carrier event: refused
        inspected("KURTA", grade="C"), inspected("HP", grade="A"),
        refund_requested("KURTA", "k-kurta"),       # gateway times out after paying
        refund_requested("KURTA", "k-kurta"),       # retry, same key
        finance_approved("HP", "k-hp"),
        finance_approved("HP", "k-hp"),             # double click
    ]


def _run():
    gateway, clock = FakeGateway(), FakeClock(NOW)
    engine = build_engine(gateway=gateway, clock=clock)
    for i, event in enumerate(_sequence()):
        if i == 10:
            gateway.mode = "timeout_after_pay"
        try:
            engine.apply(event)
        except ReturnRightError:
            pass
    items = ("KURTA", "HP", "INNER")
    return {
        "states": {i: engine.state(i) for i in items},
        "status": engine.return_status("RTN-1"),
        "ledger": [(e.item_id, e.amount, e.method, e.idempotency_key) for e in engine.ledger_entries()],
        "audit": {i: [(a.from_state, a.to_state, a.event_type) for a in engine.audit_log(i)] for i in items},
        "refused": [(e.type, err) for e, err in engine.rejected_events()],
        "payments": len(gateway.payments),
    }


def test_same_events_twice_same_outcome():
    first, second = _run(), _run()
    assert first == second
    assert first["payments"] == 2              # kurta once, headphones once
    assert len(first["refused"]) == 1          # only the late carrier event


def test_flag_replay_is_stable():
    def run():
        clock = FakeClock(NOW)
        d = FlagDetector(clock)
        for i in range(4):
            d.record_return("C-1", f"RTN-{i}", NOW)
            clock.advance(days=1)
        for i in range(4):                     # the whole stream replayed
            d.record_return("C-1", f"RTN-{i}", NOW)
        return d.count("C-1"), d.is_flagged("C-1")

    assert run() == run() == (4, True)
