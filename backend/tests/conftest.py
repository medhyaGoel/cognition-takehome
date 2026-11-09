"""Tests run against real Postgres (concurrency guarantees can't be tested on SQLite) and
exercise the real runtime roles, so row-level security and grants are part of every test.

Set TEST_DATABASE_HOST/PORT, or start the compose db: `docker compose up -d db`.
"""

import os
import pathlib
import uuid
from datetime import UTC, datetime

_HOST = os.environ.get("TEST_DATABASE_HOST", "localhost")
_PORT = os.environ.get("TEST_DATABASE_PORT", "5432")
os.environ.update(
    {
        "MIGRATION_DATABASE_URL": f"postgresql+psycopg://app:app@{_HOST}:{_PORT}/app_test",
        "DATABASE_URL": f"postgresql+psycopg://app_api:app_api_test@{_HOST}:{_PORT}/app_test",
        "WORKER_DATABASE_URL": (
            f"postgresql+psycopg://app_worker:app_worker_test@{_HOST}:{_PORT}/app_test"
        ),
        "SOURCE_DATABASE_URL": f"postgresql+psycopg://app:app@{_HOST}:{_PORT}/source_test",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.platform import auth  # noqa: E402
from tests.helpers import TEST_PUBLIC_KEY, auth_headers, principal_for  # noqa: E402, F401

TABLES = ["notifications", "refund_approvals", "refunds", "payments", "audit_log", "outbox"]
_SOURCE_SQL = pathlib.Path(__file__).resolve().parents[2] / "source-db" / "init.sql"


def _recreate_database(url: str) -> None:
    parsed = make_url(url)
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    from app.bootstrap import enable_runtime_logins, migrate

    _recreate_database(settings.migration_database_url)
    _recreate_database(settings.source_database_url)
    source = create_engine(settings.source_database_url)
    with source.begin() as conn:
        conn.exec_driver_sql(_SOURCE_SQL.read_text())
    source.dispose()
    migrate(settings.migration_database_url)
    enable_runtime_logins(
        settings.migration_database_url, [settings.database_url, settings.worker_database_url]
    )
    auth.set_key_resolver(lambda _token: TEST_PUBLIC_KEY)
    yield


@pytest.fixture(scope="session")
def owner_engine():
    """Schema-owner connection: fixtures/setup only. Never use it to test app behaviour."""
    eng = create_engine(settings.migration_database_url)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(owner_engine):
    yield
    with owner_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


@pytest.fixture
def db_as():
    """`db_as("alice")` -> app_api session bound to that persona (RLS applies)."""
    from app.db import session_for

    sessions: list[Session] = []

    def factory(persona: str) -> Session:
        s = session_for(principal_for(persona))
        sessions.append(s)
        return s

    yield factory
    for s in sessions:
        s.close()


@pytest.fixture
def worker_db():
    from app.db import WorkerSessionLocal

    with WorkerSessionLocal() as session:
        yield session


@pytest.fixture
def make_payment(owner_engine):
    """Insert a mirrored payment directly (as the sync job would). Returns its id."""

    def factory(
        amount_cents: int = 10_000, business_unit: str = "US", currency: str = "USD"
    ) -> uuid.UUID:
        pid = uuid.uuid4()
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO payments (id, provider_ref, customer_name, customer_email,"
                    " amount_cents, currency, business_unit, source_system, captured_at)"
                    " VALUES (:id, :ref, 'Test Customer', 'cust@customer.test', :amt, :cur,"
                    " :bu, 'test', :at)"
                ),
                {
                    "id": pid,
                    "ref": f"ch_{pid.hex[:10]}",
                    "amt": amount_cents,
                    "cur": currency,
                    "bu": business_unit,
                    "at": datetime.now(UTC),
                },
            )
        return pid

    return factory
