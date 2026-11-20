"""API tests: the customer flow and all three internal roles, end to end over HTTP on a seeded database."""
import threading
import uuid

from tests.api.conftest import auth, customer_login

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SYSTEM = {"X-System-Token": "test-system"}


def start_return(client, customer, lines=(1, 2), method="original_payment"):
    items = [{"line_no": n, "reason": "size"} for n in lines]
    r = client.post("/api/returns", headers=customer,
                    json={"order_no": "RR-10482", "refund_method": method, "items": items})
    assert r.status_code == 201, r.text
    return r.json()


def by_name(view, name):
    return next(i for i in view["items"] if i["name"] == name)


def test_order_shows_eligibility_with_reasons(client, customer):
    order = client.get("/api/orders/RR-10482", headers=customer).json()
    verdicts = {line["name"]: line["eligibility"] for line in order["lines"]}
    assert verdicts["Cotton kurta"]["eligible"] and "22 days left" in verdicts["Cotton kurta"]["reason"]
    assert verdicts["Wireless headphones"]["eligible"] and "2 days left" in verdicts["Wireless headphones"]["reason"]
    assert not verdicts["Innerwear pack (3)"]["eligible"]
    assert verdicts["Innerwear pack (3)"]["rule"] == "NON_RETURNABLE_CATEGORY"


def test_server_rejects_ineligible_item_even_if_submitted(client, customer):
    view = start_return(client, customer, lines=(1, 3))
    inner = by_name(view, "Innerwear pack (3)")
    assert (inner["state"], inner["rejection_rule"]) == ("rejected", "NON_RETURNABLE_CATEGORY")
    assert "hygiene" in inner["rejection_text"]
    assert by_name(view, "Cotton kurta")["state"] == "approved"
    assert view["label"]["qr_svg"].startswith("<svg") and view["status"] == "Open"


def test_full_flow_all_roles(client, customer, agent, finance, manager, gateway):
    view = start_return(client, customer, lines=(1, 2, 3))
    rid = view["return_id"]

    # mock carrier
    assert len(client.post("/api/system/carrier/pickup", headers=SYSTEM, json={"return_id": rid}).json()["picked_up"]) == 2

    # warehouse agent scans and grades
    scanned = client.post("/api/agent/scan", headers=agent, json={"code": rid}).json()
    assert {i["state"] for i in scanned["items"] if i["name"] != "Innerwear pack (3)"} == {"received"}
    items = [{"item_id": by_name(scanned, n)["item_id"], "grade": "A", "decision": "accept"}
             for n in ("Cotton kurta", "Wireless headphones")]
    graded = client.post(f"/api/agent/returns/{rid}/inspect", headers=agent,
                         json={"key_seed": uuid.uuid4().hex, "items": items}).json()
    assert by_name(graded, "Cotton kurta")["state"] == "refunded"            # <= 10k: straight to refund
    assert by_name(graded, "Wireless headphones")["state"] == "pending_finance"
    assert len(gateway.payments) == 1

    # finance approves, double click pays once
    hp = by_name(graded, "Wireless headphones")["item_id"]
    queue = client.get("/api/finance/queue", headers=finance).json()
    assert hp in [q["item_id"] for q in queue["items"]]
    key = uuid.uuid4().hex
    for _ in range(2):
        r = client.post(f"/api/finance/items/{hp}/approve", headers=finance, json={"idempotency_key": key})
        assert r.json()["state"] == "refunded" and r.json()["ledger_entries"] == 1
    assert len(gateway.payments) == 2

    # customer sees completed return and timeline
    final = client.get(f"/api/returns/{rid}", headers=customer).json()
    assert final["status"] == "Completed"
    assert [h["state"] for h in by_name(final, "Wireless headphones")["history"]] == [
        "requested", "approved", "in_transit", "received", "pending_finance", "refund_pending", "refunded"]

    # manager audit trail is continuous
    trail = client.get(f"/api/manager/items/{hp}/audit", headers=manager).json()["entries"]
    assert trail[0]["from"] is None and trail[-1]["to"] == "refunded"
    assert all(a["to"] == b["from"] for a, b in zip(trail, trail[1:]))


def test_gateway_timeout_then_retry_same_key_pays_once(client, customer, agent, finance, gateway):
    rid = start_return(client, customer, lines=(2,))["return_id"]
    scanned = client.post("/api/agent/scan", headers=agent, json={"code": rid}).json()
    hp = scanned["items"][0]["item_id"]
    client.post(f"/api/agent/returns/{rid}/inspect", headers=agent,
                json={"key_seed": uuid.uuid4().hex, "items": [{"item_id": hp, "grade": "A", "decision": "accept"}]})
    gateway.mode = "timeout_after_pay"
    r = client.post(f"/api/finance/items/{hp}/approve", headers=finance, json={"idempotency_key": "fin-key-0001"})
    assert r.json()["state"] == "refund_pending" and r.json()["ledger_entries"] == 0
    r = client.post(f"/api/finance/items/{hp}/retry", headers=finance)
    assert r.json()["state"] == "refunded" and r.json()["ledger_entries"] == 1
    assert len(gateway.payments) == 1


def test_reconcile_job_settles_pending(client, customer, agent, gateway):
    rid = start_return(client, customer, lines=(1,))["return_id"]
    item = client.post("/api/agent/scan", headers=agent, json={"code": rid}).json()["items"][0]["item_id"]
    gateway.mode = "timeout_after_pay"
    graded = client.post(f"/api/agent/returns/{rid}/inspect", headers=agent, json={
        "key_seed": uuid.uuid4().hex, "items": [{"item_id": item, "grade": "C", "decision": "accept"}]}).json()
    assert graded["items"][0]["state"] == "refund_pending"
    assert client.post("/api/system/reconcile", headers=SYSTEM).json()["settled"] == 1
    assert client.get(f"/api/returns/{rid}", headers=customer).json()["items"][0]["amount"] == "949.50"
    assert len(gateway.payments) == 1


def test_concurrent_finance_approvals_pay_once(client, customer, agent, finance, gateway):
    rid = start_return(client, customer, lines=(2,))["return_id"]
    hp = client.post("/api/agent/scan", headers=agent, json={"code": rid}).json()["items"][0]["item_id"]
    client.post(f"/api/agent/returns/{rid}/inspect", headers=agent, json={
        "key_seed": uuid.uuid4().hex, "items": [{"item_id": hp, "grade": "A", "decision": "accept"}]})
    barrier = threading.Barrier(4)
    codes = []

    def fire(key):
        barrier.wait()
        codes.append(client.post(f"/api/finance/items/{hp}/approve", headers=finance,
                                 json={"idempotency_key": key}).status_code)

    threads = [threading.Thread(target=fire, args=(k,)) for k in ("key-aaaa1", "key-aaaa1", "key-bbbb2", "key-cccc3")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(gateway.payments) == 1
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}


def test_scan_refusals_explain_themselves(client, customer, agent):
    r = client.post("/api/agent/scan", headers=agent, json={"code": "RTN-99999"})
    assert r.status_code == 404 and "No return found" in r.json()["detail"]
    view = start_return(client, customer, lines=(1,))
    client.post(f"/api/returns/{view['return_id']}/items/{view['items'][0]['item_id']}/cancel", headers=customer)
    r = client.post("/api/agent/scan", headers=agent, json={"code": view["return_id"]})
    assert r.status_code == 409 and "Scanning only works" in r.json()["detail"]


def test_customer_can_cancel_only_before_received(client, customer, agent):
    view = start_return(client, customer, lines=(1, 2))
    rid = view["return_id"]
    kurta = by_name(view, "Cotton kurta")["item_id"]
    assert client.post(f"/api/returns/{rid}/items/{kurta}/cancel", headers=customer).status_code == 200
    client.post("/api/agent/scan", headers=agent, json={"code": rid})
    hp = by_name(view, "Wireless headphones")["item_id"]
    r = client.post(f"/api/returns/{rid}/items/{hp}/cancel", headers=customer)
    assert r.status_code == 409 and r.json()["error"] == "IllegalTransition"


def test_item_cannot_be_returned_twice(client, customer):
    start_return(client, customer, lines=(1,))
    r = client.post("/api/returns", headers=customer, json={
        "order_no": "RR-10482", "refund_method": "store_credit", "items": [{"line_no": 1, "reason": "size"}]})
    assert r.status_code == 409 and "already in return" in r.json()["detail"]


def test_store_credit_never_calls_gateway(client, customer, agent, gateway):
    rid = start_return(client, customer, lines=(1,), method="store_credit")["return_id"]
    item = client.post("/api/agent/scan", headers=agent, json={"code": rid}).json()["items"][0]["item_id"]
    graded = client.post(f"/api/agent/returns/{rid}/inspect", headers=agent, json={
        "key_seed": uuid.uuid4().hex, "items": [{"item_id": item, "grade": "B", "decision": "accept"}]}).json()
    assert graded["items"][0]["state"] == "refunded" and gateway.calls == []


def test_regrade_and_double_submit_are_safe(client, customer, agent, gateway):
    rid = start_return(client, customer, lines=(1,))["return_id"]
    item = client.post("/api/agent/scan", headers=agent, json={"code": rid}).json()["items"][0]["item_id"]
    body = {"key_seed": "seed-0001-abcd", "items": [{"item_id": item, "grade": "A", "decision": "accept"}]}
    first = client.post(f"/api/agent/returns/{rid}/inspect", headers=agent, json=body).json()
    second = client.post(f"/api/agent/returns/{rid}/inspect", headers=agent, json=body).json()
    assert first["results"][0]["state"] == "refunded"
    assert second["results"][0]["error"] == "IllegalTransition"
    assert len(gateway.payments) == 1


def test_photo_upload_validates_type_and_ownership(client, customer, agent, finance, mailer):
    r = client.post("/api/photos", headers=customer, files={"file": ("p.png", PNG, "image/png")})
    assert r.status_code == 201
    photo = r.json()["photo_id"]
    assert client.post("/api/photos", headers=customer,
                       files={"file": ("x.png", b"not an image", "image/png")}).status_code == 409
    assert client.post("/api/photos", headers=customer,
                       files={"file": ("x.gif", b"GIF89a", "image/gif")}).status_code == 409
    r = client.post("/api/returns", headers=customer, json={
        "order_no": "RR-10482", "refund_method": "original_payment",
        "items": [{"line_no": 1, "reason": "damaged", "photo_id": photo}]})
    assert r.json()["items"][0]["photo_id"] == photo
    assert client.get(f"/api/photos/{photo}", headers=finance).content == PNG
    other = customer_login(client, mailer, "RR-19001", "rahul.k@example.com")
    assert client.get(f"/api/photos/{photo}", headers=other).status_code == 404


def test_dashboard_has_every_panel(client, manager):
    for days in (7, 30, 90):
        d = client.get("/api/manager/dashboard", params={"days": days}, headers=manager).json()
        assert {"return_rate", "avg_cycle_days", "refund_success_rate", "waiting_for_finance"} <= set(d["tiles"])
        assert len(d["by_category"]) == 6 and d["stages"]
    d = client.get("/api/manager/dashboard", headers=manager).json()
    assert d["tiles"]["waiting_for_finance"] >= 2
    assert "Ananya D." in [f["name"] for f in d["flagged"]]
    assert client.get("/api/manager/dashboard", params={"days": 5}, headers=manager).status_code == 422


def test_metrics_exposed_for_prometheus(client, customer, agent, gateway):
    rid = start_return(client, customer, lines=(1,))["return_id"]
    body = client.get("/metrics").text
    for name in ("returnright_refunds_total", "returnright_gateway_request_duration_seconds_bucket",
                 "returnright_events_refused_total"):
        assert name in body
