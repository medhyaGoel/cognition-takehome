"""Initial schema: payments, refunds, approvals, audit log, outbox, notifications.

Money is integer minor units (cents). Invariants are enforced by the database, not only
by application code:
  0 <= refunded_cents <= reserved_cents <= amount_cents
reserved_cents = sum of refunds that are not rejected/failed (i.e. still may be paid out).

`payments` is a local mirror of the client's system of record (synced by
app.platform.connectors), holding our refund ledger columns (reserved/refunded).
`business_unit` is carried on every row (enforced by composite FKs) for row-level security.

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
    business_unit   text NOT NULL CHECK (business_unit IN ('US', 'EU')),
    source_system   text NOT NULL,
    captured_at     timestamptz NOT NULL,
    synced_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT payments_id_bu UNIQUE (id, business_unit),
    CONSTRAINT payments_refund_bounds CHECK (
        0 <= refunded_cents AND refunded_cents <= reserved_cents AND reserved_cents <= amount_cents
    )
);

CREATE TABLE refunds (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id          uuid NOT NULL,
    business_unit       text NOT NULL,
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
    escalated_at        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT refunds_idempotency UNIQUE (requested_by, idempotency_key),
    CONSTRAINT refunds_id_bu UNIQUE (id, business_unit),
    CONSTRAINT refunds_payment_fk FOREIGN KEY (payment_id, business_unit)
        REFERENCES payments (id, business_unit)
);
CREATE INDEX refunds_status_created_idx ON refunds (status, created_at);
CREATE INDEX refunds_payment_idx ON refunds (payment_id);

CREATE TABLE refund_approvals (
    id          bigserial PRIMARY KEY,
    refund_id   uuid NOT NULL,
    business_unit text NOT NULL,
    approver    text NOT NULL,
    decision    text NOT NULL CHECK (decision IN ('approved', 'rejected')),
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT refund_approvals_one_per_approver UNIQUE (refund_id, approver),
    CONSTRAINT refund_approvals_refund_fk FOREIGN KEY (refund_id, business_unit)
        REFERENCES refunds (id, business_unit)
);

-- Maker-checker in the database: nobody approves (or rejects) their own refund.
CREATE FUNCTION refund_approvals_no_self_approval() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM refunds WHERE id = NEW.refund_id AND requested_by = NEW.approver) THEN
        RAISE EXCEPTION 'requester cannot decide their own refund' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER refund_approvals_maker_checker
    BEFORE INSERT ON refund_approvals
    FOR EACH ROW EXECUTE FUNCTION refund_approvals_no_self_approval();

CREATE TABLE audit_log (
    id           bigserial PRIMARY KEY,
    occurred_at  timestamptz NOT NULL DEFAULT now(),
    actor        text NOT NULL,
    action       text NOT NULL,
    entity_type  text NOT NULL,
    entity_id    text NOT NULL,
    business_unit text,
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

-- In-app notifications (written by the worker when dispatching outbox messages).
CREATE TABLE notifications (
    id              bigserial PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    business_unit   text,
    recipient_role  text,
    recipient       text,
    topic           text NOT NULL,
    title           text NOT NULL,
    body            text NOT NULL,
    entity_type     text,
    entity_id       text,
    outbox_id       bigint UNIQUE REFERENCES outbox(id)
);
CREATE INDEX notifications_created_idx ON notifications (created_at DESC);
"""

DOWNGRADE = """
DROP TABLE notifications;
DROP TABLE outbox;
DROP TRIGGER audit_log_no_update_delete ON audit_log;
DROP FUNCTION audit_log_append_only();
DROP TABLE audit_log;
DROP TABLE refund_approvals;
DROP FUNCTION refund_approvals_no_self_approval();
DROP TABLE refunds;
DROP TABLE payments;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
