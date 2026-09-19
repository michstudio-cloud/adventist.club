"""Audit trail. Rows are added to the caller's session so they commit (or roll
back) together with the change they describe."""
import ipaddress
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User
from app.rate_limit import client_ip


def _valid_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    try:
        return str(ipaddress.ip_address(client_ip(request)))
    except ValueError:
        return None


def record_audit(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: Any,
    actor: User | None = None,
    details: str | None = None,
    metadata: dict | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Stage an audit row. Does not flush or commit."""
    entry = AuditLog(
        id=uuid.uuid4(),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        user_id=actor.id if actor else None,
        user_email=actor.email if actor else None,
        user_role=actor.role if actor else None,
        organization_id=actor.organization_id if actor else None,
        details=details,
        metadata_json=metadata,
        ip_address=_valid_ip(request),
    )
    db.add(entry)
    return entry
