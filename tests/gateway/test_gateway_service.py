"""Mock payment gateway service: dedupes on the key (also across restarts), and can be broken on purpose."""
import threading

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "gateway.db")


@pytest.fixture
def gw(db):
    return TestClient(create_app(db_path=db, admin_token=""))


def pay(client, key="k-1", amount="1899", item="I1"):
    return client.post("/payments", json={"idempotency_key": key, "amount": amount, "item_id": item})


def behave(client, **b):
    assert client.put("/admin/behaviour", json=b).status_code == 200


def test_pays_and_answers(gw):
    r = pay(gw)
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["ref"], body["replayed"]) == ("succeeded", "GW-000001", False)


def test_same_key_returns_stored_result_without_paying_again(gw):
    first = pay(gw).json()
    second = pay(gw).json()
    assert second["ref"] == first["ref"] and second["replayed"] is True
    assert gw.get("/admin/behaviour").json()["payments_made"] == 1


def test_same_key_different_amount_is_refused(gw):
    pay(gw)
    assert pay(gw, amount="2000").status_code == 409
    assert pay(gw, item="I2").status_code == 409


def test_dedupe_survives_restart(db):
    first = pay(TestClient(create_app(db_path=db, admin_token=""))).json()
    restarted = TestClient(create_app(db_path=db, admin_token=""))
    assert pay(restarted).json()["ref"] == first["ref"]
    assert restarted.get("/admin/behaviour").json()["payments_made"] == 1


def test_status_lookup(gw):
    assert gw.get("/payments/k-1").status_code == 404
    pay(gw)
    assert gw.get("/payments/k-1").json()["status"] == "succeeded"


def test_decline_is_stored(gw):
    behave(gw, mode="decline")
    assert pay(gw).json()["status"] == "declined"
    behave(gw, mode="succeed")
    assert pay(gw).json()["status"] == "declined"     # same key: stored result
    assert gw.get("/admin/behaviour").json()["payments_made"] == 0


def test_timeout_after_pay_has_paid(gw):
    behave(gw, mode="timeout_after_pay", times=1)
    assert pay(gw).status_code == 504
    assert gw.get("/payments/k-1").json()["status"] == "succeeded"
    assert pay(gw).json()["replayed"] is True


@pytest.mark.parametrize("mode, code", [("timeout_before_pay", 504), ("error", 500)])
def test_failures_before_pay_leave_no_payment(gw, mode, code):
    behave(gw, mode=mode, times=1)
    assert pay(gw).status_code == code
    assert gw.get("/payments/k-1").status_code == 404
    assert pay(gw).json()["status"] == "succeeded"    # 'times' used up: back to succeed


def test_mode_without_times_sticks(gw):
    behave(gw, mode="error")
    assert pay(gw, key="a").status_code == 500
    assert pay(gw, key="b").status_code == 500


@pytest.mark.parametrize("body", [
    {"idempotency_key": "", "amount": "1", "item_id": "I1"},
    {"idempotency_key": "   ", "amount": "1", "item_id": "I1"},
    {"idempotency_key": "k", "amount": "0", "item_id": "I1"},
    {"idempotency_key": "k", "amount": "-5", "item_id": "I1"},
    {"idempotency_key": "k", "amount": "1.001", "item_id": "I1"},
    {"idempotency_key": "k", "amount": "abc", "item_id": "I1"},
    {"idempotency_key": "k", "amount": "1"},
])
def test_bad_requests_refused(gw, body):
    assert gw.post("/payments", json=body).status_code == 422


def test_admin_token_enforced_when_set(db):
    client = TestClient(create_app(db_path=db, admin_token="s3cret"))
    assert client.put("/admin/behaviour", json={"mode": "error"}).status_code == 401
    assert client.put("/admin/behaviour", json={"mode": "error"},
                      headers={"X-Admin-Token": "s3cret"}).status_code == 200


def test_concurrent_same_key_pays_once(db):
    app = create_app(db_path=db, admin_token="")
    barrier = threading.Barrier(8)
    refs = []

    def fire():
        client = TestClient(app)
        barrier.wait()
        refs.append(pay(client).json()["ref"])

    threads = [threading.Thread(target=fire) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(refs) == {"GW-000001"}
    assert TestClient(app).get("/admin/behaviour").json()["payments_made"] == 1
