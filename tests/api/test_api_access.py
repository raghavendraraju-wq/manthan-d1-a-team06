"""API access: staff see only their role's endpoints; customers only their own order (via one-time code)."""
import pytest

from tests.api.conftest import auth, customer_login

AGENT_URLS = [("get", "/api/agent/queue"), ("post", "/api/agent/scan")]
FINANCE_URLS = [("get", "/api/finance/queue"), ("post", "/api/finance/items/X/reject")]
MANAGER_URLS = [("get", "/api/manager/dashboard"), ("get", "/api/manager/items/X/audit")]
CUSTOMER_URLS = [("get", "/api/orders/RR-10482"), ("post", "/api/returns")]


def call(client, method, url, headers):
    return getattr(client, method)(url, headers=headers, **({"json": {}} if method == "post" else {}))


@pytest.mark.parametrize("method, url", AGENT_URLS + FINANCE_URLS + MANAGER_URLS + CUSTOMER_URLS)
def test_no_token_is_401(client, method, url):
    assert call(client, method, url, {}).status_code == 401


def test_each_role_only_reaches_its_own_endpoints(client, agent, finance, manager, customer):
    roles = {"agent": (agent, AGENT_URLS), "finance": (finance, FINANCE_URLS), "manager": (manager, MANAGER_URLS),
             "customer": (customer, CUSTOMER_URLS)}
    for role, (headers, _) in roles.items():
        for other, (_, urls) in roles.items():
            if other == role:
                continue
            for method, url in urls:
                assert call(client, method, url, headers).status_code == 403, (role, url)


def test_bad_or_expired_token_is_401(client):
    assert client.get("/api/agent/queue", headers=auth("not-a-jwt")).status_code == 401


def test_wrong_password(client):
    r = client.post("/api/auth/login", json={"email": "arjun@returnright.test", "password": "wrong"})
    assert r.status_code == 401 and "couldn't sign you in" in r.json()["detail"]


def test_otp_request_does_not_reveal_whether_order_matches(client, mailer):
    good = client.post("/api/auth/otp/request", json={"order_no": "RR-10482", "email": "priya.s@example.com"})
    bad = client.post("/api/auth/otp/request", json={"order_no": "RR-10482", "email": "someone@example.com"})
    assert good.status_code == bad.status_code == 202 and good.json() == bad.json()
    assert "someone@example.com" not in mailer.last


def test_wrong_code_and_reused_code_fail(client, mailer):
    client.post("/api/auth/otp/request", json={"order_no": "RR-10482", "email": "priya.s@example.com"})
    code = client.get("/api/demo/last-code", params={"email": "priya.s@example.com"}).json()["code"]
    wrong = "000000" if code != "000000" else "111111"
    body = {"order_no": "rr-10482", "email": " Priya.S@Example.com ", "code": wrong}
    assert client.post("/api/auth/otp/verify", json=body).status_code == 401
    assert client.post("/api/auth/otp/verify", json={**body, "code": code}).status_code == 200
    assert client.post("/api/auth/otp/verify", json={**body, "code": code}).status_code == 401


def test_customer_cannot_open_another_order(client, mailer, customer):
    assert client.get("/api/orders/RR-10483", headers=customer).status_code == 404
    other = customer_login(client, mailer, "RR-19001", "rahul.k@example.com")
    assert client.get("/api/orders/RR-10482", headers=other).status_code == 404


def test_system_endpoints_need_system_token(client):
    assert client.post("/api/system/reconcile").status_code == 403
    assert client.post("/api/system/reconcile", headers={"X-System-Token": "nope"}).status_code == 403


def test_invalid_payloads_are_422(client, agent):
    r = client.post("/api/agent/returns/RTN-1/inspect", headers=agent,
                    json={"key_seed": "abcdefgh", "items": [{"item_id": "x", "grade": "D", "decision": "accept"}]})
    assert r.status_code == 422 and r.json()["error"] == "Invalid"
