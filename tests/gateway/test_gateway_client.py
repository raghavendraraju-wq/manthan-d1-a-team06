"""HttpGateway (backend adapter) against the real mock gateway service, and the engine end to end through it."""
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.adapters.gateway_client import HttpGateway
from gateway.app import create_app
from returnright.contracts import Category, GatewayTimeout, ItemState as S, RefundMethod
from returnright.engine import build_engine
from tests.builders import finance_approved, refund_requested, to_inspected


@pytest.fixture
def service(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "gw.db"), admin_token=""))


@pytest.fixture
def seen():
    return []


@pytest.fixture
def http_gateway(service, seen):
    return HttpGateway("http://testserver", client=service, observe=lambda o, s: seen.append(o))


@pytest.fixture
def engine(http_gateway, clock):
    return build_engine(gateway=http_gateway, clock=clock)


def behave(service, **b):
    service.put("/admin/behaviour", json=b)


def paid(service):
    return service.get("/admin/behaviour").json()["payments_made"]


# --- client mapping -----------------------------------------------------------

def test_success_and_outcome_observed(http_gateway, seen):
    result = http_gateway.pay("k-1", Decimal("6249.50"), "I1")
    assert (result.status, result.ref) == ("succeeded", "GW-000001")
    assert seen == ["succeeded"]


@pytest.mark.parametrize("mode, outcome", [("timeout_before_pay", "timeout"), ("timeout_after_pay", "timeout"),
                                           ("error", "error")])
def test_unclear_answers_raise_timeout(service, http_gateway, seen, mode, outcome):
    behave(service, mode=mode, times=1)
    with pytest.raises(GatewayTimeout):
        http_gateway.pay("k-1", Decimal("100"), "I1")
    assert seen == [outcome]


def test_network_timeout_raises_timeout():
    def slow(request):
        raise httpx.ReadTimeout("no answer", request=request)

    seen = []
    gw = HttpGateway("http://gw", client=httpx.Client(base_url="http://gw", transport=httpx.MockTransport(slow)),
                     observe=lambda o, s: seen.append(o))
    with pytest.raises(GatewayTimeout):
        gw.pay("k-1", Decimal("1"), "I1")
    with pytest.raises(GatewayTimeout):
        gw.status("k-1")
    assert seen == ["timeout"]


def test_status_unknown_key_is_none(http_gateway):
    assert http_gateway.status("never-seen") is None


# --- engine end to end through the real service -------------------------------

def test_refund_pays_once_on_double_click(engine, service):
    to_inspected(engine, "KURTA")
    assert engine.apply(refund_requested("KURTA", "k-1")) == S.REFUNDED
    assert engine.apply(refund_requested("KURTA", "k-1")) == S.REFUNDED
    assert paid(service) == 1
    assert engine.ledger_entries("KURTA")[0].gateway_ref == "GW-000001"


def test_timeout_after_pay_then_retry_pays_once(engine, service):
    to_inspected(engine, "HP", category=Category.ELECTRONICS, price="12499")
    behave(service, mode="timeout_after_pay", times=1)
    assert engine.apply(finance_approved("HP", "k-1")) == S.REFUND_PENDING
    assert engine.apply(finance_approved("HP", "k-1")) == S.REFUNDED
    assert paid(service) == 1


def test_reconcile_settles_through_status_lookup(engine, service):
    to_inspected(engine, "KURTA")
    behave(service, mode="timeout_after_pay", times=1)
    engine.apply(refund_requested("KURTA", "k-1"))
    assert engine.reconcile() == 1
    assert engine.state("KURTA") == S.REFUNDED
    assert paid(service) == 1


def test_reconcile_leaves_unpaid_timeout_for_retry(engine, service):
    to_inspected(engine, "KURTA")
    behave(service, mode="timeout_before_pay", times=1)
    engine.apply(refund_requested("KURTA", "k-1"))
    assert engine.reconcile() == 0
    assert engine.apply(refund_requested("KURTA", "k-1")) == S.REFUNDED
    assert paid(service) == 1


def test_gateway_down_keeps_refund_pending(clock):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    gw = HttpGateway("http://gw", client=httpx.Client(base_url="http://gw", transport=httpx.MockTransport(down)))
    engine = build_engine(gateway=gw, clock=clock)
    to_inspected(engine, "KURTA")
    assert engine.apply(refund_requested("KURTA", "k-1")) == S.REFUND_PENDING
    assert engine.reconcile() == 0
    assert engine.ledger_entries() == []


def test_store_credit_never_calls_the_service(engine, service, seen):
    to_inspected(engine, "KURTA", method=RefundMethod.STORE_CREDIT)
    assert engine.apply(refund_requested("KURTA", "k-1")) == S.REFUNDED
    assert seen == [] and paid(service) == 0
