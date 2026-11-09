"""Initial schema: payments, refunds, approvals, audit log, outbox.

Money is integer minor units (cents). Invariants are enforced by the database, not only
by application code:
  0 <= refunded_cents <= reserved_cents <= amount_cents
reserved_cents = sum of refunds that are not rejected/failed (i.e. still may be paid out).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE payments (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_ref    text NOT NULL UNIQUE,
    customer_name   text NOT NULL,
    customer_email  text NOT NULL,
    amount_cents    bigint NOT NULL CHECK (amount_cents > 0),
    currency        char(3) NOT NULL,
    reserved_cents  bigint NOT NULL DEFAULT 0,
    refunded_cents  bigint NOT NULL DEFAULT 0,
    captured_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT payments_refund_bounds CHECK (
        0 <= refunded_cents AND refunded_cents <= reserved_cents AND reserved_cents <= amount_cents
    )
);

CREATE TABLE refunds (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id          uuid NOT NULL REFERENCES payments(id),
    amount_cents        bigint NOT NULL CHECK (amount_cents > 0),
    currency            char(3) NOT NULL,
    reason              text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 1000),
    status              text NOT NULL CHECK (status IN (
                            'pending_approval', 'approved', 'processing',
                            'succeeded', 'failed', 'rejected')),
    requested_by        text NOT NULL,
    required_approvals  integer NOT NULL DEFAULT 1 CHECK (required_approvals BETWEEN 0 AND 5),
    idempotency_key     text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
    request_hash        text NOT NULL,
    provider_refund_id  text,
    failure_reason      text,
    version             integer NOT NULL DEFAULT 1,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT refunds_idempotency UNIQUE (requested_by, idempotency_key)
);
CREATE INDEX refunds_status_created_idx ON refunds (status, created_at);
CREATE INDEX refunds_payment_idx ON refunds (payment_id);

CREATE TABLE refund_approvals (
    id          bigserial PRIMARY KEY,
    refund_id   uuid NOT NULL REFERENCES refunds(id),
    approver    text NOT NULL,
    decision    text NOT NULL CHECK (decision IN ('approved', 'rejected')),
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT refund_approvals_one_per_approver UNIQUE (refund_id, approver)
);

CREATE TABLE audit_log (
    id           bigserial PRIMARY KEY,
    occurred_at  timestamptz NOT NULL DEFAULT now(),
    actor        text NOT NULL,
    action       text NOT NULL,
    entity_type  text NOT NULL,
    entity_id    text NOT NULL,
    before       jsonb,
    after        jsonb,
    request_id   text
);
CREATE INDEX audit_log_entity_idx ON audit_log (entity_type, entity_id, id);

CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END
$$;
CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

CREATE TABLE outbox (
    id             bigserial PRIMARY KEY,
    topic          text NOT NULL,
    payload        jsonb NOT NULL,
    dedupe_key     text UNIQUE,
    created_at     timestamptz NOT NULL DEFAULT now(),
    available_at   timestamptz NOT NULL DEFAULT now(),
    dispatched_at  timestamptz,
    attempts       integer NOT NULL DEFAULT 0,
    last_error     text
);
CREATE INDEX outbox_pending_idx ON outbox (available_at) WHERE dispatched_at IS NULL;
"""

DOWNGRADE = """
DROP TABLE outbox;
DROP TRIGGER audit_log_no_update_delete ON audit_log;
DROP FUNCTION audit_log_append_only();
DROP TABLE audit_log;
DROP TABLE refund_approvals;
DROP TABLE refunds;
DROP TABLE payments;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
