"""Cargos del club (Bloque H §1).

A cargo is a TITLE, never a permission: the permissions keep coming from
`club_memberships.role` / `users.role`, so nothing in `app/rbac.py` reads this table except
to decide who hands cargos out (`can_manage_officers`). A cargo is never deleted: it is
closed with `until`, and one whose `until` is today or earlier is history.

Everything here only *stages* its change (row and audit) on the caller's session.
"""
import uuid
from datetime import date

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubMembership, ClubOfficer, Organization, User
from app.schemas.secretaria import OTHER_TITLE, OfficerOut
from app.security import utcnow
from app.services.audit import record_audit

OFFICER = "CLUB_OFFICER"
# The order a club reads its officers in (the public page and the roster pills).
TITLE_ORDER = (
    "DIRECTOR", "SUBDIRECTOR", "SECRETARIO", "TESORERO", "CAPELLAN", "CONSEJERO", "INSTRUCTOR",
    OTHER_TITLE,
)

NOT_FOUND = "Cargo no encontrado en este club"
DUPLICATE = "Esa persona ya tiene ese cargo en el club"
NOT_ACTIVE_MEMBER = "Sólo un miembro activo del club puede tener un cargo"
CUSTOM_ONLY_WITH_OTHER = "custom_title sólo se usa con el cargo OTRO"
OTHER_NEEDS_CUSTOM = "El cargo OTRO necesita un custom_title"
UNTIL_BEFORE_SINCE = "La fecha de fin no puede ser anterior al inicio del cargo"


def _today() -> date:
    return utcnow().date()


def is_active(officer: ClubOfficer, today: date | None = None) -> bool:
    return officer.until is None or officer.until > (today or _today())


def active_condition(today: date | None = None):
    """`is_active` as SQL."""
    return or_(ClubOfficer.until.is_(None), ClubOfficer.until > (today or _today()))


def label(title: str, custom_title: str | None) -> str:
    """The pill of the roster: the code, or the free text of OTRO."""
    return custom_title if title == OTHER_TITLE and custom_title else title


def as_out(officer: ClubOfficer, member: User) -> OfficerOut:
    return OfficerOut(
        id=str(officer.id),
        club_id=str(officer.club_id),
        membership_id=str(officer.membership_id),
        user_id=str(member.id),
        name=member.name,
        title=officer.title,
        custom_title=officer.custom_title,
        since=officer.since,
        until=officer.until,
        active=is_active(officer),
    )


def _rank(title: str) -> int:
    return TITLE_ORDER.index(title) if title in TITLE_ORDER else len(TITLE_ORDER)


# ----------------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------------
async def list_officers(
    db: AsyncSession, club_id: uuid.UUID, *, include_closed: bool = False
) -> list[tuple[ClubOfficer, User]]:
    stmt = (
        select(ClubOfficer, User)
        .join(ClubMembership, ClubMembership.id == ClubOfficer.membership_id)
        .join(User, User.id == ClubMembership.user_id)
        .where(ClubOfficer.club_id == club_id)
    )
    if not include_closed:
        stmt = stmt.where(active_condition(), ClubMembership.status == "ACTIVE")
    rows = (await db.execute(stmt)).all()
    return sorted(
        rows,
        key=lambda row: (
            not is_active(row[0]), _rank(row[0].title), row[1].name, row[0].since, str(row[0].id)
        ),
    )


async def titles_by_membership(
    db: AsyncSession, membership_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """The pills of a whole page of the roster, in ONE query."""
    out: dict[uuid.UUID, list[str]] = {}
    if not membership_ids:
        return out
    stmt = select(ClubOfficer.membership_id, ClubOfficer.title, ClubOfficer.custom_title).where(
        ClubOfficer.membership_id.in_(membership_ids), active_condition()
    )
    rows = sorted((await db.execute(stmt)).all(), key=lambda row: (_rank(row[1]), row[2] or ""))
    for membership_id, title, custom_title in rows:
        out.setdefault(membership_id, []).append(label(title, custom_title))
    return out


async def get_officer(db: AsyncSession, club: Organization, officer_id: uuid.UUID) -> ClubOfficer:
    stmt = select(ClubOfficer).where(ClubOfficer.id == officer_id, ClubOfficer.club_id == club.id)
    officer = (await db.execute(stmt)).scalar_one_or_none()
    if officer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return officer


async def _duplicate_exists(
    db: AsyncSession,
    membership_id: uuid.UUID,
    title: str,
    custom_title: str | None,
    *,
    except_id: uuid.UUID | None = None,
) -> bool:
    stmt = select(ClubOfficer.id).where(
        ClubOfficer.membership_id == membership_id,
        ClubOfficer.title == title,
        func.lower(func.coalesce(ClubOfficer.custom_title, "")) == (custom_title or "").lower(),
        active_condition(),
    )
    if except_id is not None:
        stmt = stmt.where(ClubOfficer.id != except_id)
    # Serializes two people naming the same cargo at once (the partial unique index is the
    # last word for open cargos).
    await db.execute(
        select(ClubMembership.id).where(ClubMembership.id == membership_id).with_for_update()
    )
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None


# ----------------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------------
async def create(
    db: AsyncSession,
    *,
    club: Organization,
    membership: ClubMembership,
    actor: User,
    title: str,
    custom_title: str | None,
    since: date | None,
    request: Request | None = None,
) -> tuple[ClubOfficer, User]:
    member = await db.get(User, membership.user_id)
    if member is None or membership.status != "ACTIVE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, NOT_ACTIVE_MEMBER)
    if await _duplicate_exists(db, membership.id, title, custom_title):
        raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE)
    officer = ClubOfficer(
        id=uuid.uuid4(),
        club_id=club.id,
        membership_id=membership.id,
        title=title,
        custom_title=custom_title,
        since=since or _today(),
        until=None,
        created_by_id=actor.id,
        created_at=utcnow(),
    )
    db.add(officer)
    record_audit(
        db,
        action="OFFICER_CREATE",
        entity_type=OFFICER,
        entity_id=officer.id,
        actor=actor,
        details=f"{label(title, custom_title)} of club {club.id}",
        metadata={
            "club_id": str(club.id),
            "membership_id": str(membership.id),
            "user_id": str(member.id),
            "title": title,
            "custom_title": custom_title,
            "since": officer.since.isoformat(),
        },
        request=request,
    )
    await db.flush()
    return officer, member


async def update(
    db: AsyncSession,
    officer: ClubOfficer,
    *,
    actor: User,
    changes: dict,
    request: Request | None = None,
) -> ClubOfficer:
    """`until` and `custom_title`, nothing else: a different cargo is another row."""
    if "custom_title" in changes:
        if officer.title != OTHER_TITLE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, CUSTOM_ONLY_WITH_OTHER)
        if changes["custom_title"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, OTHER_NEEDS_CUSTOM)
    if "until" in changes and changes["until"] is not None and changes["until"] < officer.since:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, UNTIL_BEFORE_SINCE)

    custom_title = changes.get("custom_title", officer.custom_title)
    until = changes.get("until", officer.until)
    reopens = until is None or until > _today()
    if reopens and await _duplicate_exists(
        db, officer.membership_id, officer.title, custom_title, except_id=officer.id
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE)

    previous = {"until": officer.until.isoformat() if officer.until else None,
                "custom_title": officer.custom_title}
    officer.custom_title = custom_title
    officer.until = until
    record_audit(
        db,
        action="OFFICER_UPDATE",
        entity_type=OFFICER,
        entity_id=officer.id,
        actor=actor,
        details=f"Updated fields: {', '.join(sorted(changes))}",
        metadata={
            "club_id": str(officer.club_id),
            "fields": sorted(changes),
            "from": previous,
            "to": {"until": until.isoformat() if until else None, "custom_title": custom_title},
        },
        request=request,
    )
    await db.flush()
    return officer


async def close(
    db: AsyncSession, officer: ClubOfficer, *, actor: User, request: Request | None = None
) -> ClubOfficer:
    """`until = today`; never a delete. Idempotent: a closed cargo answers the same."""
    today = _today()
    if not is_active(officer, today):
        return officer
    # A cargo that has not started yet closes on its first day (the CHECK wants until >= since).
    officer.until = max(today, officer.since)
    record_audit(
        db,
        action="OFFICER_CLOSE",
        entity_type=OFFICER,
        entity_id=officer.id,
        actor=actor,
        metadata={"club_id": str(officer.club_id), "title": officer.title,
                  "until": officer.until.isoformat()},
        request=request,
    )
    await db.flush()
    return officer
