# API contract (v1)

Base path `/api`. JSON everywhere. Auth: `Authorization: Bearer <token>` (get one from
`POST /api/auth/dev-login {"email": ...}` in dev). Money is **integer cents** + ISO currency.
Timestamps are ISO-8601 UTC. IDs are UUID strings.

Errors (all non-2xx from domain logic):
```json
{"error": {"code": "conflict", "message": "human readable"}}
```
| status | code | when |
|---|---|---|
| 401 | — (FastAPI `detail`) | missing/invalid token |
| 403 | `forbidden` or FastAPI `detail` | role missing, or self-approval |
| 404 | `not_found` | unknown id |
| 409 | `conflict` / `version_conflict` / `invalid_transition` / `idempotency_mismatch` / `insufficient_refundable_amount` | concurrency or state violations — client refetches |
| 422 | `validation_failed` or FastAPI validation | bad input |

## Roles
`viewer`, `refund_agent`, `refund_approver`, `finance_admin` (see `backend/app/platform/auth.py`).
Demo users: alice=refund_agent, bob & carol=refund_approver, dave=finance_admin+refund_approver,
erin=viewer.

## Platform (exists)
| method | path | roles | notes |
|---|---|---|---|
| GET | `/healthz` | public | |
| POST | `/api/auth/dev-login` | public (dev only) | `{email}` → `{access_token, token_type}` |
| GET | `/api/auth/dev-users` | public (dev only) | list of demo users for the login picker |
| GET | `/api/me` | any authenticated | `{subject, display_name, roles[]}` |

## Refunds
`ALL_READ = viewer, refund_agent, refund_approver, finance_admin`

| method | path | roles | notes |
|---|---|---|---|
| GET | `/api/payments?q=` | ALL_READ | search by provider_ref / customer name; ≤50 rows |
| GET | `/api/payments/{id}` | ALL_READ | Payment + `refundable_cents` |
| GET | `/api/refunds?status=&limit=&offset=` | ALL_READ | newest first, limit ≤100 |
| GET | `/api/refunds/{id}` | ALL_READ | RefundDetail (refund + approvals + payment summary) |
| POST | `/api/refunds` | refund_agent, finance_admin | header `Idempotency-Key` (8–128 chars) **required**; body `{payment_id, amount_cents, reason}` → 201 Refund. Replay with same key+body → 200 same Refund. Same key, different body → 409 `idempotency_mismatch` |
| POST | `/api/refunds/{id}/approve` | refund_approver, finance_admin | body `{expected_version, comment?}`. Requester can't approve own (403). One decision per approver. When approvals ≥ required → `approved` |
| POST | `/api/refunds/{id}/reject` | refund_approver, finance_admin | body `{expected_version, reason}` → `rejected`, releases reservation |
| GET | `/api/refunds/{id}/audit` | ALL_READ | audit entries for the refund, oldest first |
| GET | `/api/refunds/export.csv?status=` | finance_admin | CSV export (Excel connector equivalent) |

### Schemas
```text
Payment { id, provider_ref, customer_name, customer_email*, amount_cents, currency,
          reserved_cents, refunded_cents, refundable_cents, captured_at }
          *masked ("a***@customer.test") for callers without refund_agent/refund_approver/finance_admin
Refund  { id, payment_id, amount_cents, currency, reason, status, requested_by,
          required_approvals, approvals_count, provider_refund_id, failure_reason,
          version, created_at, updated_at }
RefundDetail = Refund + { approvals: [{approver, decision, comment, created_at}],
                          payment: Payment }
AuditEntry { id, occurred_at, actor, action, entity_type, entity_id, before, after, request_id }
```

### Refund state machine
```
pending_approval --approve (enough approvals)--> approved --worker--> processing --> succeeded
        |                                                                 \--> failed
        \--reject--> rejected
```
- Creation **reserves** the amount on the payment atomically
  (`reserved_cents + amount <= amount_cents`, also a DB CHECK). `rejected`/`failed` release it;
  `succeeded` moves it into `refunded_cents`.
- Every transition is a conditional update on `(id, status, version)`; `version` increments.
  A stale `expected_version` → 409 `version_conflict`.
- `required_approvals` is set at creation by the workflow engine (default 1; 0 = auto-approve).
- Execution against the payment provider happens in the worker, never in a request:
  `approved → processing` (commit) → provider call with idempotency key = refund id →
  `succeeded|failed` (commit). Stuck `processing` rows are reconciled by re-calling the
  (idempotent) provider.

## Workflows / notifications
| method | path | roles | notes |
|---|---|---|---|
| GET | `/api/workflows/rules` | ALL_READ | the loaded rules (read-only; edited via PR to `config/workflows.yaml`) |
| GET | `/api/notifications?limit=` | finance_admin, refund_approver | outbox messages, newest first: `{id, topic, payload, created_at, dispatched_at, attempts, last_error}` |

Domain events emitted by refunds (`app.platform.workflows.on_event`): `refund.created`,
`refund.approved`, `refund.rejected`, `refund.succeeded`, `refund.failed`.
`data` always includes `amount_cents`, `currency`, `status`, `requested_by`, `payment_id`.
