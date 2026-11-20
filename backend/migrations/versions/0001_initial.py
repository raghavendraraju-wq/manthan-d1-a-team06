"""Initial schema: orders, returns, items, refund ledger, audit log.

Revision ID: 0001
Revises:
"""
from alembic import op

from backend.db.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The first revision creates the schema straight from the models (they are the single source of truth);
    # later revisions are written as ordinary Alembic operations.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
