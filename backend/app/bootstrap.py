"""Local/dev bootstrap, run by the one-shot `migrate` compose service as the schema owner:
1. alembic upgrade head
2. enable login for runtime roles with passwords taken from DATABASE_URL / WORKER_DATABASE_URL
   (production: roles and credentials are provisioned by the platform team, not this script)
3. sync payments from the system of record
"""

import os

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.platform.connectors.payments_source import PostgresPaymentsSource, sync_payments

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def migrate(owner_url: str) -> None:
    cfg = Config(os.path.join(_HERE, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_HERE, "migrations"))
    cfg.attributes["database_url"] = owner_url
    command.upgrade(cfg, "head")


def enable_runtime_logins(owner_url: str, runtime_urls: list[str]) -> None:
    if settings.is_production:
        raise RuntimeError("runtime DB credentials are provisioned externally in production")
    owner = create_engine(owner_url, isolation_level="AUTOCOMMIT")
    with owner.connect() as conn:
        raw = conn.connection.dbapi_connection
        for url in runtime_urls:
            parsed = make_url(url)
            raw.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(parsed.username), sql.Literal(parsed.password)
                )
            )
    owner.dispose()


def main() -> None:
    migrate(settings.migration_database_url)
    enable_runtime_logins(
        settings.migration_database_url, [settings.database_url, settings.worker_database_url]
    )
    owner = create_engine(settings.migration_database_url)
    with Session(owner) as db:
        report = sync_payments(db, PostgresPaymentsSource(settings.source_database_url))
    print(f"payments sync: inserted={report.inserted} drift={report.drift}")  # noqa: T201


if __name__ == "__main__":
    main()
