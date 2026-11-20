"""PostgreSQL schema (also runs on SQLite for quick local tests).

Ledger design (ADR-001): one row per refunded item. `return_item_id` and
`idempotency_key` are both UNIQUE, so the database itself refuses a second
refund for an item or a reused key, even if application code were wrong.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATES = ("requested", "approved", "in_transit", "received", "inspected", "pending_finance",
          "refund_pending", "refund_failed", "refunded", "rejected", "cancelled", "expired")
CATEGORIES = ("apparel", "footwear", "electronics", "home", "innerwear", "perishables")


class UTCDateTime(TypeDecorator):
    """Stores UTC, always returns timezone-aware UTC (SQLite drops the offset otherwise)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


Money = Numeric(12, 2, asdecimal=True)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)


class Product(Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20))
    price: Mapped[Decimal] = mapped_column(Money)
    __table_args__ = (CheckConstraint(_in("category", CATEGORIES), name="ck_products_category"),
                      CheckConstraint("price > 0", name="ck_products_price"))


class Order(Base):
    __tablename__ = "orders"
    order_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    email: Mapped[str] = mapped_column(String(200))
    ordered_at: Mapped[datetime] = mapped_column(UTCDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(ForeignKey("orders.order_no"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    product_name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20))
    price: Mapped[Decimal] = mapped_column(Money)
    __table_args__ = (UniqueConstraint("order_no", "line_no", name="uq_order_lines_order_line"),)


class Return(Base):
    __tablename__ = "returns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_id: Mapped[str] = mapped_column(String(20), unique=True)
    order_no: Mapped[str] = mapped_column(ForeignKey("orders.order_no"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    refund_method: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ReturnItem(Base):
    __tablename__ = "return_items"
    item_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    return_id: Mapped[str] = mapped_column(ForeignKey("returns.return_id"), index=True)
    order_line_id: Mapped[int | None] = mapped_column(ForeignKey("order_lines.id"), index=True)
    customer_id: Mapped[str] = mapped_column(String(20), index=True)
    order_no: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(20))
    price: Mapped[Decimal] = mapped_column(Money)
    delivered_at: Mapped[datetime] = mapped_column(UTCDateTime)
    refund_method: Mapped[str] = mapped_column(String(20))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    arrived_at: Mapped[datetime] = mapped_column(UTCDateTime)      # processing time, for the flag rule
    state: Mapped[str] = mapped_column(String(20), index=True)
    grade: Mapped[str | None] = mapped_column(String(1))
    amount: Mapped[Decimal | None] = mapped_column(Money)
    rejection_rule: Mapped[str | None] = mapped_column(String(40))
    refund_key: Mapped[str | None] = mapped_column(String(200))
    refund_keys: Mapped[list] = mapped_column(JSON, default=list)
    gateway_ref: Mapped[str | None] = mapped_column(String(60))
    reason: Mapped[str | None] = mapped_column(String(40))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (CheckConstraint(_in("state", STATES), name="ck_return_items_state"),
                      CheckConstraint("grade IS NULL OR grade IN ('A', 'B', 'C')", name="ck_return_items_grade"))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("return_items.item_id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime)


class LedgerEntry(Base):
    __tablename__ = "refund_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_item_id: Mapped[str] = mapped_column(ForeignKey("return_items.item_id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    method: Mapped[str] = mapped_column(String(20))
    gateway_ref: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (CheckConstraint("amount > 0", name="ck_ledger_amount"),
                      CheckConstraint("method = 'store_credit' OR gateway_ref IS NOT NULL", name="ck_ledger_ref"))


class RejectedEvent(Base):
    __tablename__ = "rejected_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    item_id: Mapped[str | None] = mapped_column(String(100))
    event_type: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text)


class StaffUser(Base):
    __tablename__ = "staff_users"
    email: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(200))
    __table_args__ = (CheckConstraint("role IN ('agent', 'finance', 'manager')", name="ck_staff_role"),)


class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_no: Mapped[str] = mapped_column(String(20), index=True)
    filename: Mapped[str] = mapped_column(String(80))
    content_type: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


Index("ix_audit_log_at", AuditLog.at)
Index("ix_return_items_requested_at", ReturnItem.requested_at)
