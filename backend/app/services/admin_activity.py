"""«Actividad» of the administration: the latest account, club and director events of the
admin's scope, read from `audit_log` (`record_audit`). Read-only.

Which events: a fixed list (`ACTIONS`) — sign-ups, club requests and decisions, directors
appointed, director requests (sign-up, event pass, «Mi club ya está en el evento») and their
decisions, and accepted invitations. The web paints each `action` with its own label.

Scope, strictly: every row is placed on ONE node — the club named in its metadata
(`club_id`), else the organization it is about (`entity_type = ORGANIZATION`), else the
`organization_id` of its metadata, else the organization of whoever did it at the time — and
an administrator only reads rows whose node is inside the subtree of one of their
administrative roles (ADMIN_ASSOCIATION or higher, `rbac.effective_roles`). A row with no
node (e.g. somebody who signed up without a club) is only for MASTER_GC.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import AuditLog, Organization, User
from app.rbac import effective_roles, is_master
from app.security import ADMIN_ASSOCIATION, ROLE_RANK, utcnow

ACTIONS = (
    "REGISTER",
    "CLUB_REQUEST",
    "CLUB_APPROVE",
    "CLUB_REJECT",
    "CLUB_DIRECTOR_ASSIGN",
    "CLUB_DIRECTOR_REQUEST",
    "CLUB_DIRECTOR_REQUEST_APPROVE",
    "CLUB_DIRECTOR_REQUEST_REJECT",
    "EVENT_DIRECTOR_REQUEST",
    "EVENT_DIRECTOR_SELF_REGISTER",
    "EVENT_DIRECTOR_APPROVE",
    "EVENT_DIRECTOR_LINK",
    "ORG_INVITATION_ACCEPT",
)
WINDOW = timedelta(days=180)


async def scope_paths(db: AsyncSession, actor: User) -> list[str] | None:
    """None: everything (MASTER_GC). Otherwise the subtrees of the actor's roles of
    ADMIN_ASSOCIATION rank or higher (empty: nothing)."""
    if is_master(actor):
        return None
    floor = ROLE_RANK[ADMIN_ASSOCIATION]
    return sorted({
        scoped.path
        for scoped in await effective_roles(db, actor)
        if scoped.path and ROLE_RANK.get(scoped.role, 0) >= floor
    })


async def may_read(db: AsyncSession, actor: User) -> bool:
    paths = await scope_paths(db, actor)
    return paths is None or bool(paths)


def _node_id():
    return func.coalesce(
        AuditLog.metadata_json["club_id"].astext,
        case((AuditLog.entity_type == "ORGANIZATION", AuditLog.entity_id)),
        AuditLog.metadata_json["organization_id"].astext,
        cast(AuditLog.organization_id, String),
    )


async def latest(db: AsyncSession, actor: User, *, limit: int = 50, offset: int = 0,
                 action: str | None = None) -> dict:
    """A page, newest first: `{"items": [...], "has_more": bool}`."""
    paths = await scope_paths(db, actor)
    if paths is not None and not paths:
        return {"items": [], "has_more": False}
    node = aliased(Organization)
    person = aliased(User)
    stmt = (
        select(AuditLog, node, person.name)
        .outerjoin(node, cast(node.id, String) == _node_id())
        .outerjoin(person, person.id == AuditLog.user_id)
        .where(
            AuditLog.action.in_((action,) if action else ACTIONS),
            AuditLog.created_at > utcnow() - WINDOW,
        )
    )
    if paths is not None:
        stmt = stmt.where(node.id.is_not(None), or_(*(node.path.op("<@")(path) for path in paths)))
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1).offset(offset)
    rows = (await db.execute(stmt)).all()
    items = []
    for entry, organization, actor_name in rows[:limit]:
        meta = entry.metadata_json or {}
        items.append({
            "id": str(entry.id),
            "action": entry.action,
            "created_at": entry.created_at,
            "actor": {"name": actor_name, "email": entry.user_email, "role": entry.user_role}
            if entry.user_id or entry.user_email else None,
            "organization": {"id": str(organization.id), "name": organization.name, "type": organization.type}
            if organization is not None else None,
            "via": meta.get("via") if isinstance(meta.get("via"), str) else None,
            "role": meta.get("role") if isinstance(meta.get("role"), str) else None,
            "reason": meta.get("reason") if isinstance(meta.get("reason"), str) else None,
        })
    return {"items": items, "has_more": len(rows) > limit}
