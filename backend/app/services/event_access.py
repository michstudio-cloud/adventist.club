"""Who may do what in an event (spec 2026-09-24-eventos §3.3).

Two kinds of authority, kept apart on purpose:

  * PLATFORM authority — "is association admin (or higher) over org X". It lives in ONE
    function, `can_manage_events_of`. TODAY it reads `users.role` + `users.organization_id`
    with the ltree scope helpers of `app/rbac.py`.

    TODO(role_assignments): when the branch that adds `role_assignments` and
    `rbac.has_role(user, role, org)` merges (spec §1.1), replace the body of
    `can_manage_events_of` with `has_role(actor, ADMIN_ASSOCIATION, organization)` (which
    already inherits upwards through ltree and covers MASTER_GC). Nothing else here, nor in
    the router or services, needs to change: every "may manage this event" decision goes
    through `can_manage_events_of` / `event_roles`.

  * EVENT authority — COORDINATOR / JUDGE rows of `event_staff`. Contextual: never touches
    `users.role`. And the DIRECTOR of a registered club, who only ever sees their own club.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubMembership, Event, EventStaff, Organization, User
from app.rbac import director_blocked, get_org_path, is_master, org_in_subtree
from app.security import ADMIN_ASSOCIATION, CLUB_DIRECTOR, ROLE_RANK

COORDINATOR = "COORDINATOR"
JUDGE = "JUDGE"
# Organization types that may own an event.
OWNER_TYPES = ("association", "union", "division")


async def can_manage_events_of(
    db: AsyncSession, actor: User | None, organization_id: uuid.UUID | None
) -> bool:
    """ADMIN_ASSOCIATION (or higher) whose scope contains `organization_id`; MASTER_GC
    everywhere. THE seam for `rbac.has_role` (see the module docstring)."""
    if actor is None or organization_id is None or actor.status != "ACTIVE":
        return False
    if is_master(actor):
        return True
    if ROLE_RANK.get(actor.role, 0) < ROLE_RANK[ADMIN_ASSOCIATION]:
        return False
    scope_path = await get_org_path(db, actor.organization_id)
    return await org_in_subtree(db, organization_id, scope_path)


async def director_club_ids(db: AsyncSession, actor: User) -> set[uuid.UUID]:
    """The clubs `actor` directs today: an ACTIVE CLUB_DIRECTOR membership, or (legacy) a
    CLUB_DIRECTOR attached to an active club whose own sign-up is not pending/rejected."""
    clubs = set(
        (
            await db.execute(
                select(ClubMembership.club_id).where(
                    ClubMembership.user_id == actor.id,
                    ClubMembership.role == CLUB_DIRECTOR,
                    ClubMembership.status == "ACTIVE",
                )
            )
        ).scalars()
    )
    if actor.role == CLUB_DIRECTOR and actor.organization_id and not director_blocked(actor):
        club = await db.get(Organization, actor.organization_id)
        if club is not None and club.type == "club" and club.status == "active":
            clubs.add(club.id)
    return clubs


@dataclass
class EventRoles:
    admin: bool = False
    coordinator: bool = False
    # Activities the actor judges; `judge_all` = an assignment with activity NULL.
    judge_all: bool = False
    judge_activity_ids: set[uuid.UUID] = field(default_factory=set)
    director_club_ids: set[uuid.UUID] = field(default_factory=set)

    @property
    def coordination(self) -> bool:
        """Manage the event: admin in scope or COORDINATOR of the event."""
        return self.admin or self.coordinator

    @property
    def judge(self) -> bool:
        return self.judge_all or bool(self.judge_activity_ids)

    def judges(self, activity_id: uuid.UUID, parent_id: uuid.UUID | None = None) -> bool:
        """Assigned to this activity (or to its group, or to all)."""
        return (
            self.judge_all
            or activity_id in self.judge_activity_ids
            or (parent_id is not None and parent_id in self.judge_activity_ids)
        )

    @property
    def any(self) -> bool:
        return self.coordination or self.judge or bool(self.director_club_ids)

    def names(self) -> list[str]:
        names = []
        if self.admin:
            names.append("ADMIN")
        if self.coordinator:
            names.append(COORDINATOR)
        if self.judge:
            names.append(JUDGE)
        if self.director_club_ids:
            names.append("DIRECTOR")
        return names


async def event_roles(
    db: AsyncSession,
    actor: User | None,
    event: Event,
    *,
    registered_club_ids: set[uuid.UUID] | None = None,
) -> EventRoles:
    """Everything `actor` is in `event`. `registered_club_ids` (clubs with a registration in
    this event, any status) narrows the director role to clubs that take part."""
    roles = EventRoles()
    if actor is None or actor.status != "ACTIVE":
        return roles
    roles.admin = await can_manage_events_of(db, actor, event.organization_id)
    staff = (
        await db.execute(
            select(EventStaff).where(
                EventStaff.event_id == event.id, EventStaff.user_id == actor.id, EventStaff.active
            )
        )
    ).scalars()
    for row in staff:
        if row.role == COORDINATOR:
            roles.coordinator = True
        elif row.role == JUDGE:
            if row.activity_id is None:
                roles.judge_all = True
            else:
                roles.judge_activity_ids.add(row.activity_id)
    clubs = await director_club_ids(db, actor)
    if registered_club_ids is not None:
        clubs &= registered_club_ids
    roles.director_club_ids = clubs
    return roles
