"""Local demo data: realistic refund history created through the real API.

Run inside the stack (development only):
    docker compose exec api python -m app.demo_seed

Every refund goes through the same HTTP path a user would take (OIDC token -> RBAC -> RLS ->
reservation/idempotency -> approvals -> payout worker). Only afterwards are refund and approval
timestamps shifted into the last 14 days, so the dashboard has a history; that one UPDATE runs as
the schema owner. Audit-log timestamps are left as recorded, because the audit log is append-only.

Re-running is safe: idempotency keys are deterministic, so existing refunds are replayed, not
duplicated.
"""

import os
from datetime import timedelta
from itertools import zip_longest

import httpx
from sqlalchemy import create_engine, text

from app.apps.refunds.execution import execute_pending
from app.config import settings

API = os.environ.get("DEMO_API_URL", "http://localhost:8000")
TOKEN_URL = os.environ.get(
    "DEMO_TOKEN_URL", "http://keycloak:8080/realms/internal-tools/protocol/openid-connect/token"
)
PASSWORD = "demo-password"  # noqa: S105 - local Keycloak demo users only
KEY_PREFIX = "demo-seed-"
REFUNDS_PER_UNIT = {"US": 26, "EU": 14}
AGENT = {"US": "alice", "EU": "frank"}
APPROVERS = {"US": ["bob", "carol", "dave"], "EU": ["carol", "dave"]}
REASONS = [
    "Customer charged twice",
    "Item never delivered",
    "Damaged on arrival",
    "Subscription cancelled within trial",
    "Goodwill credit after outage",
    "Wrong size shipped",
]


def token(username: str) -> str:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "internal-tools-dev-cli",
            "username": username,
            "password": PASSWORD,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


class Client:
    def __init__(self, username: str):
        self.username = username
        self.http = httpx.Client(
            base_url=API, headers={"Authorization": f"Bearer {token(username)}"}, timeout=10
        )

    def get(self, path: str, **params):
        resp = self.http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, body: dict, **headers):
        return self.http.post(path, json=body, headers=headers)


def plan_amount(i: int, captured_cents: int) -> int:
    """Deterministic mix of partial and full refunds, plus a few fake-provider failure triggers.

    Derived only from the immutable captured amount, so a re-run sends the identical request body
    and the idempotency key replays it instead of conflicting.
    """
    if i % 9 == 4:
        return captured_cents
    amount = max(100, captured_cents * (20 + i * 37 % 60) // 100)
    if i % 11 == 7 and amount >= 113:  # ends in 13 cents -> fake provider declines it
        amount = amount - amount % 100 + 13
        if amount > captured_cents:
            amount -= 100
    return amount


def seed_unit(unit: str) -> list[str]:
    """One refund per distinct payment, so reservations never compete with each other."""
    agent = Client(AGENT[unit])
    approvers = [Client(u) for u in APPROVERS[unit]]
    payments = [p for p in agent.get("/api/payments") if p["amount_cents"] >= 100]
    ids = []
    for i, payment in enumerate(payments[: REFUNDS_PER_UNIT[unit]]):
        resp = agent.post(
            "/api/refunds",
            {
                "payment_id": payment["id"],
                "amount_cents": plan_amount(i, payment["amount_cents"]),
                "reason": REASONS[i % len(REASONS)],
            },
            **{"Idempotency-Key": f"{KEY_PREFIX}{unit.lower()}-{i:03d}"},
        )
        if resp.status_code not in (200, 201):
            print(f"  skip {unit}-{i}: {resp.status_code} {resp.text[:120]}")
            continue
        refund = resp.json()
        ids.append(refund["id"])
        decide(i, refund, approvers)
    return ids


def decide(i: int, refund: dict, approvers: list[Client]) -> None:
    """Most refunds get approved, every 7th is rejected, and the newest few stay pending."""
    if i >= REFUNDS_PER_UNIT[refund["business_unit"]] - 5:
        return
    for approver in approvers:
        refund = approver.get(f"/api/refunds/{refund['id']}")
        if not refund["status"].startswith("pending"):
            return
        if i % 7 == 3:
            approver.post(
                f"/api/refunds/{refund['id']}/reject",
                {"expected_version": refund["version"], "reason": "Outside refund policy"},
            )
            return
        approver.post(
            f"/api/refunds/{refund['id']}/approve",
            {"expected_version": refund["version"], "comment": "Verified with order history"},
        )


def backfill(ids: list[str]) -> None:
    """Spread the seeded refunds over the last 14 days, newest last; approvals follow requests."""
    engine = create_engine(settings.migration_database_url)
    with engine.begin() as conn:
        for n, refund_id in enumerate(ids):
            age = timedelta(hours=(len(ids) - n) * 14 * 24 / (len(ids) + 1))
            to_approve = timedelta(minutes=20 + (n * 53) % 600)
            conn.execute(
                text(
                    "UPDATE refunds SET created_at = now() - :age,"
                    " updated_at = now() - :age + :tta + interval '2 minutes' WHERE id = :id"
                    " AND idempotency_key LIKE :prefix"
                ),
                {"age": age, "tta": to_approve, "id": refund_id, "prefix": f"{KEY_PREFIX}%"},
            )
            conn.execute(
                text(
                    "UPDATE refund_approvals SET created_at = now() - :age + :tta"
                    " + (id % 5) * interval '7 minutes' WHERE refund_id = :id"
                ),
                {"age": age, "tta": to_approve, "id": refund_id},
            )


def main() -> None:
    if settings.is_production:
        raise SystemExit("demo_seed is for local development only")
    by_unit = []
    for unit in REFUNDS_PER_UNIT:
        print(f"seeding {unit} refunds through the API...")
        by_unit.append(seed_unit(unit))
    paid = 0
    while processed := execute_pending():
        paid += processed
    print(f"payout worker processed {paid} refunds")
    ids = [r for pair in zip_longest(*by_unit) for r in pair if r]
    backfill(ids)
    print(f"done: {len(ids)} demo refunds")


if __name__ == "__main__":
    main()
