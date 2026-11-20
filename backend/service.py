"""Application service: turns API calls into engine events and builds read models.

The UI never sets a state; every change is an event through ReturnEngine (ADR-002).
"""
from __future__ import annotations

import io
import secrets
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import qrcode
import qrcode.image.svg
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from returnright import eligibility, pricing
from returnright.access import OtpService, normalize_email
from returnright.contracts import (
    IST, TERMINAL_STATES, Category, Clock, Event, EventType, ItemState as S, RefundMethod,
    ReturnRightError, Role,
)
from returnright.engine import ReturnEngine

from .db.models import (
    AuditLog, Customer, LedgerEntry, Order, OrderLine, Photo, Return, ReturnItem, StaffUser,
)
from .metrics import Metrics

REASONS = {
    "size": "Wrong size or fit",
    "damaged": "Arrived damaged",
    "not-described": "Not as described",
    "changed-mind": "Changed my mind",
}
STATE_LABELS = {
    "requested": "Requested", "approved": "Approved", "in_transit": "In transit", "received": "Received",
    "inspected": "Inspected", "pending_finance": "Pending finance", "refund_pending": "Refund pending",
    "refund_failed": "Refund failed", "refunded": "Refunded", "rejected": "Rejected",
    "cancelled": "Cancelled", "expired": "Expired",
}
RULE_TEXT = {
    "INSPECTION_REJECTED": "Rejected at inspection by the warehouse.",
    "FINANCE_REJECTED": "Refund rejected by finance after review.",
}
CATEGORY_LABELS = {c.value: c.value.capitalize() for c in Category}
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
PHOTO_MAGIC = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/webp": (b"RIFF",)}
SCANNABLE = {S.APPROVED, S.IN_TRANSIT}
GRADEABLE = {S.RECEIVED, S.INSPECTED, S.PENDING_FINANCE}


class NotFound(Exception):
    pass


class Refused(Exception):
    """A request the user can fix; the message is shown as is."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class OrderEmails(Mapping):
    """order number -> email on the order, read from the database (for OtpService)."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def __getitem__(self, order_no: str) -> str:
        with self.sessions() as s:
            email = s.scalar(select(Order.email).where(Order.order_no == order_no))
        if email is None:
            raise KeyError(order_no)
        return email

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        with self.sessions() as s:
            return s.scalar(select(func.count()).select_from(Order)) or 0


def money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def qr_svg(text: str) -> str:
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    image.save(buf)
    svg = buf.getvalue().decode()
    return svg[svg.index("<svg"):]


@dataclass
class Services:
    engine: ReturnEngine
    sessions: sessionmaker[Session]
    clock: Clock
    otp: OtpService
    metrics: Metrics
    upload_dir: Path
    max_upload_bytes: int
    expiry_days: int
    carrier_delay_s: int

    # ------------------------------------------------------------------ helpers

    def now(self) -> datetime:
        return self.clock.now()

    def _apply(self, event: Event) -> S:
        try:
            return self.engine.apply(event)
        except ReturnRightError as exc:
            self.metrics.refused.labels(type(exc).__name__).inc()
            raise

    def _event(self, type_: EventType, item_id: str, actor: Role, **payload) -> Event:
        return Event(type_, item_id, self.now(), actor, payload)

    # ------------------------------------------------------------------ staff

    def staff_user(self, email: str) -> StaffUser | None:
        with self.sessions() as s:
            return s.get(StaffUser, normalize_email(email))

    # ------------------------------------------------------------------ customer

    def order_view(self, order_no: str) -> dict:
        now = self.now()
        with self.sessions() as s:
            order = s.get(Order, order_no)
            if order is None:
                raise NotFound(order_no)
            customer = s.get(Customer, order.customer_id)
            lines = s.execute(select(OrderLine).where(OrderLine.order_no == order_no)
                              .order_by(OrderLine.line_no)).scalars().all()
            items = s.execute(select(ReturnItem).where(ReturnItem.order_no == order_no)).scalars().all()
            return_ids = s.execute(select(Return.return_id).where(Return.order_no == order_no)
                                   .order_by(Return.id.desc())).scalars().all()
        active = {i.order_line_id: i for i in items if i.state not in (S.CANCELLED.value, S.EXPIRED.value)}
        out_lines = []
        for line in lines:
            existing = active.get(line.id)
            if order.delivered_at is None:
                verdict = {"eligible": False, "rule": "NOT_DELIVERED", "reason": "Not delivered yet",
                           "last_day": None, "days_left": None}
            else:
                e = eligibility.check(Category(line.category), order.delivered_at, now)
                verdict = {"eligible": e.eligible, "rule": e.rule, "reason": e.reason,
                           "last_day": e.last_day.isoformat() if e.last_day else None, "days_left": e.days_left}
            if existing is not None:
                verdict = {**verdict, "eligible": False, "rule": "ALREADY_RETURNED",
                           "reason": f"Already in return {existing.return_id} · {STATE_LABELS[existing.state]}"}
            out_lines.append({
                "line_no": line.line_no, "sku": line.sku, "name": line.product_name, "category": line.category,
                "category_label": CATEGORY_LABELS[line.category], "price": money(line.price),
                "eligibility": verdict, "return_id": existing.return_id if existing else None,
            })
        return {
            "order_no": order.order_no, "customer_name": customer.name if customer else "",
            "ordered_at": order.ordered_at.isoformat(),
            "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
            "lines": out_lines, "returns": return_ids,
        }

    def save_photo(self, order_no: str, content_type: str, data: bytes) -> str:
        if content_type not in PHOTO_TYPES:
            raise Refused("Photos must be JPEG, PNG or WebP.")
        if len(data) == 0 or len(data) > self.max_upload_bytes:
            raise Refused(f"Photos must be smaller than {self.max_upload_bytes // (1024 * 1024)} MB.")
        if not any(data.startswith(m) for m in PHOTO_MAGIC[content_type]):
            raise Refused("That file doesn't look like the image type it claims to be.")
        photo_id = uuid.uuid4().hex
        filename = photo_id + PHOTO_TYPES[content_type]
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        (self.upload_dir / filename).write_bytes(data)
        with self.sessions() as s, s.begin():
            s.add(Photo(id=photo_id, order_no=order_no, filename=filename, content_type=content_type,
                        created_at=self.now()))
        return photo_id

    def photo(self, photo_id: str) -> tuple[Photo, Path]:
        with self.sessions() as s:
            photo = s.get(Photo, photo_id)
        if photo is None:
            raise NotFound(photo_id)
        return photo, self.upload_dir / photo.filename

    def create_return(self, order_no: str, refund_method: str, items: list[dict]) -> dict:
        try:
            method = RefundMethod(refund_method)
        except ValueError:
            raise Refused("Choose a refund method: original payment or store credit.") from None
        if not items:
            raise Refused("Select at least one item to return.")
        line_nos = [i["line_no"] for i in items]
        if len(set(line_nos)) != len(line_nos):
            raise Refused("Each item can only be listed once.")
        view = self.order_view(order_no)
        if view["delivered_at"] is None:
            raise Refused("This order hasn't been delivered yet, so it can't be returned.")
        lines = {line["line_no"]: line for line in view["lines"]}
        with self.sessions() as s:
            order = s.get(Order, order_no)
            line_rows = {r.line_no: r for r in s.execute(
                select(OrderLine).where(OrderLine.order_no == order_no)).scalars()}
            photo_orders = dict(s.execute(select(Photo.id, Photo.order_no).where(
                Photo.id.in_([i.get("photo_id") for i in items if i.get("photo_id")]))).all())
        for req in items:
            line = lines.get(req["line_no"])
            if line is None:
                raise Refused(f"Item {req['line_no']} isn't on order {order_no}.")
            if line["return_id"]:
                raise Refused(f"{line['name']} is already in return {line['return_id']}.")
            if req.get("reason") not in REASONS:
                raise Refused("Choose a reason for each item before you continue.")
            if req.get("photo_id") and photo_orders.get(req["photo_id"]) != order_no:
                raise Refused("That photo doesn't belong to this order. Upload it again.")

        with self.sessions() as s, s.begin():
            ret = Return(return_id=f"TMP-{secrets.token_hex(6)}", order_no=order_no,
                         customer_id=order.customer_id, refund_method=method.value, created_at=self.now())
            s.add(ret)
            s.flush()
            ret.return_id = f"RTN-{20000 + ret.id}"
            return_id = ret.return_id

        for req in items:
            row = line_rows[req["line_no"]]
            self._apply(self._event(
                EventType.RETURN_REQUESTED, f"{return_id}-{row.line_no}", Role.CUSTOMER,
                return_id=return_id, customer_id=order.customer_id, order_no=order_no,
                category=row.category, price_inr=row.price, delivered_at=order.delivered_at,
                refund_method=method.value, reason=req["reason"], photo_id=req.get("photo_id"),
                product_name=row.product_name, order_line_id=row.id, line_no=row.line_no,
            ))
        return self.return_view(return_id)

    def return_view(self, return_id: str, with_label: bool = True) -> dict:
        with self.sessions() as s:
            ret = s.scalar(select(Return).where(Return.return_id == return_id))
            if ret is None:
                raise NotFound(return_id)
            customer = s.get(Customer, ret.customer_id)
            items = s.execute(select(ReturnItem).where(ReturnItem.return_id == return_id)
                              .order_by(ReturnItem.item_id)).scalars().all()
            audits = s.execute(select(AuditLog).where(AuditLog.item_id.in_([i.item_id for i in items]))
                               .order_by(AuditLog.id)).scalars().all()
        history = defaultdict(list)
        for a in audits:
            history[a.item_id].append({"state": a.to_state, "label": STATE_LABELS[a.to_state],
                                       "at": a.at.isoformat(), "actor": a.actor, "event": a.event_type})
        out_items = [self._item_view(i, history[i.item_id]) for i in items]
        states = {S(i.state) for i in items}
        status = "Completed" if items and all(st in TERMINAL_STATES for st in states) else "Open"
        flagged = self.engine.flags.is_flagged(ret.customer_id)
        view = {
            "return_id": ret.return_id, "order_no": ret.order_no, "customer_id": ret.customer_id,
            "customer_name": customer.name if customer else "", "customer_flagged": flagged,
            "customer_returns_30d": self.engine.flags.count(ret.customer_id),
            "refund_method": ret.refund_method, "created_at": ret.created_at.isoformat(),
            "status": status, "items": out_items,
            "refund_total": money(sum((Decimal(i["amount"]) for i in out_items
                                       if i["amount"] and i["state"] not in ("rejected", "cancelled", "expired")),
                                      Decimal("0"))),
        }
        if with_label and any(i.state != S.REJECTED.value or i.rejection_rule not in
                              ("NON_RETURNABLE_CATEGORY", "WINDOW_EXPIRED") for i in items):
            view["label"] = {"code": ret.return_id, "qr_svg": qr_svg(ret.return_id),
                             "to": "ReturnRight warehouse, Indore",
                             "voided": all(S(i.state) in (S.CANCELLED, S.EXPIRED, S.REJECTED) for i in items)}
        return view

    def _item_view(self, row: ReturnItem, history: list[dict]) -> dict:
        extra = row.extra or {}
        went_finance = any(h["state"] == "pending_finance" for h in history) or (
            row.amount is not None and pricing.needs_finance(row.amount))
        path = ["requested", "approved", "in_transit", "received", "inspected"]
        if went_finance or (row.amount is None and pricing.needs_finance(row.price)):
            path.append("pending_finance")
        path.append("refunded")
        rule = row.rejection_rule
        if rule in ("NON_RETURNABLE_CATEGORY", "WINDOW_EXPIRED"):
            rule_text = eligibility.check(Category(row.category), row.delivered_at, row.requested_at).reason
        else:
            rule_text = RULE_TEXT.get(rule) if rule else None
        return {
            "item_id": row.item_id, "name": extra.get("product_name", row.item_id), "category": row.category,
            "category_label": CATEGORY_LABELS[row.category], "price": money(row.price), "state": row.state,
            "state_label": STATE_LABELS[row.state], "grade": row.grade, "amount": money(row.amount),
            "needs_finance": bool(row.amount is not None and pricing.needs_finance(row.amount)),
            "may_need_finance": pricing.needs_finance(row.price),
            "reason": row.reason, "reason_label": REASONS.get(row.reason or "", row.reason),
            "photo_id": extra.get("photo_id"), "refund_method": row.refund_method,
            "rejection_rule": rule, "rejection_text": rule_text, "refund_key": row.refund_key,
            "refund_keys_used": len(row.refund_keys or []), "gateway_ref": row.gateway_ref,
            "requested_at": row.requested_at.isoformat(), "path": path, "history": history,
            "can_cancel": S(row.state) in (S.APPROVED, S.IN_TRANSIT),
        }

    def cancel_item(self, return_id: str, item_id: str) -> dict:
        self._item_in_return(return_id, item_id)
        self._apply(self._event(EventType.CUSTOMER_CANCELLED, item_id, Role.CUSTOMER))
        return self.return_view(return_id)

    def _item_in_return(self, return_id: str, item_id: str) -> None:
        item = self.engine.store.get_item(item_id)
        if item is None or item.return_id != return_id:
            raise NotFound(item_id)

    def return_order_no(self, return_id: str) -> str:
        with self.sessions() as s:
            order_no = s.scalar(select(Return.order_no).where(Return.return_id == return_id))
        if order_no is None:
            raise NotFound(return_id)
        return order_no

    # ------------------------------------------------------------------ warehouse agent

    def agent_queue(self) -> dict:
        with self.sessions() as s:
            rows = s.execute(select(ReturnItem.return_id, ReturnItem.state, func.count())
                             .where(ReturnItem.state.in_([st.value for st in (S.APPROVED, S.IN_TRANSIT, S.RECEIVED,
                                                                              S.INSPECTED, S.PENDING_FINANCE)]))
                             .group_by(ReturnItem.return_id, ReturnItem.state)).all()
        by_return: dict[str, dict] = defaultdict(lambda: {"states": {}, "items": 0})
        for return_id, state, n in rows:
            by_return[return_id]["states"][STATE_LABELS[state]] = n
            by_return[return_id]["items"] += n
        def bucket(states: dict) -> str:
            if "Received" in states:
                return "to_grade"
            if "In transit" in states:
                return "in_transit"
            if "Approved" in states:
                return "awaiting_pickup"
            return "graded"
        out = defaultdict(list)
        for return_id, info in sorted(by_return.items()):
            out[bucket(info["states"])].append({"return_id": return_id, **info})
        return {k: out.get(k, [])[:50] for k in ("to_grade", "in_transit", "awaiting_pickup")}

    def agent_scan(self, code: str) -> dict:
        return_id = code.strip().upper().removeprefix("RETURNRIGHT:")
        with self.sessions() as s:
            exists = s.scalar(select(Return.id).where(Return.return_id == return_id))
        if not exists:
            raise NotFound(f"No return found for “{code.strip()}”. Check the code on the label and try again.")
        items = self.engine.store.items_in_return(return_id)
        scanned = []
        for item in items:
            if item.state in SCANNABLE:
                self._apply(self._event(EventType.QR_SCANNED, item.item_id, Role.AGENT))
                scanned.append(item.item_id)
        if not scanned and not any(i.state in GRADEABLE for i in items):
            states = sorted({STATE_LABELS[i.state.value] for i in items})
            raise Refused(f"{return_id} is already {' / '.join(states)}. Scanning only works for returns that are "
                          f"Approved or In transit, so nothing was changed.")
        view = self.return_view(return_id, with_label=False)
        view["scanned"] = scanned
        return view

    def agent_inspect(self, return_id: str, key_seed: str, decisions: list[dict]) -> dict:
        if not decisions:
            raise Refused("Grade at least one item.")
        results = []
        for d in decisions:
            item_id = d["item_id"]
            self._item_in_return(return_id, item_id)
            result = {"item_id": item_id}
            try:
                state = self._apply(self._event(EventType.INSPECTED, item_id, Role.AGENT,
                                                grade=d["grade"], decision=d["decision"]))
                if state is S.INSPECTED:
                    # Decision D2: refunds up to INR 10,000 start straight after inspection.
                    state = self._apply(self._event(EventType.REFUND_REQUESTED, item_id, Role.SYSTEM,
                                                    idempotency_key=f"{key_seed}:{item_id}"))
                result["state"] = state.value
            except ReturnRightError as exc:
                result["error"] = type(exc).__name__
                result["message"] = _friendly(exc, self.engine.state(item_id))
            results.append(result)
        view = self.return_view(return_id, with_label=False)
        view["results"] = results
        return view

    # ------------------------------------------------------------------ finance

    def finance_queue(self) -> dict:
        states = [S.PENDING_FINANCE.value, S.REFUND_PENDING.value, S.REFUND_FAILED.value]
        with self.sessions() as s:
            rows = s.execute(select(ReturnItem, Customer.name).join(Customer, Customer.id == ReturnItem.customer_id)
                             .where(ReturnItem.state.in_(states)).order_by(ReturnItem.updated_at)).all()
        queue = []
        for row, name in rows:
            item = self._item_view(row, [])
            item.update({"return_id": row.return_id, "customer_id": row.customer_id, "customer_name": name,
                         "customer_flagged": self.engine.flags.is_flagged(row.customer_id),
                         "customer_returns_30d": self.engine.flags.count(row.customer_id)})
            queue.append(item)
        return {"items": queue, "pending_count": sum(1 for q in queue if q["state"] == "pending_finance")}

    def finance_decide(self, item_id: str, approve: bool, idempotency_key: str | None = None) -> dict:
        if approve:
            if not idempotency_key:
                raise Refused("Missing idempotency key; reload the page and try again.")
            state = self._apply(self._event(EventType.FINANCE_APPROVED, item_id, Role.FINANCE,
                                            idempotency_key=idempotency_key))
        else:
            state = self._apply(self._event(EventType.FINANCE_REJECTED, item_id, Role.FINANCE))
        return self._finance_item(item_id, state)

    def finance_retry(self, item_id: str) -> dict:
        item = self.engine.get_item(item_id)
        if item.state is not S.REFUND_PENDING or not item.refund_key:
            raise Refused("Only a refund that is still pending can be retried with the same key.")
        state = self._apply(self._event(EventType.FINANCE_APPROVED, item_id, Role.FINANCE,
                                        idempotency_key=item.refund_key))
        return self._finance_item(item_id, state)

    def _finance_item(self, item_id: str, state: S) -> dict:
        with self.sessions() as s:
            row = s.get(ReturnItem, item_id)
            ledger = s.execute(select(func.count()).select_from(LedgerEntry)
                               .where(LedgerEntry.return_item_id == item_id)).scalar()
        view = self._item_view(row, [])
        view.update({"return_id": row.return_id, "ledger_entries": ledger, "outcome": state.value})
        return view

    # ------------------------------------------------------------------ manager

    def dashboard(self, days: int) -> dict:
        now = self.now()
        since = now - timedelta(days=days)
        with self.sessions() as s:
            items = s.execute(select(ReturnItem).where(ReturnItem.requested_at >= since)).scalars().all()
            delivered = s.execute(select(OrderLine.category, func.count()).join(Order)
                                  .where(Order.delivered_at >= since, Order.delivered_at <= now)
                                  .group_by(OrderLine.category)).all()
            audits = s.execute(select(AuditLog).where(AuditLog.item_id.in_([i.item_id for i in items]))
                               .order_by(AuditLog.id)).scalars().all()
            settled = s.execute(select(AuditLog.to_state, func.count())
                                .where(AuditLog.at >= since, AuditLog.from_state == S.REFUND_PENDING.value)
                                .group_by(AuditLog.to_state)).all()
            waiting = s.scalar(select(func.count()).select_from(ReturnItem)
                               .where(ReturnItem.state == S.PENDING_FINANCE.value))
            pending = s.scalar(select(func.count()).select_from(ReturnItem)
                               .where(ReturnItem.state == S.REFUND_PENDING.value))
            flagged = self.engine.flags.flagged_customers()
            names = dict(s.execute(select(Customer.id, Customer.name)
                                   .where(Customer.id.in_(list(flagged)))).all()) if flagged else {}
            refunded_by_customer = dict(s.execute(
                select(ReturnItem.customer_id, func.sum(LedgerEntry.amount))
                .join(LedgerEntry, LedgerEntry.return_item_id == ReturnItem.item_id)
                .where(ReturnItem.customer_id.in_(list(flagged)), LedgerEntry.created_at >= now - timedelta(days=30))
                .group_by(ReturnItem.customer_id)).all()) if flagged else {}

        sold = dict(delivered)
        returned = Counter(i.category for i in items)
        by_category = []
        for c in Category:
            n_sold, n_ret = sold.get(c.value, 0), returned.get(c.value, 0)
            by_category.append({"category": c.value, "label": CATEGORY_LABELS[c.value], "returned": n_ret,
                                "sold": n_sold, "rate": round(100 * n_ret / n_sold, 1) if n_sold else None})

        reasons = Counter(i.reason for i in items if i.reason)
        total_reasons = sum(reasons.values())
        top_reasons = [{"reason": r, "label": REASONS.get(r, r), "count": n,
                        "share": round(100 * n / total_reasons, 1)} for r, n in reasons.most_common()]

        first: dict[str, dict[str, datetime]] = defaultdict(dict)
        for a in audits:
            first[a.item_id].setdefault(a.to_state, a.at)
        stage_pairs = [("Requested → Approved", ("requested",), ("approved",)),
                       ("Approved → In transit", ("approved",), ("in_transit",)),
                       ("In transit → Received", ("in_transit",), ("received",)),
                       ("Received → Inspected", ("received",), ("inspected", "pending_finance")),
                       ("Pending finance → Approved", ("pending_finance",), ("refund_pending",)),
                       ("Inspected → Refunded", ("inspected", "pending_finance"), ("refunded",))]
        stages = []
        for label, starts, ends in stage_pairs:
            spans = []
            for entered in first.values():
                start = min((entered[k] for k in starts if k in entered), default=None)
                end = min((entered[k] for k in ends if k in entered), default=None)
                if start and end and end >= start:
                    spans.append((end - start).total_seconds() / 86400)
            stages.append({"stage": label, "avg_days": round(sum(spans) / len(spans), 2) if spans else None,
                           "items": len(spans)})
        cycles = [(e["refunded"] - e["requested"]).total_seconds() / 86400
                  for e in first.values() if "refunded" in e and "requested" in e]
        outcomes = dict(settled)
        ok, failed = outcomes.get("refunded", 0), outcomes.get("refund_failed", 0)
        n_sold_all, n_ret_all = sum(sold.values()), len(items)
        self.metrics.finance_queue.set(waiting or 0)
        self.metrics.pending.set(pending or 0)
        return {
            "days": days,
            "tiles": {
                "return_rate": round(100 * n_ret_all / n_sold_all, 1) if n_sold_all else None,
                "avg_cycle_days": round(sum(cycles) / len(cycles), 1) if cycles else None,
                "refund_success_rate": round(100 * ok / (ok + failed), 1) if ok + failed else None,
                "waiting_for_finance": waiting or 0,
                "refunds_pending": pending or 0,
                "items_returned": n_ret_all,
            },
            "by_category": by_category, "top_reasons": top_reasons, "stages": stages,
            "flagged": sorted(({"customer_id": c, "name": names.get(c, c), "returns_30d": n,
                                "refunded_30d": money(refunded_by_customer.get(c) or Decimal("0"))}
                               for c, n in flagged.items()), key=lambda f: -f["returns_30d"]),
        }

    def audit_trail(self, item_id: str) -> list[dict]:
        if self.engine.store.get_item(item_id) is None:
            raise NotFound(item_id)
        return [{"from": a.from_state.value if a.from_state else None, "to": a.to_state.value,
                 "event": a.event_type.value, "actor": a.actor.value, "at": a.at.isoformat()}
                for a in self.engine.audit_log(item_id)]

    # ------------------------------------------------------------------ system / jobs

    def carrier_pickup(self, return_id: str) -> list[str]:
        moved = []
        for item in self.engine.store.items_in_return(return_id):
            if item.state is S.APPROVED:
                self._apply(self._event(EventType.CARRIER_PICKED_UP, item.item_id, Role.SYSTEM))
                moved.append(item.item_id)
        return moved

    def _items_in_state_before(self, state: S, before: datetime) -> list[str]:
        with self.sessions() as s:
            return list(s.execute(select(ReturnItem.item_id).where(
                ReturnItem.state == state.value, ReturnItem.requested_at < before)).scalars())

    def run_carrier_job(self) -> int:
        """Mock carrier (decision D3): picks up approved parcels after CARRIER_DELAY_SECONDS."""
        n = 0
        for item_id in self._items_in_state_before(S.APPROVED, self.now() - timedelta(seconds=self.carrier_delay_s)):
            try:
                self._apply(self._event(EventType.CARRIER_PICKED_UP, item_id, Role.SYSTEM))
                n += 1
            except ReturnRightError:
                pass
        return n

    def run_expiry_job(self) -> int:
        n = 0
        for item_id in self._items_in_state_before(S.APPROVED, self.now() - timedelta(days=self.expiry_days)):
            try:
                self._apply(self._event(EventType.EXPIRY_DUE, item_id, Role.SYSTEM))
                n += 1
            except ReturnRightError:
                pass
        return n

    def run_reconcile_job(self) -> int:
        settled = self.engine.reconcile()
        with self.sessions() as s:
            self.metrics.pending.set(s.scalar(select(func.count()).select_from(ReturnItem)
                                              .where(ReturnItem.state == S.REFUND_PENDING.value)) or 0)
        return settled


def _friendly(exc: ReturnRightError, state: S) -> str:
    label = STATE_LABELS[state.value]
    name = type(exc).__name__
    if name == "IllegalTransition":
        return f"Not changed: the item is already {label}."
    if name == "DuplicateRefund":
        return "This item already has a refund, so nothing was paid again."
    if name == "MalformedEvent":
        return "Choose a grade (A, B or C) and a decision for this item."
    return f"Not changed ({name})."


def ist_date(value: datetime) -> str:
    return value.astimezone(IST).date().isoformat()
