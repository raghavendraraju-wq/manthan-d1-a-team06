"""Alembic environment: the database URL comes from DATABASE_URL (backend.config)."""
from __future__ import annotations

from alembic import context

from backend.config import Settings
from backend.db import make_engine
from backend.db.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = context.config.attributes.get("database_url") or Settings().database_url
    engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
