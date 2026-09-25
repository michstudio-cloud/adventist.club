"""Several scoped roles per person (Bloque I §1.1, 026_role_assignments.sql).

`role_assignments` holds every role a person has, each on a node of the tree. The
columns every existing check reads — `users.role` and `users.organization_id` — stay,
and are the PRINCIPAL role: the highest `ROLE_RANK` among the active assignments.
`recompute` is the ONE place that writes them from the assignments.

Three rules keep the old code working unchanged:

  1. The legacy writers (the membership service, `PATCH /users/{id}`, sign-up) still
     write those two columns. The `is_primary` row mirrors them; `sync_legacy` catches
     it up at the start of every write here, and `rbac.effective_roles` never trusts the
     mirror (it reads the columns), so a legacy change counts at once.
  2. A club role lives in `club_memberships` (spec E §6). While somebody holds an ACTIVE
     membership, their principal stays that membership unless an ADMINISTRATIVE role
     outranks it: only then does `users.organization_id` leave the club. Club rows here
     are a mirror of the membership (`mirror_club_membership`).
  3. Everything stages on the caller's session and writes its audit row; nothing commits.
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubMembership, Organization, RoleAssignment, User
from app.security import ADMIN_ROLES, CLUB_LEVEL_ROLES, MASTER_GC, ROLE_RANK, STUDENT, utcnow
from app.services.audit import record_audit

ACTIVE, ENDED = "ACTIVE", "ENDED"
# source
BACKFILL, LEGACY, INVITATION, CLUB, ADMIN = "BACKFILL", "LEGACY", "INVITATION", "CLUB", "ADMIN"
# end_reason
REPLACED = "REPLACED"  # the legacy columns moved on without it
RETIRED = "RETIRED"  # somebody with authority took it away
MEMBERSHIP_CLOSED = "MEMBERSHIP_CLOSED"  # the club membership it mirrored ended or changed

ROLE_ASSIGNMENT = "ROLE_ASSIGNMENT"

CLUB_ROLE_DETAIL = (
    "Los roles de club se gestionan desde la membresía del club, no desde el equipo."
)


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------
async def active_of(db: AsyncSession, user_id: uuid.UUID) -> list[RoleAssignment]:
    stmt = (
        select(RoleAssignment)
        .where(RoleAssignment.user_id == user_id, RoleAssignment.status == ACTIVE)
        .order_by(RoleAssignment.granted_at, RoleAssignment.id)
    )
    return list((await db.execute(stmt)).scalars().all())


def _find(rows: list[RoleAssignment], role: str, org_id) -> RoleAssignment | None:
    return next((r for r in rows if r.role == role and r.organization_id == org_id), None)


async def _active_membership(db: AsyncSession, user_id: uuid.UUID) -> ClubMembership | None:
    stmt = select(ClubMembership).where(
        ClubMembership.user_id == user_id, ClubMembership.status == ACTIVE
    )
    return (await db.execute(stmt)).scalars().first()


def _stage(
    db: AsyncSession,
    *,
    user_id,
    role: str,
    org_id,
    source: str,
    primary: bool = False,
    granted_by: User | None = None,
    invitation_id=None,
) -> RoleAssignment:
    row = RoleAssignment(
        id=uuid.uuid4(),
        user_id=user_id,
        role=role,
        organization_id=org_id,
        status=ACTIVE,
        is_primary=primary,
        source=source,
        invitation_id=invitation_id,
        granted_by_id=granted_by.id if granted_by else None,
        granted_at=utcnow(),
    )
    db.add(row)
    return row


def _close(row: RoleAssignment, *, reason: str, actor: User | None) -> None:
    row.status = ENDED
    row.is_primary = False
    row.ended_at = utcnow()
    row.ended_by_id = actor.id if actor else None
    row.end_reason = reason


# ----------------------------------------------------------------------------
# The mirror of the legacy columns
# ----------------------------------------------------------------------------
async def sync_legacy(db: AsyncSession, user: User) -> None:
    """Make the `is_primary` row say what `users.role` + `users.organization_id` say.

    When legacy code moved those columns (a club transfer, `PATCH /users/{id}`), the
    old principal is over — it is ENDED as REPLACED — and the new pair becomes the
    principal, reusing an active row for it when there is one."""
    rows = await active_of(db, user.id)
    primary = next((r for r in rows if r.is_primary), None)
    pair = (user.role, user.organization_id)
    if primary is not None and (primary.role, primary.organization_id) == pair:
        return
    if primary is not None:
        _close(primary, reason=REPLACED, actor=None)
        await db.flush()  # the partial unique index allows ONE active primary
    match = _find(rows, *pair)
    if match is not None and match.status == ACTIVE:
        match.is_primary = True
    else:
        _stage(db, user_id=user.id, role=user.role, org_id=user.organization_id,
               source=LEGACY, primary=True)
    await db.flush()


def _rank_key(row: RoleAssignment):
    # Highest rank; on a tie the current principal stays (no flapping), then the oldest.
    return (-ROLE_RANK.get(row.role, 0), not row.is_primary, row.granted_at)


async def recompute(db: AsyncSession, user: User) -> tuple[str, uuid.UUID | None]:
    """THE place that writes `users.role` and `users.organization_id` from the
    assignments. Returns the principal pair. Call `sync_legacy` first."""
    rows = await active_of(db, user.id)
    membership = await _active_membership(db, user.id)
    best = min(rows, key=_rank_key) if rows else None

    if membership is not None and (best is None or best.role not in ADMIN_ROLES):
        # Rule 2: a club account stays attached to its club.
        principal = _find(rows, membership.role, membership.club_id)
        if principal is None:
            principal = _stage(db, user_id=user.id, role=membership.role,
                               org_id=membership.club_id, source=CLUB)
            rows.append(principal)
    elif best is not None:
        principal = best
    else:
        # Nothing left (the last role was retired): an account always has one.
        principal = _stage(db, user_id=user.id, role=STUDENT, org_id=None, source=LEGACY)
        rows.append(principal)

    for row in rows:
        if row.is_primary and row is not principal:
            row.is_primary = False
    await db.flush()
    principal.is_primary = True
    if (user.role, user.organization_id) != (principal.role, principal.organization_id):
        user.role = principal.role
        user.organization_id = principal.organization_id
    await db.flush()
    return principal.role, principal.organization_id


# ----------------------------------------------------------------------------
# Writers
# ----------------------------------------------------------------------------
async def grant(
    db: AsyncSession,
    *,
    user: User,
    role: str,
    organization: Organization | None,
    actor: User | None,
    source: str = ADMIN,
    invitation_id=None,
    request: Request | None = None,
    sync: bool = True,
) -> RoleAssignment:
    """Give `user` the role on `organization` (idempotent: an active one is returned as
    it is) and recompute the principal. Who may grant is the caller's decision.

    `sync=False` only when the caller already synced and then let the membership service
    write the legacy columns on purpose (appointing a director): syncing again would
    take that write for a demotion."""
    org_id = organization.id if organization is not None else None
    if org_id is None and role != MASTER_GC:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El rol necesita una organización.")
    if sync:
        await sync_legacy(db, user)
    existing = _find(await active_of(db, user.id), role, org_id)
    row = existing or _stage(
        db, user_id=user.id, role=role, org_id=org_id, source=source,
        granted_by=actor, invitation_id=invitation_id,
    )
    if existing is not None and invitation_id is not None and existing.invitation_id is None:
        # The club mirror the appointment just wrote: it came from this invitation.
        existing.invitation_id = invitation_id
        existing.granted_by_id = existing.granted_by_id or (actor.id if actor else None)
    await db.flush()
    before = user.role
    await recompute(db, user)
    if existing is None or invitation_id is not None:
        record_audit(
            db,
            action="ROLE_GRANT",
            entity_type=ROLE_ASSIGNMENT,
            entity_id=row.id,
            actor=actor or user,
            details=f"{user.email} is now {role} of {org_id}",
            metadata={
                "user_id": str(user.id),
                "role": role,
                "organization_id": str(org_id) if org_id else None,
                "source": source,
                "invitation_id": str(invitation_id) if invitation_id else None,
                "principal_before": before,
                "principal_after": user.role,
            },
            request=request,
        )
    return row


async def end(
    db: AsyncSession,
    row: RoleAssignment,
    *,
    user: User,
    actor: User | None,
    reason: str = RETIRED,
    request: Request | None = None,
) -> RoleAssignment:
    """Take one role away (idempotent) and recompute the principal. Club roles are
    refused: they end with the membership (`memberships.end`), which mirrors here."""
    if row.status != ACTIVE:
        return row
    if row.role in CLUB_LEVEL_ROLES and await _is_club(db, row.organization_id):
        raise HTTPException(status.HTTP_409_CONFLICT, CLUB_ROLE_DETAIL)
    await sync_legacy(db, user)
    await db.refresh(row)
    if row.status != ACTIVE:  # it was the stale mirror, already REPLACED
        return row
    _close(row, reason=reason, actor=actor)
    await db.flush()
    before = user.role
    await recompute(db, user)
    record_audit(
        db,
        action="ROLE_END",
        entity_type=ROLE_ASSIGNMENT,
        entity_id=row.id,
        actor=actor or user,
        details=f"{user.email} is no longer {row.role} of {row.organization_id}",
        metadata={
            "user_id": str(user.id),
            "role": row.role,
            "organization_id": str(row.organization_id) if row.organization_id else None,
            "reason": reason,
            "principal_before": before,
            "principal_after": user.role,
        },
        request=request,
    )
    return row


async def _is_club(db: AsyncSession, org_id) -> bool:
    if org_id is None:
        return False
    node = await db.get(Organization, org_id)
    return node is not None and node.type == "club"


async def mirror_club_membership(
    db: AsyncSession, user_id: uuid.UUID, club_id: uuid.UUID, *, actor: User | None = None
) -> None:
    """Called by the membership service after it changes a membership: the club rows of
    this person on this club follow it. A row whose membership ended (or whose role
    changed) is ENDED; the current role gets its row. Never touches `users` — that is
    the membership service's own write — and never recomputes."""
    membership = (
        await db.execute(
            select(ClubMembership).where(
                ClubMembership.user_id == user_id,
                ClubMembership.club_id == club_id,
                ClubMembership.status == ACTIVE,
            )
        )
    ).scalars().first()
    rows = [
        r for r in await active_of(db, user_id)
        if r.organization_id == club_id and r.role in CLUB_LEVEL_ROLES
    ]
    changed = False
    for row in rows:
        if membership is None or row.role != membership.role:
            _close(row, reason=MEMBERSHIP_CLOSED, actor=actor)
            changed = True
    if membership is not None and not any(
        r.role == membership.role and r.status == ACTIVE for r in rows
    ):
        _stage(db, user_id=user_id, role=membership.role, org_id=club_id, source=CLUB)
        changed = True
    if changed:
        await db.flush()


# ----------------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------------
async def roles_out(db: AsyncSession, user: User) -> list[dict]:
    """`roles` of `GET /auth/me`: every role in force, the principal first."""
    from app.rbac import effective_roles

    scoped = await effective_roles(db, user)
    ids = {s.organization_id for s in scoped if s.organization_id is not None}
    nodes = {}
    if ids:
        stmt = select(Organization).where(Organization.id.in_(ids))
        nodes = {n.id: n for n in (await db.execute(stmt)).scalars().all()}
    out = []
    for position, entry in enumerate(scoped):
        node = nodes.get(entry.organization_id)
        out.append({
            "role": entry.role,
            "principal": position == 0,
            "organization": (
                {"id": str(node.id), "name": node.name, "type": node.type.upper()}
                if node is not None else None
            ),
        })
    head, rest = out[:1], out[1:]
    rest.sort(key=lambda item: -ROLE_RANK.get(item["role"], 0))
    return head + rest


async def active_on(db: AsyncSession, organization: Organization) -> list[tuple[RoleAssignment, User]]:
    """The team of one node: the assignments in force ON it (not below it). A stale
    `is_primary` mirror is left out — the person's columns already say otherwise."""
    stmt = (
        select(RoleAssignment, User)
        .join(User, User.id == RoleAssignment.user_id)
        .where(RoleAssignment.organization_id == organization.id, RoleAssignment.status == ACTIVE)
        .order_by(RoleAssignment.granted_at)
    )
    out = []
    for row, person in (await db.execute(stmt)).all():
        if row.is_primary and (row.role, row.organization_id) != (person.role, person.organization_id):
            continue
        out.append((row, person))
    out.sort(key=lambda pair: -ROLE_RANK.get(pair[0].role, 0))
    return out
