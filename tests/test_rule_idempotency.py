"""Rule: a refund is idempotent. ADR-001: idempotency key AND one refund per return item.
Clicking twice, or retrying after a timeout, must never pay twice."""
import threading
from decimal import Decimal

import pytest

from returnright.contracts import Category, DuplicateRefund, ItemState as S, RefundMethod
from tests.builders import finance_approved, refund_requested, to_inspected


def test_double_click_same_key_pays_once(engine, gateway):
    to_inspected(engine, "I1")
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUNDED
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUNDED   # same answer, no error
    assert len(gateway.payments) == 1
    assert len(engine.ledger_entries("I1")) == 1


def test_finance_double_approve_pays_once(engine, gateway):
    to_inspected(engine, "I1", category=Category.ELECTRONICS, price="12499")
    engine.apply(finance_approved("I1", "k-1"))
    engine.apply(finance_approved("I1", "k-1"))
    assert len(gateway.payments) == 1


def test_new_key_for_refunded_item_is_refused(engine, gateway):
    to_inspected(engine, "I1")
    engine.apply(refund_requested("I1", "k-1"))
    with pytest.raises(DuplicateRefund):
        engine.apply(refund_requested("I1", "k-2"))
    assert len(gateway.payments) == 1


def test_timeout_after_payment_then_retry_same_key(engine, gateway):
    to_inspected(engine, "I1")
    gateway.mode = "timeout_after_pay"
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUND_PENDING
    assert engine.ledger_entries("I1") == []
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUNDED
    assert len(gateway.payments) == 1
    assert len(engine.ledger_entries("I1")) == 1


def test_timeout_before_payment_then_retry_pays_once(engine, gateway):
    to_inspected(engine, "I1")
    gateway.mode = "timeout_before_pay"
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUND_PENDING
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUNDED
    assert len(gateway.payments) == 1


def test_reconcile_settles_stuck_refund(engine, gateway):
    to_inspected(engine, "I1")
    gateway.mode = "timeout_after_pay"
    engine.apply(refund_requested("I1", "k-1"))
    assert engine.reconcile() == 1
    assert engine.state("I1") == S.REFUNDED
    assert len(engine.ledger_entries("I1")) == 1
    assert engine.reconcile() == 0   # nothing left; running again changes nothing


def test_reconcile_and_retry_racing_still_pay_once(engine, gateway):
    to_inspected(engine, "I1")
    gateway.mode = "timeout_after_pay"
    engine.apply(refund_requested("I1", "k-1"))
    engine.reconcile()
    engine.apply(refund_requested("I1", "k-1"))
    assert len(gateway.payments) == 1
    assert len(engine.ledger_entries("I1")) == 1


@pytest.mark.parametrize("keys", [("k-1", "k-1"), ("k-1", "k-2")])
def test_concurrent_requests_pay_once(engine, gateway, keys):
    to_inspected(engine, "I1")
    barrier = threading.Barrier(2)

    def fire(key):
        barrier.wait()
        try:
            engine.apply(refund_requested("I1", key))
        except DuplicateRefund:
            pass

    threads = [threading.Thread(target=fire, args=(k,)) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(gateway.payments) == 1
    assert len(engine.ledger_entries("I1")) == 1


def test_store_credit_double_click_one_ledger_entry(engine, gateway):
    to_inspected(engine, "I1", method=RefundMethod.STORE_CREDIT)
    engine.apply(refund_requested("I1", "k-1"))
    engine.apply(refund_requested("I1", "k-1"))
    assert len(engine.ledger_entries("I1")) == 1
    assert gateway.calls == []


def test_declined_refund_moves_to_refund_failed(engine, gateway):
    to_inspected(engine, "I1")
    gateway.mode = "decline"
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUND_FAILED
    assert gateway.payments == []
    assert engine.ledger_entries("I1") == []


@pytest.mark.skip(reason="Decision pending: how a retry after a DECLINE works under the one-refund-per-item rule")
def test_retry_after_decline():
    pass


def test_ledger_amount_matches_graded_amount(engine):
    to_inspected(engine, "I1", grade="C", category=Category.ELECTRONICS, price="12499")
    engine.apply(refund_requested("I1", "k-1"))
    assert engine.ledger_entries("I1")[0].amount == Decimal("6249.50")
