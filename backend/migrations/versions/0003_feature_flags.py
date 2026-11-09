"""Feature flags: per business unit x environment state, prod changes via maker-checker.

Tables (all RLS-scoped by business unit, except the global flag catalogue):
  feature_flags         key + description (reference data, read-only to runtime roles)
  flag_states           enabled/version per (flag, business_unit, environment)
  flag_change_requests  every toggle; dev/staging are 'applied' at once, prod starts 'pending'
                        and needs a *different* user to approve (trigger below).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

FLAGS = [
    ("instant_payouts", "Pay merchants out instantly instead of T+1"),
    ("new_kyc_flow", "Route new signups through the redesigned KYC flow"),
    ("refund_auto_approve_v2", "Use the v2 rules for auto-approving small refunds"),
    ("card_3ds2_challenge", "Force 3DS2 challenge on high-risk card payments"),
    ("ledger_shadow_writes", "Dual-write ledger entries to the new ledger service"),
    ("dispute_ai_triage", "Suggest dispute outcomes with the triage model"),
]

UPGRADE = """
CREATE TABLE feature_flags (
    key          text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,62}$'),
    description  text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE flag_states (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_key       text NOT NULL REFERENCES feature_flags(key),
    business_unit  text NOT NULL CHECK (business_unit IN ('US', 'EU')),
    environment    text NOT NULL CHECK (environment IN ('dev', 'staging', 'prod')),
    enabled        boolean NOT NULL DEFAULT false,
    version        integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     text,
    CONSTRAINT flag_states_unique UNIQUE (flag_key, business_unit, environment)
);

CREATE TABLE flag_change_requests (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_state_id     uuid NOT NULL REFERENCES flag_states(id),
    flag_key          text NOT NULL,
    business_unit     text NOT NULL CHECK (business_unit IN ('US', 'EU')),
    environment       text NOT NULL CHECK (environment IN ('dev', 'staging', 'prod')),
    desired_enabled   boolean NOT NULL,
    expected_version  integer NOT NULL,
    status            text NOT NULL CHECK (status IN ('applied', 'pending', 'approved', 'rejected')),
    requested_by      text NOT NULL,
    idempotency_key   text NOT NULL,
    request_hash      text NOT NULL,
    decided_by        text,
    decided_at        timestamptz,
    decision_reason   text,
    version           integer NOT NULL DEFAULT 1,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT flag_change_requests_idempotency UNIQUE (requested_by, idempotency_key),
    CONSTRAINT flag_change_requests_prod_only_pending
        CHECK (status = 'applied' OR environment = 'prod'),
    CONSTRAINT flag_change_requests_decision_complete
        CHECK ((status IN ('approved', 'rejected')) = (decided_by IS NOT NULL))
);
CREATE INDEX flag_change_requests_pending ON flag_change_requests (status, created_at);

-- Maker-checker in the database: nobody decides their own prod change request.
CREATE FUNCTION flag_change_requests_maker_checker() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.decided_by IS NOT NULL AND NEW.decided_by = NEW.requested_by THEN
        RAISE EXCEPTION 'maker-checker: % cannot decide their own change request', NEW.decided_by
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'change request % is already %', OLD.id, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER flag_change_requests_maker_checker
    BEFORE INSERT OR UPDATE ON flag_change_requests
    FOR EACH ROW EXECUTE FUNCTION flag_change_requests_maker_checker();

GRANT SELECT ON feature_flags TO app_api, app_worker;
GRANT SELECT ON flag_states TO app_api, app_worker;
GRANT UPDATE (enabled, version, updated_at, updated_by) ON flag_states TO app_api;
GRANT SELECT, INSERT ON flag_change_requests TO app_api;
GRANT SELECT ON flag_change_requests TO app_worker;
GRANT UPDATE (status, decided_by, decided_at, decision_reason, version)
    ON flag_change_requests TO app_api;

ALTER TABLE feature_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE flag_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE flag_change_requests ENABLE ROW LEVEL SECURITY;

-- The catalogue holds no business-unit data; any authenticated request may read it.
CREATE POLICY feature_flags_api_select ON feature_flags FOR SELECT TO app_api
    USING (app_user() IS NOT NULL);
CREATE POLICY flag_states_api_select ON flag_states FOR SELECT TO app_api
    USING (business_unit = ANY (app_business_units()));
CREATE POLICY flag_states_api_update ON flag_states FOR UPDATE TO app_api
    USING (business_unit = ANY (app_business_units()))
    WITH CHECK (business_unit = ANY (app_business_units()) AND updated_by = app_user());
CREATE POLICY flag_change_requests_api_select ON flag_change_requests FOR SELECT TO app_api
    USING (business_unit = ANY (app_business_units()));
CREATE POLICY flag_change_requests_api_insert ON flag_change_requests FOR INSERT TO app_api
    WITH CHECK (business_unit = ANY (app_business_units()) AND requested_by = app_user());
CREATE POLICY flag_change_requests_api_update ON flag_change_requests FOR UPDATE TO app_api
    USING (business_unit = ANY (app_business_units()))
    WITH CHECK (business_unit = ANY (app_business_units()) AND decided_by = app_user());

CREATE POLICY feature_flags_worker_read ON feature_flags FOR SELECT TO app_worker USING (true);
CREATE POLICY flag_states_worker_read ON flag_states FOR SELECT TO app_worker USING (true);
CREATE POLICY flag_change_requests_worker_read ON flag_change_requests FOR SELECT
    TO app_worker USING (true);
"""

DOWNGRADE = """
DROP TABLE flag_change_requests;
DROP FUNCTION flag_change_requests_maker_checker();
DROP TABLE flag_states;
DROP TABLE feature_flags;
"""


SEED_STATES = """
INSERT INTO flag_states (flag_key, business_unit, environment, enabled)
SELECT f.key, bu, env, env = 'dev'
FROM feature_flags f, unnest(ARRAY['US', 'EU']) bu, unnest(ARRAY['dev', 'staging', 'prod']) env;
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    insert = sa.text("INSERT INTO feature_flags (key, description) VALUES (:key, :description)")
    for key, description in FLAGS:
        op.get_bind().execute(insert, {"key": key, "description": description})
    op.execute(SEED_STATES)


def downgrade() -> None:
    op.execute(DOWNGRADE)
