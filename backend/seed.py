"""Synthetic data: 40 products in 6 categories, 500 customers, 2,000 orders, staff accounts,
plus a return history played through the real engine so the dashboard and queues have data.

Deterministic (fixed random seed) and relative to "now", so windows stay realistic.
Skips itself if the database already has customers.

Run: python -m backend.seed
"""
from __future__ import annotations

import random
import secrets
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from faker import Faker
from sqlalchemy import func, select

from returnright.contracts import (
    IST, Event, EventType, GatewayResult, ItemState as S, ReturnRightError, Role,
)
from returnright.engine import build_engine
from returnright.flags import FlagDetector

from .adapters.sql_store import SqlStore
from .auth import hash_password
from .config import Settings
from .db import make_engine, make_sessionmaker
from .db.models import Customer, Order, OrderLine, Product, Return, StaffUser

PRODUCTS = {
    "apparel": [("Cotton kurta", 1899), ("Linen shirt", 1499), ("Denim jeans", 2299), ("Silk saree", 6999),
                ("Wool blazer", 5499), ("Printed dress", 2499), ("Track pants", 1199), ("Hoodie", 1799),
                ("Formal trousers", 1999), ("Rain jacket", 3299)],
    "footwear": [("Running shoes", 4999), ("Leather loafers", 3999), ("Sandals", 1299), ("Sneakers", 3499),
                 ("Formal shoes", 4499), ("Flip-flops", 499), ("Trail boots", 7999)],
    "electronics": [("Wireless headphones", 12499), ("27-inch monitor", 18990), ("Smartwatch", 24400),
                    ("Bluetooth speaker", 3999), ("Power bank", 1799), ("Mechanical keyboard", 6499),
                    ("Wireless mouse", 1299), ("Tablet", 28990)],
    "home": [("Ceramic dinner set", 3499), ("Cotton bedsheet", 1599), ("Table lamp", 2199), ("Cushion covers (4)", 899),
             ("Non-stick pan", 1899), ("Wall clock", 1299), ("Storage boxes (3)", 999)],
    "innerwear": [("Innerwear pack (3)", 799), ("Sports bra", 999), ("Thermal set", 1499), ("Socks pack (5)", 499)],
    "perishables": [("Organic honey", 450), ("Assam tea 500 g", 380), ("Dry fruit box", 1250), ("Cold-pressed oil", 620)],
}
STAFF = [("arjun@returnright.test", "Arjun", "agent"), ("meera@returnright.test", "Meera", "finance"),
         ("vikram@returnright.test", "Vikram", "manager")]
DEMO_CUSTOMERS = [("C-0001", "Priya S.", "priya.s@example.com"), ("C-0002", "Rahul K.", "rahul.k@example.com"),
                  ("C-0003", "Ananya D.", "ananya.d@example.com")]
REASONS = ["size", "size", "size", "damaged", "not-described", "changed-mind"]


class SeedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class SeedGateway:
    """Seed history only: answers like the mock gateway without a network call. Refs say SEED."""

    def __init__(self) -> None:
        self.results: dict[str, GatewayResult] = {}

    def pay(self, key, amount, item_id):
        return self.results.setdefault(key, GatewayResult("succeeded", f"SEED-{len(self.results) + 1:05d}"))

    def status(self, key):
        return self.results.get(key)


def _ist(day: datetime, hour: int) -> datetime:
    return datetime.combine(day.astimezone(IST).date(), time(hour), IST).astimezone(timezone.utc)


def seed(settings: Settings | None = None, now: datetime | None = None, orders: int = 2000, customers: int = 500,
         history_returns: int = 170) -> bool:
    settings = settings or Settings()
    now = now or datetime.now(timezone.utc)
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as s:
        if s.scalar(select(func.count()).select_from(Customer)):
            return False

    rnd = random.Random(42)
    fake = Faker("en_IN")
    fake.seed_instance(42)

    with sessions() as s, s.begin():
        for email, name, role in STAFF:
            s.add(StaffUser(email=email, name=name, role=role, password_hash=hash_password(settings.demo_staff_password)))
        catalog = []
        n = 0
        for category, items in PRODUCTS.items():
            for name, price in items:
                n += 1
                product = Product(sku=f"SKU-{n:03d}", name=name, category=category, price=Decimal(price))
                s.add(product)
                catalog.append(product)
        assert len(catalog) == 40
        by_name = {p.name: p for p in catalog}

        people = list(DEMO_CUSTOMERS)
        used = {e for _, _, e in people}
        while len(people) < customers:
            first, last = fake.first_name(), fake.last_name()
            email = f"{first}.{last}{rnd.randint(1, 99)}@example.com".lower().replace(" ", "")
            if email in used:
                continue
            used.add(email)
            people.append((f"C-{len(people) + 1:04d}", f"{first} {last[0]}.", email))
        for cid, name, email in people:
            s.add(Customer(id=cid, name=name, email=email))
        s.flush()

        numbers = [f"RR-{10001 + i}" for i in range(orders)]
        if "RR-10482" not in numbers:
            numbers[-1] = "RR-10482"          # small test seeds still get the prototype's demo order
        for order_no in numbers:
            if order_no == "RR-10482":
                delivered = _ist(now - timedelta(days=8), 15)
                cid, _, email = people[0]
                lines = [by_name["Cotton kurta"], by_name["Wireless headphones"], by_name["Innerwear pack (3)"]]
                ordered = delivered - timedelta(days=3)
            else:
                cid, _, email = people[rnd.randrange(3, len(people))]
                ordered = now - timedelta(days=rnd.uniform(1, 90))
                delivered = ordered + timedelta(days=rnd.uniform(2, 6))
                if delivered > now:
                    delivered = None
                lines = rnd.sample(catalog, rnd.choice([1, 1, 1, 2, 2, 3]))
            s.add(Order(order_no=order_no, customer_id=cid, email=email, ordered_at=ordered, delivered_at=delivered))
            for line_no, p in enumerate(lines, start=1):
                s.add(OrderLine(order_no=order_no, line_no=line_no, sku=p.sku, product_name=p.name,
                                category=p.category, price=p.price))

    _history(sessions, rnd, now, history_returns, settings)
    return True


def _history(sessions, rnd: random.Random, now: datetime, count: int, settings: Settings) -> None:
    clock = SeedClock(now)
    engine = build_engine(gateway=SeedGateway(), clock=clock, store=SqlStore(sessions, clock), flags=FlagDetector(clock))

    with sessions() as s:
        delivered = s.execute(select(Order).where(Order.delivered_at.is_not(None), Order.order_no != "RR-10482",
                                                  Order.customer_id.not_in(["C-0001", "C-0002", "C-0003"]))
                              .order_by(Order.order_no)).scalars().all()
        lines = {}
        for line in s.execute(select(OrderLine)).scalars():
            lines.setdefault(line.order_no, []).append(line)

    def make_return(order: Order, chosen: list[OrderLine], method: str, at: datetime) -> list[str]:
        with sessions() as s, s.begin():
            ret = Return(return_id=f"TMP-{secrets.token_hex(6)}", order_no=order.order_no,
                         customer_id=order.customer_id, refund_method=method, created_at=at)
            s.add(ret)
            s.flush()
            ret.return_id = f"RTN-{20000 + ret.id}"
            return_id = ret.return_id
        ids = []
        for line in chosen:
            item_id = f"{return_id}-{line.line_no}"
            clock.at = at
            try:
                engine.apply(Event(EventType.RETURN_REQUESTED, item_id, at, Role.CUSTOMER, {
                    "return_id": return_id, "customer_id": order.customer_id, "order_no": order.order_no,
                    "category": line.category, "price_inr": line.price, "delivered_at": order.delivered_at,
                    "refund_method": method, "reason": rnd.choice(REASONS), "product_name": line.product_name,
                    "order_line_id": line.id, "line_no": line.line_no, "photo_id": None}))
            except ReturnRightError:
                continue
            ids.append(item_id)
        return ids

    def step(item_id: str, type_: EventType, actor: Role, at: datetime, **payload) -> bool:
        if at > now:
            return False
        clock.at = at
        try:
            engine.apply(Event(type_, item_id, at, actor, payload))
            return True
        except ReturnRightError:
            return False

    def progress(item_id: str, start: datetime, *, stop_at: S | None = None, grade: str | None = None,
                 finance: str | None = None) -> None:
        if engine.state(item_id) is not S.APPROVED:
            return
        r = rnd.random()
        if stop_at is None and r < 0.08:
            step(item_id, EventType.CUSTOMER_CANCELLED, Role.CUSTOMER, start + timedelta(hours=rnd.uniform(2, 30)))
            return
        t = start + timedelta(days=rnd.uniform(0.5, 2.5))
        if stop_at is S.APPROVED or not step(item_id, EventType.CARRIER_PICKED_UP, Role.SYSTEM, t):
            return
        if stop_at is S.IN_TRANSIT:
            return
        t += timedelta(days=rnd.uniform(1.5, 4))
        if not step(item_id, EventType.QR_SCANNED, Role.AGENT, t):
            return
        t += timedelta(days=rnd.uniform(0.3, 2))
        grade = grade or rnd.choices("ABC", weights=[60, 25, 15])[0]
        decision = "reject" if (stop_at is None and rnd.random() < 0.07) else "accept"
        if not step(item_id, EventType.INSPECTED, Role.AGENT, t, grade=grade, decision=decision):
            return
        state = engine.state(item_id)
        if state is S.INSPECTED:
            step(item_id, EventType.REFUND_REQUESTED, Role.SYSTEM, t + timedelta(minutes=2),
                 idempotency_key=f"seed:{item_id}")
        elif state is S.PENDING_FINANCE and stop_at is not S.PENDING_FINANCE:
            t += timedelta(days=rnd.uniform(0.2, 1.5))
            choice = finance or rnd.choices(["approve", "reject", "wait"], weights=[75, 12, 13])[0]
            if choice == "approve":
                step(item_id, EventType.FINANCE_APPROVED, Role.FINANCE, t, idempotency_key=f"seed-fin:{item_id}")
            elif choice == "reject":
                step(item_id, EventType.FINANCE_REJECTED, Role.FINANCE, t)

    # Random history over the last ~90 days.
    for order in rnd.sample(delivered, min(count, len(delivered))):
        order_lines = lines[order.order_no]
        chosen = rnd.sample(order_lines, rnd.choice([1] * 4 + [min(2, len(order_lines))]))
        window = 10 if all(line.category == "electronics" for line in chosen) else 30
        latest = min(now, order.delivered_at + timedelta(days=window))
        if latest <= order.delivered_at:
            continue
        at = order.delivered_at + (latest - order.delivered_at) * rnd.uniform(0.05, 0.95)
        method = rnd.choices(["original_payment", "store_credit"], weights=[80, 20])[0]
        for item_id in make_return(order, chosen, method, at):
            progress(item_id, at)

    # Demo cases that the prototype walks through.
    _demo_returns(sessions, rnd, now, make_return, progress)


def _demo_returns(sessions, rnd, now, make_return, progress) -> None:
    """Finance queue (monitor via store credit, flagged customer's smartwatch), a parcel in transit."""
    with sessions() as s:
        products = {p.name: p for p in s.execute(select(Product)).scalars()}

    def order_for(cid: str, email: str, names: list[str], days_ago: float) -> Order:
        delivered = now - timedelta(days=days_ago)
        with sessions() as s, s.begin():
            n = s.scalar(select(func.count()).select_from(Order).where(Order.order_no >= "RR-19001"))
            order = Order(order_no=f"RR-{19001 + n}", customer_id=cid, email=email,
                          ordered_at=delivered - timedelta(days=3), delivered_at=delivered)
            s.add(order)
            s.flush()
            for line_no, name in enumerate(names, start=1):
                p = products[name]
                s.add(OrderLine(order_no=order.order_no, line_no=line_no, sku=p.sku, product_name=p.name,
                                category=p.category, price=p.price))
        with sessions() as s:
            order = s.get(Order, order.order_no)
            return order, s.execute(select(OrderLine).where(OrderLine.order_no == order.order_no)
                                    .order_by(OrderLine.line_no)).scalars().all()

    # Rahul: 27-inch monitor, grade B, store credit -> waits for finance (INR 18,990).
    order, lines = order_for("C-0002", "rahul.k@example.com", ["27-inch monitor"], 9)
    at = order.delivered_at + timedelta(days=1)
    for item_id in make_return(order, lines, "store_credit", at):
        progress(item_id, at, grade="B", stop_at=S.PENDING_FINANCE)

    # Ananya: flagged (4 returns in 30 days); smartwatch graded C = INR 12,200 waits for finance.
    for k, names in enumerate([["Hoodie"], ["Sneakers"], ["Table lamp"]]):
        order, lines = order_for("C-0003", "ananya.d@example.com", names, 25 - 5 * k)
        at = order.delivered_at + timedelta(days=1)
        for item_id in make_return(order, lines, "original_payment", at):
            progress(item_id, at, grade="A")
    order, lines = order_for("C-0003", "ananya.d@example.com", ["Smartwatch"], 9)
    at = order.delivered_at + timedelta(days=1)
    for item_id in make_return(order, lines, "original_payment", at):
        progress(item_id, at, grade="C", stop_at=S.PENDING_FINANCE)

    # A parcel on its way to the warehouse, ready for the agent to scan.
    order, lines = order_for("C-0002", "rahul.k@example.com", ["Linen shirt", "Running shoes"], 6)
    at = order.delivered_at + timedelta(days=1)
    for item_id in make_return(order, lines, "original_payment", at):
        progress(item_id, at, stop_at=S.IN_TRANSIT)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    print("seeded" if seed() else "database already has data; seed skipped")
