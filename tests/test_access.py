"""Persona-level access: staff see only their role's screens and can only send their
role's events; customers need order no. + email + a one-time code."""
import pytest

from returnright.access import OtpService, can_access
from returnright.contracts import Category, Forbidden, ItemState as S, Role, Screen
from tests.builders import (
    finance_approved, inspected, requested, scanned, to_inspected, to_received,
)

OWN_SCREEN = {
    Role.CUSTOMER: Screen.CUSTOMER_WIZARD,
    Role.AGENT: Screen.SCAN_AND_GRADE,
    Role.FINANCE: Screen.APPROVAL_QUEUE,
    Role.MANAGER: Screen.DASHBOARD,
}


@pytest.mark.parametrize("role", list(OWN_SCREEN))
@pytest.mark.parametrize("screen", list(Screen))
def test_each_role_sees_only_its_own_screen(role, screen):
    assert can_access(role, screen) is (OWN_SCREEN[role] == screen)


@pytest.mark.parametrize("screen", list(Screen))
def test_system_role_has_no_screens(screen):
    assert can_access(Role.SYSTEM, screen) is False


@pytest.mark.parametrize("actor", [Role.CUSTOMER, Role.FINANCE, Role.MANAGER])
def test_only_agent_can_scan(engine, actor):
    engine.apply(requested("I1"))
    with pytest.raises(Forbidden):
        engine.apply(scanned("I1", actor=actor))
    assert engine.state("I1") == S.APPROVED


@pytest.mark.parametrize("actor", [Role.CUSTOMER, Role.FINANCE, Role.MANAGER])
def test_only_agent_can_grade(engine, actor):
    to_received(engine, "I1")
    with pytest.raises(Forbidden):
        engine.apply(inspected("I1", actor=actor))
    assert engine.state("I1") == S.RECEIVED


@pytest.mark.parametrize("actor", [Role.CUSTOMER, Role.AGENT, Role.MANAGER])
def test_only_finance_can_approve(engine, gateway, actor):
    to_inspected(engine, "I1", category=Category.ELECTRONICS, price="12499")
    with pytest.raises(Forbidden):
        engine.apply(finance_approved("I1", "k-1", actor=actor))
    assert engine.state("I1") == S.PENDING_FINANCE
    assert gateway.calls == []


ORDERS = {"RR-10482": "priya.s@example.com"}


def test_code_sent_when_order_and_email_match(clock, email):
    otp = OtpService(clock, email, ORDERS)
    otp.issue("RR-10482", "priya.s@example.com")
    assert len(email.sent) == 1
    assert email.sent[0][0] == "priya.s@example.com"
    assert len(email.last_code()) == 6


def test_email_matching_ignores_case_and_spaces(clock, email):
    otp = OtpService(clock, email, ORDERS)
    otp.issue("RR-10482", "  Priya.S@Example.com ")
    assert len(email.sent) == 1


@pytest.mark.parametrize("order_no, addr", [
    ("RR-10482", "someone.else@example.com"),
    ("RR-99999", "priya.s@example.com"),
])
def test_no_code_sent_on_mismatch(clock, email, order_no, addr):
    OtpService(clock, email, ORDERS).issue(order_no, addr)
    assert email.sent == []


def test_correct_code_verifies_once(clock, email):
    otp = OtpService(clock, email, ORDERS)
    otp.issue("RR-10482", "priya.s@example.com")
    code = email.last_code()
    assert otp.verify("RR-10482", "priya.s@example.com", code) is True
    assert otp.verify("RR-10482", "priya.s@example.com", code) is False


def test_wrong_code_fails(clock, email):
    otp = OtpService(clock, email, ORDERS)
    otp.issue("RR-10482", "priya.s@example.com")
    wrong = "000000" if email.last_code() != "000000" else "111111"
    assert otp.verify("RR-10482", "priya.s@example.com", wrong) is False


def test_resend_voids_earlier_code(clock, email):
    otp = OtpService(clock, email, ORDERS)
    otp.issue("RR-10482", "priya.s@example.com")
    first = email.last_code()
    otp.issue("RR-10482", "priya.s@example.com")
    second = email.last_code()
    if first != second:
        assert otp.verify("RR-10482", "priya.s@example.com", first) is False
    assert otp.verify("RR-10482", "priya.s@example.com", second) is True


def test_code_for_one_order_does_not_open_another(clock, email):
    otp = OtpService(clock, email, {**ORDERS, "RR-20000": "priya.s@example.com"})
    otp.issue("RR-10482", "priya.s@example.com")
    assert otp.verify("RR-20000", "priya.s@example.com", email.last_code()) is False


@pytest.mark.skip(reason="Decision pending: how long a one-time code stays valid")
def test_code_expires():
    pass
