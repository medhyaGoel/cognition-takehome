"""Database-level governance: least-privilege roles, row-level security (Dataverse
business-units equivalent), append-only audit, maker-checker. These hold even if app code is
wrong, because they are enforced by Postgres for the runtime roles."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError


def _insert_refund(db, payment_id, requested_by, bu="US", amount=100):
    rid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO refunds (id, payment_id, business_unit, amount_cents, currency, reason,"
            " status, requested_by, idempotency_key, request_hash)"
            " VALUES (:id, :p, :bu, :amt, 'USD', 'r', 'pending_approval', :by, :k, 'h')"
        ),
        {"id": rid, "p": payment_id, "bu": bu, "amt": amount, "by": requested_by, "k": rid.hex},
    )
    return rid


def test_rls_scopes_rows_to_business_units(db_as, make_payment):
    make_payment(business_unit="US")
    make_payment(business_unit="EU", currency="EUR")
    count = "SELECT business_unit FROM payments ORDER BY 1"
    assert db_as("alice").scalars(text(count)).all() == ["US"]
    assert db_as("frank").scalars(text(count)).all() == ["EU"]
    assert db_as("dave").scalars(text(count)).all() == ["EU", "US"]


def test_rls_fails_closed_without_identity(make_payment):
    from app.db import SessionLocal

    make_payment()
    with SessionLocal() as anonymous:
        assert anonymous.scalar(text("SELECT count(*) FROM payments")) == 0


def test_cannot_write_into_other_business_unit(db_as, make_payment):
    eu_payment = make_payment(business_unit="EU", currency="EUR")
    alice = db_as("alice")
    with pytest.raises(ProgrammingError, match="row-level security"), alice.begin():
        _insert_refund(alice, eu_payment, "alice@example.com", bu="EU")
    with alice.begin():
        updated = alice.execute(
            text("UPDATE payments SET reserved_cents = 1 WHERE id = :p"), {"p": eu_payment}
        ).rowcount
    assert updated == 0


def test_cannot_create_refund_as_someone_else(db_as, make_payment):
    payment = make_payment()
    alice = db_as("alice")
    with pytest.raises(ProgrammingError, match="row-level security"), alice.begin():
        _insert_refund(alice, payment, "bob@example.com")


def test_maker_checker_enforced_by_database(db_as, make_payment):
    payment = make_payment()
    dave = db_as("dave")
    with dave.begin():
        rid = _insert_refund(dave, payment, "dave@example.com")
    approve = text(
        "INSERT INTO refund_approvals (refund_id, business_unit, approver, decision)"
        " VALUES (:r, 'US', :a, 'approved')"
    )
    with pytest.raises(IntegrityError, match="own refund"), dave.begin():
        dave.execute(approve, {"r": rid, "a": "dave@example.com"})
    bob = db_as("bob")
    with pytest.raises(ProgrammingError, match="row-level security"), bob.begin():
        bob.execute(approve, {"r": rid, "a": "carol@example.com"})
    with bob.begin():
        bob.execute(approve, {"r": rid, "a": "bob@example.com"})


def test_audit_log_is_append_only_and_unforgeable(db_as):
    insert = text(
        "INSERT INTO audit_log (actor, action, entity_type, entity_id, business_unit)"
        " VALUES (:actor, 't', 't', '1', 'US')"
    )
    bob = db_as("bob")
    with bob.begin():
        bob.execute(insert, {"actor": "bob@example.com"})
    with pytest.raises(ProgrammingError, match="row-level security"), bob.begin():
        bob.execute(insert, {"actor": "alice@example.com"})
    for stmt in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(ProgrammingError, match="permission denied"), bob.begin():
            bob.execute(text(stmt))


def test_audit_log_append_only_even_for_owner(owner_engine):
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_log (actor, action, entity_type, entity_id)"
                " VALUES ('t', 't', 't', '1')"
            )
        )
    for stmt in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="append-only"), owner_engine.begin() as conn:
            conn.execute(text(stmt))


def test_runtime_role_cannot_delete_or_touch_immutable_columns(db_as, make_payment):
    payment = make_payment()
    alice = db_as("alice")
    for stmt in (
        "DELETE FROM payments",
        "UPDATE payments SET amount_cents = 1",
        "TRUNCATE refunds",
        "CREATE TABLE evil (id int)",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"), alice.begin():
            alice.execute(text(stmt), {"p": payment})


def test_refund_bounds_enforced_by_db(db_as, make_payment):
    payment = make_payment(amount_cents=100)
    alice = db_as("alice")
    with pytest.raises(IntegrityError), alice.begin():
        alice.execute(
            text("UPDATE payments SET reserved_cents = 101 WHERE id = :p"), {"p": payment}
        )


def test_worker_role_sees_all_business_units(worker_db, make_payment):
    make_payment(business_unit="US")
    make_payment(business_unit="EU", currency="EUR")
    assert worker_db.scalar(text("SELECT count(*) FROM payments")) == 2
