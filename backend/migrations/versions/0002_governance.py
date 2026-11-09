"""Governance: least-privilege runtime roles, column-level grants, row-level security.

Runtime roles (created here if missing; in production the platform team provisions them):
  app_api     serves HTTP requests. NOBYPASSRLS. Sees only rows in the caller's business units.
              INSERT/UPDATE on specific columns only; no DELETE anywhere; audit is insert-only.
  app_worker  background jobs (outbox dispatch, payouts, reconciliation). Explicit
              all-business-unit policies; still no DELETE and no audit UPDATE.
The schema owner (migrations, connector sync) is never used to serve traffic.

RLS context comes from transaction-local settings set by app.db for each request:
  app.user            the authenticated subject (email)
  app.business_units  comma-separated business units from the IdP token
Missing context => NULL => no rows (fail-closed).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

RLS_TABLES = ["payments", "refunds", "refund_approvals", "audit_log", "notifications"]

UPGRADE = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
        CREATE ROLE app_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_worker') THEN
        CREATE ROLE app_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_api, app_worker;

CREATE FUNCTION app_user() RETURNS text LANGUAGE sql STABLE AS
    $$ SELECT nullif(current_setting('app.user', true), '') $$;
CREATE FUNCTION app_business_units() RETURNS text[] LANGUAGE sql STABLE AS
    $$ SELECT string_to_array(nullif(current_setting('app.business_units', true), ''), ',') $$;

-- payments: reference data mirrored from the system of record; app may only move ledger columns.
GRANT SELECT ON payments TO app_api, app_worker;
GRANT UPDATE (reserved_cents, refunded_cents) ON payments TO app_api, app_worker;

GRANT SELECT, INSERT ON refunds TO app_api;
GRANT SELECT ON refunds TO app_worker;
GRANT UPDATE (status, required_approvals, provider_refund_id, failure_reason, version,
              updated_at, escalated_at) ON refunds TO app_api, app_worker;

GRANT SELECT, INSERT ON refund_approvals TO app_api;
GRANT SELECT ON refund_approvals TO app_worker;

GRANT SELECT, INSERT ON audit_log TO app_api, app_worker;

GRANT INSERT ON outbox TO app_api;
GRANT SELECT, INSERT ON outbox TO app_worker;
GRANT UPDATE (dispatched_at, attempts, last_error, available_at) ON outbox TO app_worker;

GRANT SELECT ON notifications TO app_api;
GRANT SELECT, INSERT ON notifications TO app_worker;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_api, app_worker;

ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE refund_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY payments_api_bu ON payments TO app_api
    USING (business_unit = ANY (app_business_units()))
    WITH CHECK (business_unit = ANY (app_business_units()));

CREATE POLICY refunds_api_select ON refunds FOR SELECT TO app_api
    USING (business_unit = ANY (app_business_units()));
CREATE POLICY refunds_api_insert ON refunds FOR INSERT TO app_api
    WITH CHECK (business_unit = ANY (app_business_units()) AND requested_by = app_user());
CREATE POLICY refunds_api_update ON refunds FOR UPDATE TO app_api
    USING (business_unit = ANY (app_business_units()))
    WITH CHECK (business_unit = ANY (app_business_units()));

CREATE POLICY approvals_api_select ON refund_approvals FOR SELECT TO app_api
    USING (business_unit = ANY (app_business_units()));
CREATE POLICY approvals_api_insert ON refund_approvals FOR INSERT TO app_api
    WITH CHECK (business_unit = ANY (app_business_units()) AND approver = app_user());

CREATE POLICY audit_api_select ON audit_log FOR SELECT TO app_api
    USING (business_unit IS NULL OR business_unit = ANY (app_business_units()));
CREATE POLICY audit_api_insert ON audit_log FOR INSERT TO app_api
    WITH CHECK (actor = app_user()
                AND (business_unit IS NULL OR business_unit = ANY (app_business_units())));

CREATE POLICY notifications_api_select ON notifications FOR SELECT TO app_api
    USING (business_unit IS NULL OR business_unit = ANY (app_business_units()));
"""

WORKER_POLICIES = "\n".join(
    f"CREATE POLICY {t}_worker_all ON {t} TO app_worker USING (true) WITH CHECK (true);"
    for t in RLS_TABLES
)

DOWNGRADE = (
    "\n".join(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;" for t in RLS_TABLES)
    + "\n"
    + "\n".join(
        f"DROP POLICY IF EXISTS {p} ON {t};"
        for t, p in [
            ("payments", "payments_api_bu"),
            ("refunds", "refunds_api_select"),
            ("refunds", "refunds_api_insert"),
            ("refunds", "refunds_api_update"),
            ("refund_approvals", "approvals_api_select"),
            ("refund_approvals", "approvals_api_insert"),
            ("audit_log", "audit_api_select"),
            ("audit_log", "audit_api_insert"),
            ("notifications", "notifications_api_select"),
        ]
        + [(t, f"{t}_worker_all") for t in RLS_TABLES]
    )
    + """
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_api, app_worker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_api, app_worker;
REVOKE USAGE ON SCHEMA public FROM app_api, app_worker;
DROP FUNCTION app_business_units();
DROP FUNCTION app_user();
"""
)


def upgrade() -> None:
    op.execute(UPGRADE)
    op.execute(WORKER_POLICIES)


def downgrade() -> None:
    op.execute(DOWNGRADE)
