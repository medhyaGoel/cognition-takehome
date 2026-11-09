from collections.abc import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Notification


def list_recent(db: Session, *, roles: Iterable[str], limit: int) -> list[Notification]:
    """Newest first, only notifications addressed to one of the caller's roles (or to no role).
    Runs as the caller (`get_db`), so RLS also limits rows to their business units."""
    addressed = or_(
        Notification.recipient_role.is_(None), Notification.recipient_role.in_(sorted(roles))
    )
    with db.begin():
        stmt = (
            select(Notification)
            .where(addressed)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
        )
        return list(db.scalars(stmt.limit(limit)))
