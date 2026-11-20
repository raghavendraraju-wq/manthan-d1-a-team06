"""Decisions agreed after the suite was written (docs/build-plan.md §4 and §6).
These cover the cases the original suite skips as 'decision pending'."""
from datetime import timedelta

import pytest

from returnright.access import OtpService
from returnright.contracts import (
    AuditEntry, Category, EventType, IllegalTransition, ItemState as S, LedgerEntry, RefundMethod, Role,
)
from returnright.store import InMemoryStore
from tests.builders import NOW, refund_requested, requested, to_inspected

ORDERS = {"RR-10482": "priya.s@example.com"}


# --- One-time code valid for 10 minutes (placeholder) ----------------------------

def test_code_valid_for_ten_minutes(clock, email):
    otp = OtpService(clock, email, ORDERS)
    otp.issue("RR-10482", "priya.s@example.com")
    clock.advance(minutes=10)
    assert otp.verify("RR-10482", "priya.s@example.com", email.last_code()) is True


def test_code_expires_after_ten_minutes(clock, email):
    otp = OtpService(clock, email, ORDERS)
    otp.issue("RR-10482", "priya.s@example.com")
    clock.advance(minutes=10, seconds=1)
    assert otp.verify("RR-10482", "priya.s@example.com", email.last_code()) is False


def test_code_locked_after_five_wrong_tries(clock, email):
    otp = OtpService(clock, email, ORDERS)
    otp.issue("RR-10482", "priya.s@example.com")
    code = email.last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert otp.verify("RR-10482", "priya.s@example.com", wrong) is False
    assert otp.verify("RR-10482", "priya.s@example.com", code) is False


# --- After a decline, one new key may be tried, then manual follow-up --------------

def test_one_new_key_after_decline_pays_once(engine, gateway):
    to_inspected(engine, "I1")
    gateway.mode = "decline"
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUND_FAILED
    gateway.mode = "succeed"
    assert engine.apply(refund_requested("I1", "k-2")) == S.REFUNDED
    assert len(gateway.payments) == 1
    assert [e.idempotency_key for e in engine.ledger_entries("I1")] == ["k-2"]


def test_same_key_after_decline_returns_stored_result(engine, gateway):
    to_inspected(engine, "I1")
    gateway.mode = "decline"
    engine.apply(refund_requested("I1", "k-1"))
    gateway.mode = "succeed"
    assert engine.apply(refund_requested("I1", "k-1")) == S.REFUND_FAILED
    assert gateway.payments == []


def test_second_decline_needs_manual_follow_up(engine, gateway):
    to_inspected(engine, "I1")
    gateway.mode = "decline"
    engine.apply(refund_requested("I1", "k-1"))
    assert engine.apply(refund_requested("I1", "k-2")) == S.REFUND_FAILED
    with pytest.raises(IllegalTransition):
        engine.apply(refund_requested("I1", "k-3"))
    assert gateway.payments == []


# --- D9: only returns with an approved item count toward the flag ------------------

def test_rejected_request_does_not_count_toward_flag(engine):
    for i in range(4):
        engine.apply(requested(f"IN{i}", ret=f"RTN-{i}", customer="C-7", category=Category.INNERWEAR, price="799"))
    assert engine.flags.count("C-7") == 0
    for i in range(4):
        engine.apply(requested(f"AP{i}", ret=f"RTN-A{i}", customer="C-7"))
    assert engine.flags.is_flagged("C-7") is True


def test_mixed_return_counts_once(engine):
    engine.apply(requested("K", ret="RTN-1", customer="C-8"))
    engine.apply(requested("H", ret="RTN-1", customer="C-8", category=Category.ELECTRONICS, price="12499"))
    assert engine.flags.count("C-8") == 1


# --- Store: a unit is all-or-nothing (ADR-002) ------------------------------------

def test_unit_discards_writes_when_block_raises(engine):
    engine.apply(requested("I1"))
    store = engine.store
    with pytest.raises(RuntimeError):
        with store.unit("I1") as unit:
            item = unit.get("I1")
            item.state = S.RECEIVED
            unit.put(item, AuditEntry("I1", S.APPROVED, S.RECEIVED, EventType.QR_SCANNED, NOW, Role.AGENT))
            raise RuntimeError("crash mid-transaction")
    assert engine.state("I1") == S.APPROVED
    assert len(engine.audit_log("I1")) == 2


def test_ledger_unique_per_item_and_key():
    store = InMemoryStore()
    with store.unit("I1") as unit:
        unit.add_ledger(LedgerEntry("I1", 1, RefundMethod.ORIGINAL, "k-1", "GW-1"))
    for entry in (LedgerEntry("I1", 1, RefundMethod.ORIGINAL, "k-2", "GW-2"),
                  LedgerEntry("I2", 1, RefundMethod.ORIGINAL, "k-1", "GW-3")):
        with pytest.raises(Exception, match="already holds"):
            with store.unit(entry.item_id) as unit:
                unit.add_ledger(entry)
    assert len(store.ledger_entries()) == 1
