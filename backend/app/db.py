"""Database sessions.

Two runtime engines, each connecting as a least-privilege Postgres role:
  * `app_api`    — serves HTTP requests; subject to row-level security (RLS).
  * `app_worker` — background jobs (outbox, payouts); explicit cross-business-unit policy.

RLS context: every transaction opened on a session that carries a Principal (see
`session_for`) runs `set_config('app.user', ..., true)` and `set_config('app.business_units',
..., true)` first. Settings are transaction-local, so pooled connections never leak identity.
A session without a principal has no context and RLS returns zero rows (fail-closed).
"""

from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings
from app.platform.auth import Principal, get_principal

engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
worker_engine = create_engine(settings.worker_database_url, pool_pre_ping=True, pool_size=5)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
WorkerSessionLocal = sessionmaker(bind=worker_engine, expire_on_commit=False, autoflush=False)

WORKER_ACTOR = "system:worker"


class Base(DeclarativeBase):
    pass


@event.listens_for(Session, "after_begin")
def _apply_rls_context(session: Session, _transaction, connection) -> None:
    principal: Principal | None = session.info.get("principal")
    if principal is None:
        return
    connection.execute(
        text("SELECT set_config('app.user', :u, true), set_config('app.business_units', :b, true)"),
        {"u": principal.subject, "b": ",".join(sorted(principal.business_units))},
    )


def session_for(principal: Principal) -> Session:
    """A request-scoped session whose every transaction is bound to `principal` for RLS."""
    session = SessionLocal()
    session.info["principal"] = principal
    return session


def get_db(principal: Principal = Depends(get_principal)) -> Iterator[Session]:
    """Route dependency. Services own the transaction boundary (`with db.begin():`)."""
    with session_for(principal) as session:
        yield session
