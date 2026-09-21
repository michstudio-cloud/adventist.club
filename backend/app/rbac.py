"""
Permission helpers.

Scope rule: an admin-type role acts on anything whose organization sits in the
subtree rooted at the admin's own organization (`target.path <@ admin.path`).
MASTER_GC is global. The legacy API only compared organization ids for
equality and left the hierarchy as a TODO.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Guardianship, HonorEnrollment, Organization, User
from app.security import (
    ADMIN_ROLES,
    CLUB_APPROVED,
    CLUB_DIRECTOR,
    COORDINATOR_ZONE,
    INSTRUCTOR,
    MASTER_GC,
    ROLE_RANK,
)

# Roles that may look at the members of their own organization subtree.
MEMBER_VIEW_ROLES = (*ADMIN_ROLES, CLUB_DIRECTOR, INSTRUCTOR)


def is_admin_role(user: User) -> bool:
    return user.role in ADMIN_ROLES


def is_master(user: User) -> bool:
    return user.role == MASTER_GC


def outranks(actor: User, role: str) -> bool:
    """True when `actor` has strictly more authority than `role`."""
    if is_master(actor):
        return True
    return ROLE_RANK.get(actor.role, 0) > ROLE_RANK.get(role, 0)


async def get_org_path(db: AsyncSession, organization_id: uuid.UUID | None) -> str | None:
    if organization_id is None:
        return None
    stmt = select(Organization.path).where(Organization.id == organization_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def org_in_subtree(
    db: AsyncSession, organization_id: uuid.UUID | None, scope_path: str | None
) -> bool:
    """Is `organization_id` equal to or a descendant of the node at `scope_path`?"""
    if organization_id is None or not scope_path:
        return False
    stmt = select(Organization.id).where(
        Organization.id == organization_id,
        Organization.path.op("<@")(scope_path),
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def org_in_user_scope(
    db: AsyncSession, actor: User, organization_id: uuid.UUID | None
) -> bool:
    if is_master(actor):
        return True
    scope_path = await get_org_path(db, actor.organization_id)
    return await org_in_subtree(db, organization_id, scope_path)


def director_blocked(user: User) -> bool:
    """A self-registered director whose club is still pending (or was rejected)
    has no authority over members yet. Directors appointed by an administrator
    have `club_approval` NULL and are not affected."""
    return (
        user.role == CLUB_DIRECTOR
        and user.club_approval is not None
        and user.club_approval != CLUB_APPROVED
    )


async def club_scope_paths(db: AsyncSession, actor: User) -> list[str] | None:
    """
    Subtrees whose club requests `actor` may see and decide. None means global.

    Administrators decide on clubs below their own organization. A zone
    coordinator sits *beside* the clubs (both hang from the association), so
    their scope is the whole association that contains their organization.
    """
    if is_master(actor):
        return None
    if actor.role not in ADMIN_ROLES:
        return []
    own_path = await get_org_path(db, actor.organization_id)
    if not own_path:
        return []
    paths = [own_path]
    if actor.role == COORDINATOR_ZONE:
        stmt = select(Organization.path).where(
            Organization.type == "association",
            Organization.path.op("@>")(own_path),
        )
        paths.extend(path for path in (await db.execute(stmt)).scalars() if path != own_path)
    return paths


async def can_decide_club(db: AsyncSession, actor: User, club: Organization) -> bool:
    paths = await club_scope_paths(db, actor)
    if paths is None:
        return True
    for scope_path in paths:
        if await org_in_subtree(db, club.id, scope_path):
            return True
    return False


async def can_view_user(db: AsyncSession, actor: User, target: User) -> bool:
    if actor.id == target.id or is_master(actor):
        return True
    if director_blocked(actor):
        return False
    if actor.role in MEMBER_VIEW_ROLES:
        return await org_in_user_scope(db, actor, target.organization_id)
    return False


async def can_manage_user(db: AsyncSession, actor: User, target: User) -> bool:
    """Admin-level control over another account (edit admin fields, verify, delete)."""
    if actor.id == target.id or not is_admin_role(actor):
        return False
    if is_master(actor):
        return True
    if not outranks(actor, target.role):
        return False
    return await org_in_user_scope(db, actor, target.organization_id)


# ----------------------------------------------------------------------------
# Portfolio (Bloque A): who reviews, who issues, who may look.
# ----------------------------------------------------------------------------
# guardianships.consent_status once the guardian granted consent (the column's CHECK
# allows PENDING / APPROVED / REJECTED).
CONSENT_GRANTED = "APPROVED"
CLUB_REVIEW_ROLES = (CLUB_DIRECTOR, INSTRUCTOR)


async def member_club(db: AsyncSession, member: User | None) -> Organization | None:
    """The member's CURRENT club: their organization, when it is an active club."""
    if member is None or member.organization_id is None:
        return None
    club = await db.get(Organization, member.organization_id)
    if club is None or club.type != "club" or club.status != "active":
        return None
    return club


def club_staff_in_good_standing(actor: User, roles: tuple[str, ...] = CLUB_REVIEW_ROLES) -> bool:
    """May `actor` act as staff of the club they are attached to? A director needs an approved
    club (`director_blocked`); an instructor needs an active, VERIFIED account. VERIFIED today
    only means a confirmed e-mail or an administrator's approval, not a vetted instructor: the
    real protection is that nobody picks their own `organization_id` (POST /auth/register
    rejects it), so whoever is attached to a club was placed there by an administrator."""
    if actor.role not in roles or actor.organization_id is None or actor.status != "ACTIVE":
        return False
    if actor.role == INSTRUCTOR and actor.verification_status != "VERIFIED":
        return False
    return not director_blocked(actor)


async def _has_club_jurisdiction(
    db: AsyncSession, actor: User, enrollment: HonorEnrollment, roles: tuple[str, ...]
) -> bool:
    """`actor` holds one of `roles` in the club the enrolled member belongs to today
    (not the club stored on the enrollment: a member who moves takes their reviewers along)."""
    if not club_staff_in_good_standing(actor, roles):
        return False
    club = await member_club(db, await db.get(User, enrollment.user_id))
    return club is not None and club.id == actor.organization_id


async def can_review(db: AsyncSession, actor: User, enrollment: HonorEnrollment) -> bool:
    """The single place that decides who gives verdicts on an enrollment."""
    if actor.id == enrollment.user_id:
        return False  # nobody reviews their own work, whatever their role
    if is_master(actor):
        return True
    if await _has_club_jurisdiction(db, actor, enrollment, CLUB_REVIEW_ROLES):
        return True
    # Bloque B: `or` the instructor of the course of a COURSE enrollment, here and only here.
    return False


async def can_issue(db: AsyncSession, actor: User, enrollment: HonorEnrollment) -> bool:
    if actor.id == enrollment.user_id:
        return False
    if is_master(actor):
        return True
    if enrollment.mode != "CLUB":
        return False  # COURSE: the course instructor issues (Bloque D)
    return await _has_club_jurisdiction(db, actor, enrollment, (CLUB_DIRECTOR,))


async def can_view_portfolio(db: AsyncSession, actor: User, target: User) -> bool:
    """Read access to someone's enrollments, evidence and certificates."""
    if await can_view_user(db, actor, target):
        # the person themself, MASTER_GC and the hierarchy above them. Club staff only while in
        # good standing: a portfolio holds evidence of minors, the user directory does not.
        own_or_admin = actor.id == target.id or actor.role not in CLUB_REVIEW_ROLES
        if own_or_admin or club_staff_in_good_standing(actor):
            return True
    consent = select(Guardianship.id).where(
        Guardianship.guardian_id == actor.id,
        Guardianship.child_id == target.id,
        Guardianship.consent_status == CONSENT_GRANTED,
    )
    if (await db.execute(consent.limit(1))).scalar_one_or_none() is not None:
        return True
    # Reviewers with jurisdiction over any live enrollment. Today can_view_user already
    # covers the club's staff; course instructors (Bloque B) will only get in through here.
    enrollments = select(HonorEnrollment).where(
        HonorEnrollment.user_id == target.id, HonorEnrollment.status != "WITHDRAWN"
    )
    for enrollment in (await db.execute(enrollments)).scalars().all():
        if await can_review(db, actor, enrollment):
            return True
    return False
