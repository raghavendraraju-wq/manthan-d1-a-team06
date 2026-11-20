"""API test fixtures: a fresh, migrated and seeded database per test.

Uses TEST_DATABASE_URL (PostgreSQL in CI and Compose) or falls back to SQLite.
The payment gateway is the in-process FakeGateway so tests can break it on purpose.
"""
from __future__ import annotations

import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.adapters.mailer import Mailer
from backend.config import Settings
from backend.main import create_app
from backend.migrate import upgrade
from backend.seed import seed
from tests.fakes import FakeGateway


def _reset(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        if url.startswith("postgresql"):
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
def db_url(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'api.db'}"
    _reset(url)
    upgrade(url)
    return url


@pytest.fixture
def settings(db_url, tmp_path):
    base = Settings()
    s = replace(base, database_url=db_url, jobs_enabled=False, demo_mode=True, upload_dir=str(tmp_path / "uploads"),
                jwt_secret="test-secret-at-least-32-bytes-long!!", system_token="test-system", demo_staff_password="demo-pass")
    seed(s, orders=500, customers=40, history_returns=15)
    return s


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def mailer():
    return Mailer()


@pytest.fixture
def app(settings, gateway, mailer):
    return create_app(settings, gateway=gateway, mailer=mailer)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def staff(client, email: str) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": "demo-pass"})
    assert r.status_code == 200, r.text
    return auth(r.json()["token"])


@pytest.fixture
def agent(client):
    return staff(client, "arjun@returnright.test")


@pytest.fixture
def finance(client):
    return staff(client, "meera@returnright.test")


@pytest.fixture
def manager(client):
    return staff(client, "vikram@returnright.test")


def customer_login(client, mailer, order_no="RR-10482", email="priya.s@example.com") -> dict:
    assert client.post("/api/auth/otp/request", json={"order_no": order_no, "email": email}).status_code == 202
    code = client.get("/api/demo/last-code", params={"email": email}).json()["code"]
    r = client.post("/api/auth/otp/verify", json={"order_no": order_no, "email": email, "code": code})
    assert r.status_code == 200, r.text
    return auth(r.json()["token"])


@pytest.fixture
def customer(client, mailer):
    return customer_login(client, mailer)
