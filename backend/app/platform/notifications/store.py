from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification


def list_recent(db: Session, *, limit: int) -> list[Notification]:
    """Newest first. Runs as the caller (`get_db`), so RLS limits rows to their business units."""
    with db.begin():
        stmt = select(Notification).order_by(Notification.created_at.desc(), Notification.id.desc())
        return list(db.scalars(stmt.limit(limit)))
