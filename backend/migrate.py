"""Apply database migrations: python -m backend.migrate"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade(database_url: str | None = None) -> None:
    cfg = Config(str(Path(__file__).with_name("alembic.ini")))
    if database_url:
        cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    upgrade()
