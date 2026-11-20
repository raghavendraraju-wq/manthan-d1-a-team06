"""Malformed events are refused with MalformedEvent and change nothing."""
import pytest

from returnright.contracts import ItemState as S, MalformedEvent
from returnright.events import parse_event
from tests.builders import raw, requested

GOOD_REQUEST = {
    "return_id": "RTN-1", "customer_id": "C-1", "order_no": "RR-10482",
    "category": "apparel", "price_inr": "1899", "delivered_at": "2026-11-12T15:00:00+05:30",
    "refund_method": "original_payment",
}


def _with(**changes):
    p = dict(GOOD_REQUEST)
    for k, v in changes.items():
        if v is ...:
            p.pop(k)
        else:
            p[k] = v
    return raw("RETURN_REQUESTED", actor="customer", payload=p)


MALFORMED = {
    "not a dict": "QR_SCANNED",
    "missing type": {k: v for k, v in raw("QR_SCANNED").items() if k != "type"},
    "unknown type": raw("TELEPORTED"),
    "missing item_id": {k: v for k, v in raw("QR_SCANNED").items() if k != "item_id"},
    "empty item_id": raw("QR_SCANNED", item=""),
    "bad timestamp": raw("QR_SCANNED", at="20/11/2026 10am"),
    "timestamp without offset": raw("QR_SCANNED", at="2026-11-20T10:00:00"),
    "unknown actor": raw("QR_SCANNED", actor="intern"),
    "payload not a dict": {**raw("QR_SCANNED"), "payload": ["grade", "A"]},
    "grade D": raw("INSPECTED", payload={"grade": "D", "decision": "accept"}),
    "missing grade": raw("INSPECTED", payload={"decision": "accept"}),
    "decision maybe": raw("INSPECTED", payload={"grade": "A", "decision": "maybe"}),
    "refund without key": raw("REFUND_REQUESTED", actor="system", payload={}),
    "blank key": raw("REFUND_REQUESTED", actor="system", payload={"idempotency_key": "  "}),
    "price not a number": _with(price_inr="abc"),
    "negative price": _with(price_inr="-1"),
    "zero price": _with(price_inr="0"),
    "unknown category": _with(category="weapons"),
    "unknown refund method": _with(refund_method="cash"),
    "missing return_id": _with(return_id=...),
    "delivered_at bad": _with(delivered_at="yesterday"),
}


@pytest.mark.parametrize("bad", MALFORMED.values(), ids=MALFORMED.keys())
def test_parse_rejects(bad):
    with pytest.raises(MalformedEvent):
        parse_event(bad)


def test_parse_accepts_well_formed_request():
    event = parse_event(_with())
    assert event.item_id == "ITM-1"
    assert event.at.utcoffset() is not None


@pytest.mark.parametrize("bad", [MALFORMED["grade D"], MALFORMED["bad timestamp"], MALFORMED["unknown type"]],
                         ids=["grade D", "bad timestamp", "unknown type"])
def test_engine_refuses_malformed_without_side_effects(engine, gateway, bad):
    engine.apply(requested("ITM-1"))
    audit_before = len(engine.audit_log("ITM-1"))
    with pytest.raises(MalformedEvent):
        engine.apply_raw(bad)
    assert engine.state("ITM-1") == S.APPROVED
    assert len(engine.audit_log("ITM-1")) == audit_before
    assert gateway.calls == []
    assert engine.rejected_events()[-1][1] == "MalformedEvent"
