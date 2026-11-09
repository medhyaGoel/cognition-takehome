"""Idempotent demo data. Safe to run on every container start."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Payment

DEMO_PAYMENTS = [
    ("ch_1001", "Ada Lovelace", "ada@customer.test", 4_500, "USD"),
    ("ch_1002", "Grace Hopper", "grace@customer.test", 25_000, "USD"),
    ("ch_1003", "Alan Turing", "alan@customer.test", 120_000, "USD"),
    ("ch_1004", "Katherine Johnson", "katherine@customer.test", 9_999, "USD"),
    ("ch_1005", "Edsger Dijkstra", "edsger@customer.test", 75_000, "USD"),
    ("ch_1006", "Barbara Liskov", "barbara@customer.test", 3_200, "USD"),
]


def main() -> None:
    with SessionLocal() as db, db.begin():
        existing = set(db.scalars(select(Payment.provider_ref)))
        for ref, name, email, amount, currency in DEMO_PAYMENTS:
            if ref not in existing:
                db.add(
                    Payment(
                        provider_ref=ref,
                        customer_name=name,
                        customer_email=email,
                        amount_cents=amount,
                        currency=currency,
                    )
                )


if __name__ == "__main__":
    main()
