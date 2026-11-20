"""Rule: per-item state machine, driven by crafted event sequences."""
from decimal import Decimal

import pytest

from returnright.contracts import (
    Category, EventType, IllegalTransition, ItemState as S, RefundMethod,
)
from tests.builders import (
    cancelled, expiry_due, finance_approved, finance_rejected, inspected, picked_up,
    refund_requested, requested, scanned, to_inspected, to_received,
)


def test_happy_path_up_to_10k(engine):
    states = [
        engine.apply(requested("I1")),
        engine.apply(picked_up("I1")),
        engine.apply(scanned("I1")),
        engine.apply(inspected("I1", grade="A")),
        engine.apply(refund_requested("I1", "k-1")),
    ]
    assert states == [S.APPROVED, S.IN_TRANSIT, S.RECEIVED, S.INSPECTED, S.REFUNDED]


def test_over_10k_goes_through_finance(engine):
    assert to_inspected(engine, "I1", category=Category.ELECTRONICS, price="12499") == S.PENDING_FINANCE
    assert engine.apply(finance_approved("I1", "k-1")) == S.REFUNDED


def test_system_cannot_refund_past_finance(engine):
    to_inspected(engine, "I1", category=Category.ELECTRONICS, price="12499")
    with pytest.raises(IllegalTransition):
        engine.apply(refund_requested("I1", "k-1"))
    assert engine.state("I1") == S.PENDING_FINANCE


def test_ineligible_item_is_rejected_with_rule(engine):
    assert engine.apply(requested("I1", category=Category.INNERWEAR, price="799")) == S.REJECTED
    assert engine.rejection_rule("I1") == "NON_RETURNABLE_CATEGORY"


def test_scan_without_carrier_event_skips_in_transit(engine):
    engine.apply(requested("I1"))
    assert engine.apply(scanned("I1")) == S.RECEIVED


@pytest.mark.parametrize("final_events", [
    [inspected("I1"), refund_requested("I1", "k-1")],          # REFUNDED
    [inspected("I1", decision="reject")],                       # REJECTED
])
def test_scan_only_works_in_approved_or_in_transit(engine, final_events):
    to_received(engine, "I1")
    for e in final_events:
        engine.apply(e)
    before = engine.state("I1")
    with pytest.raises(IllegalTransition):
        engine.apply(scanned("I1"))
    assert engine.state("I1") == before


@pytest.mark.parametrize("setup", [
    [requested("I1")],
    [requested("I1"), picked_up("I1")],
])
def test_customer_can_cancel_before_received(engine, setup):
    for e in setup:
        engine.apply(e)
    assert engine.apply(cancelled("I1")) == S.CANCELLED


def test_customer_cannot_cancel_after_received(engine):
    to_received(engine, "I1")
    with pytest.raises(IllegalTransition):
        engine.apply(cancelled("I1"))
    assert engine.state("I1") == S.RECEIVED


def test_expiry_only_from_approved(engine):
    engine.apply(requested("I1"))
    assert engine.apply(expiry_due("I1")) == S.EXPIRED
    engine.apply(requested("I2"))
    engine.apply(picked_up("I2"))
    with pytest.raises(IllegalTransition):
        engine.apply(expiry_due("I2"))


def test_inspection_reject(engine):
    to_received(engine, "I1")
    assert engine.apply(inspected("I1", decision="reject")) == S.REJECTED


def test_finance_reject_pays_nothing(engine, gateway):
    to_inspected(engine, "I1", category=Category.ELECTRONICS, price="24400")
    assert engine.apply(finance_rejected("I1")) == S.REJECTED
    assert gateway.calls == []
    assert engine.ledger_entries("I1") == []


def test_regrade_allowed_before_refund_and_can_leave_finance(engine):
    to_inspected(engine, "I1", category=Category.ELECTRONICS, price="12499")   # PENDING_FINANCE
    assert engine.apply(inspected("I1", grade="C")) == S.INSPECTED            # 6,249.50
    assert engine.refund_amount("I1") == Decimal("6249.50")
    assert engine.apply(inspected("I1", grade="A")) == S.PENDING_FINANCE      # back over 10k


def test_regrade_refused_once_refund_started(engine):
    to_inspected(engine, "I1")
    engine.apply(refund_requested("I1", "k-1"))
    with pytest.raises(IllegalTransition):
        engine.apply(inspected("I1", grade="C"))
    assert engine.refund_amount("I1") == Decimal("1899")


def test_grade_c_refunds_half(engine):
    to_inspected(engine, "I1", grade="C", price="1899")
    engine.apply(refund_requested("I1", "k-1"))
    assert engine.ledger_entries("I1")[0].amount == Decimal("949.50")


def test_mixed_basket_items_move_independently(engine):
    engine.apply(requested("KURTA", ret="RTN-1"))
    engine.apply(requested("HP", ret="RTN-1", category=Category.ELECTRONICS, price="12499"))
    assert engine.apply(requested("INNER", ret="RTN-1", category=Category.INNERWEAR, price="799")) == S.REJECTED
    assert engine.return_status("RTN-1") == "Open"
    for item in ("KURTA", "HP"):
        engine.apply(scanned(item))
        engine.apply(inspected(item))
    engine.apply(refund_requested("KURTA", "k-kurta"))
    assert engine.return_status("RTN-1") == "Open"          # HP still with finance
    engine.apply(finance_approved("HP", "k-hp"))
    assert engine.return_status("RTN-1") == "Completed"


def test_every_state_change_writes_one_audit_entry(engine):
    to_inspected(engine, "I1")
    engine.apply(refund_requested("I1", "k-1"))
    trail = [(a.from_state, a.to_state) for a in engine.audit_log("I1")]
    assert trail[:2] == [(None, S.REQUESTED), (S.REQUESTED, S.APPROVED)]
    assert trail[-1][1] == S.REFUNDED
    for (_, to), (frm, _) in zip(trail, trail[1:]):
        assert to == frm, "audit trail must be continuous"


def test_refused_event_writes_no_audit_and_is_recorded(engine):
    to_received(engine, "I1")
    before = len(engine.audit_log("I1"))
    with pytest.raises(IllegalTransition):
        engine.apply(cancelled("I1"))
    assert len(engine.audit_log("I1")) == before
    event, error = engine.rejected_events()[-1]
    assert event.type == EventType.CUSTOMER_CANCELLED and error == "IllegalTransition"


def test_store_credit_follows_same_states_without_gateway(engine, gateway):
    to_inspected(engine, "I1", method=RefundMethod.STORE_CREDIT)
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUNDED
    assert gateway.calls == []
    assert engine.ledger_entries("I1")[0].method == RefundMethod.STORE_CREDIT
