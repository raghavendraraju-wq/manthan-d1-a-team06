"""The brief's 10 written end-to-end cases, following the prototype's flows.
They run at the domain level; the same cases can be driven through the UI with Playwright."""
from decimal import Decimal

import pytest

from returnright.contracts import (
    Category, IllegalTransition, ItemState as S, RefundMethod,
)
from tests.builders import (
    DELIVERED, cancelled, finance_approved, finance_rejected, inspected, picked_up,
    refund_requested, requested, scanned, to_inspected,
)
from datetime import datetime
from returnright.contracts import IST


def test_e2e_01_kurta_grade_a_refunded_to_original_payment(engine, gateway):
    to_inspected(engine, "KURTA", price="1899")
    assert engine.apply(refund_requested("KURTA", "k-1")) == S.REFUNDED
    assert engine.ledger_entries("KURTA")[0].amount == Decimal("1899")
    assert len(gateway.payments) == 1


def test_e2e_02_headphones_over_10k_need_finance(engine):
    assert to_inspected(engine, "HP", category=Category.ELECTRONICS, price="12499") == S.PENDING_FINANCE
    assert engine.apply(finance_approved("HP", "k-1")) == S.REFUNDED
    assert engine.ledger_entries("HP")[0].amount == Decimal("12499")


def test_e2e_03_headphones_graded_c_skip_finance(engine):
    assert to_inspected(engine, "HP", grade="C", category=Category.ELECTRONICS, price="12499") == S.INSPECTED
    assert engine.apply(refund_requested("HP", "k-1")) == S.REFUNDED
    assert engine.ledger_entries("HP")[0].amount == Decimal("6249.50")


def test_e2e_04_mixed_basket_completes_when_all_items_end(engine):
    engine.apply(requested("KURTA", ret="RTN-1"))
    assert engine.apply(requested("INNER", ret="RTN-1", category=Category.INNERWEAR, price="799")) == S.REJECTED
    engine.apply(scanned("KURTA"))
    engine.apply(inspected("KURTA"))
    assert engine.return_status("RTN-1") == "Open"
    engine.apply(refund_requested("KURTA", "k-1"))
    assert engine.return_status("RTN-1") == "Completed"


def test_e2e_05_electronics_on_day_11_rejected_with_reason(engine):
    late = datetime(2026, 11, 23, 9, 0, tzinfo=IST)
    assert engine.apply(requested("HP", category=Category.ELECTRONICS, price="12499", at=late)) == S.REJECTED
    assert engine.rejection_rule("HP") == "WINDOW_EXPIRED"


def test_e2e_06_monitor_to_store_credit_via_finance(engine, gateway):
    to_inspected(engine, "MON", grade="B", category=Category.ELECTRONICS, price="18990",
                 method=RefundMethod.STORE_CREDIT)
    assert engine.apply(finance_approved("MON", "k-1")) == S.REFUNDED
    entry = engine.ledger_entries("MON")[0]
    assert (entry.method, entry.amount, entry.gateway_ref) == (RefundMethod.STORE_CREDIT, Decimal("18990"), None)
    assert gateway.calls == []


def test_e2e_07_finance_rejects_smartwatch(engine, gateway):
    to_inspected(engine, "SW", grade="C", category=Category.ELECTRONICS, price="24400")   # 12,200
    assert engine.apply(finance_rejected("SW")) == S.REJECTED
    assert engine.ledger_entries("SW") == [] and gateway.calls == []


def test_e2e_08_gateway_timeout_then_retry_pays_once(engine, gateway):
    to_inspected(engine, "HP", category=Category.ELECTRONICS, price="12499")
    gateway.mode = "timeout_after_pay"
    assert engine.apply(finance_approved("HP", "k-1")) == S.REFUND_PENDING
    assert engine.apply(finance_approved("HP", "k-1")) == S.REFUNDED
    assert len(gateway.payments) == 1


def test_e2e_09_customer_cancels_in_transit_then_parcel_scan_refused(engine):
    engine.apply(requested("KURTA"))
    engine.apply(picked_up("KURTA"))
    assert engine.apply(cancelled("KURTA")) == S.CANCELLED
    with pytest.raises(IllegalTransition):
        engine.apply(scanned("KURTA"))


def test_e2e_10_scan_without_carrier_event_still_completes(engine):
    engine.apply(requested("KURTA", ret="RTN-9"))
    assert engine.apply(scanned("KURTA")) == S.RECEIVED
    engine.apply(inspected("KURTA"))
    engine.apply(refund_requested("KURTA", "k-1"))
    assert engine.return_status("RTN-9") == "Completed"
