"""Test chạy trên PostgreSQL thật. Đặt TD_TEST_DATABASE_URL tới một database rỗng dành riêng cho test."""
import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

TEST_URL = os.environ.get("TD_TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/thanhduc_test")
os.environ["TD_DATABASE_URL"] = TEST_URL
os.environ["TD_SECRET_KEY"] = "test-secret-key-at-least-32-bytes-long"

from app.config import get_settings  # noqa: E402
from app.db import get_session, reset_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

TABLES = "audit_log, customer_phones, customer_contacts, customers, products, users"
PASSWORD = "matkhau-test-123"


@pytest.fixture(scope="session")
def engine():
    get_settings.cache_clear()
    reset_engine()
    eng = create_engine(TEST_URL)
    with eng.begin() as conn:
        conn.execute(text("drop schema public cascade; create schema public"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.attributes["database_url"] = TEST_URL
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as conn:
        conn.execute(text("alter table audit_log disable trigger audit_log_readonly"))
        conn.execute(text(f"truncate {TABLES} restart identity cascade"))
        conn.execute(text("alter table audit_log enable trigger audit_log_readonly"))
        conn.execute(text("alter sequence ktd_seq restart with 1"))
    yield


@pytest.fixture
def db(engine):
    gen = get_session()
    session = next(gen)
    yield session
    session.close()


@pytest.fixture
def client(engine):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user(db):
    def _make(username: str, *roles: str) -> User:
        u = User(username=username, full_name=username.title(), password_hash=hash_password(PASSWORD), roles=list(roles))
        db.add(u)
        db.commit()
        return u

    return _make


@pytest.fixture
def login(client, make_user):
    def _login(username: str, *roles: str) -> dict:
        make_user(username, *roles)
        r = client.post("/auth/login", data={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    return _login
