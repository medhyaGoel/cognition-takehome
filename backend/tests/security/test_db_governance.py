"""Catalog-level governance checks. They read pg_catalog, so a future migration that adds a
table without RLS, grants DELETE, or weakens a runtime role fails CI even if no test touches it."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import settings
from tests.helpers import principal_for

RUNTIME_ROLES = ("app_api", "app_worker")
# Tables without business_unit that app_api may not read (write-only from requests).
NON_BU_TABLES = {"outbox", "alembic_version"}

_TABLES = """
SELECT c.oid, c.relname, c.relrowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname
"""


@pytest.fixture
def catalog(owner_engine):
    with owner_engine.connect() as conn:
        yield conn


def _tables(catalog):
    return catalog.execute(text(_TABLES)).all()


def test_schema_has_tables(catalog):
    names = {t.relname for t in _tables(catalog)}
    assert {"payments", "refunds", "refund_approvals", "audit_log", "outbox"} <= names


@pytest.mark.parametrize("role", RUNTIME_ROLES)
def test_runtime_roles_have_no_delete_truncate_or_ddl_privileges(catalog, role):
    bad = []
    for t in _tables(catalog):
        for priv in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            if catalog.scalar(
                text("SELECT has_table_privilege(:r, :o, :p)"), {**_p(role, t), "p": priv}
            ):
                bad.append(f"{t.relname}:{priv}")
    assert bad == []
    assert not catalog.scalar(
        text("SELECT has_schema_privilege(:r, 'public', 'CREATE')"), {"r": role}
    )
    assert not catalog.scalar(
        text("SELECT has_database_privilege(:r, current_database(), 'CREATE')"), {"r": role}
    )


def _p(role, t):
    return {"r": role, "o": t.oid}


@pytest.mark.parametrize("role", RUNTIME_ROLES)
def test_runtime_roles_cannot_bypass_rls_or_escalate(catalog, role):
    attrs = catalog.execute(
        text(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication"
            " FROM pg_roles WHERE rolname = :r"
        ),
        {"r": role},
    ).one()
    assert not any(attrs), attrs
    # Not a member of (or able to SET ROLE to) any other role, e.g. the schema owner.
    memberships = catalog.scalars(
        text(
            "SELECT b.rolname FROM pg_roles b"
            " WHERE b.rolname <> :r AND pg_has_role(:r, b.oid, 'MEMBER')"
            " AND b.rolname NOT LIKE 'pg\\_%'"
        ),
        {"r": role},
    ).all()
    assert memberships == []
    owned = catalog.scalars(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND pg_get_userbyid(c.relowner) = :r"
        ),
        {"r": role},
    ).all()
    assert owned == [], "runtime roles must not own tables (owners bypass RLS)"


def test_every_business_unit_table_has_rls_and_an_app_api_policy(catalog):
    problems = []
    for t in _tables(catalog):
        has_bu = catalog.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = :o"
                " AND attname = 'business_unit' AND NOT attisdropped)"
            ),
            {"o": t.oid},
        )
        if not has_bu:
            continue
        if not t.relrowsecurity:
            problems.append(f"{t.relname}: RLS not enabled")
        api_policies = catalog.scalar(
            text(
                "SELECT count(*) FROM pg_policy p WHERE p.polrelid = :o"
                " AND (SELECT oid FROM pg_roles WHERE rolname = 'app_api') = ANY (p.polroles)"
            ),
            {"o": t.oid},
        )
        if api_policies == 0:
            problems.append(f"{t.relname}: no app_api policy")
    assert problems == []


def test_app_api_can_only_read_rls_tables(catalog):
    """Any table app_api can SELECT must be RLS-protected (or explicitly exempt)."""
    readable_without_rls = [
        t.relname
        for t in _tables(catalog)
        if not t.relrowsecurity
        and catalog.scalar(
            text("SELECT has_table_privilege('app_api', :o, 'SELECT')"), {"o": t.oid}
        )
    ]
    assert readable_without_rls == []


def test_non_business_unit_tables_are_known(catalog):
    without_bu = {
        t.relname
        for t in _tables(catalog)
        if not catalog.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = :o"
                " AND attname = 'business_unit' AND NOT attisdropped)"
            ),
            {"o": t.oid},
        )
    }
    assert without_bu <= NON_BU_TABLES, "new table without business_unit: add it or justify it"


def test_no_security_definer_functions(catalog):
    """SECURITY DEFINER runs as the owner and would bypass RLS; none are needed today."""
    definers = catalog.scalars(
        text(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'public' AND p.prosecdef"
        )
    ).all()
    assert definers == []


def test_audit_log_is_insert_only_for_runtime_roles(catalog):
    for role in RUNTIME_ROLES:
        assert not catalog.scalar(
            text("SELECT has_any_column_privilege(:r, 'audit_log', 'UPDATE')"), {"r": role}
        )


# --- RLS context isolation on pooled connections ---------------------------------------------


@pytest.fixture
def single_connection_engine():
    """One pooled physical connection, so every checkout reuses it."""
    eng = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    yield eng
    eng.dispose()


def _context(conn):
    return conn.execute(
        text(
            "SELECT pg_backend_pid(), current_setting('app.user', true),"
            " current_setting('app.business_units', true), (SELECT count(*) FROM payments)"
        )
    ).one()


@pytest.mark.parametrize("outcome", ["commit", "rollback", "error"])
def test_rls_context_does_not_leak_to_next_checkout(
    single_connection_engine, make_payment, outcome
):
    make_payment(business_unit="US")
    session = Session(bind=single_connection_engine, info={"principal": principal_for("dave")})
    try:
        with session.begin():
            pid, user, bus, visible = _context(session.connection())
            assert (user, bus, visible) == ("dave@example.com", "EU,US", 1)
            if outcome == "rollback":
                session.rollback()
            elif outcome == "error":
                session.execute(text("SELECT 1/0"))
    except Exception:
        assert outcome == "error"
    finally:
        session.close()

    with single_connection_engine.connect() as conn:
        next_pid, user, bus, visible = _context(conn)
    assert next_pid == pid, "test must reuse the same pooled connection"
    assert (user or None, bus or None, visible) == (None, None, 0)


def test_concurrent_sessions_see_only_their_business_units(make_payment):
    from app.db import session_for

    make_payment(business_unit="US")
    make_payment(business_unit="EU", currency="EUR")
    expected = {"alice": ["US"], "frank": ["EU"], "dave": ["EU", "US"], "erin": ["US"]}
    barrier = threading.Barrier(len(expected))

    def run(persona):
        seen = set()
        for i in range(25):
            with session_for(principal_for(persona)) as s, s.begin():
                if i == 0:
                    barrier.wait()
                seen.add(tuple(s.scalars(text("SELECT business_unit FROM payments ORDER BY 1"))))
        return persona, seen

    with ThreadPoolExecutor(max_workers=len(expected)) as pool:
        results = dict(pool.map(run, expected))
    assert results == {p: {tuple(bus)} for p, bus in expected.items()}
