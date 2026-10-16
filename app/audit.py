from sqlalchemy.orm import Session

from .models import AuditLog, User


def record(
    session: Session,
    user: User | None,
    action: str,
    entity: str,
    entity_id: str,
    before: dict | None,
    after: dict | None,
    reason: str = "",
) -> None:
    session.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
            reason=reason,
        )
    )
