"""Units of a club (Bloque E, E5).

A unit is a LIGHT table, not a node of the organization tree, and that is the
whole design decision: if a member hung from their unit, `users.organization_id`
would stop being the club and `can_review` of block A ("whose organization is
the member's current club") would break. A unit has no jurisdiction of its own,
`org-nodes` reads are public and units are named groups of minors, and they are
reshuffled every year — archiving a row is trivial, rewriting an ltree subtree
is not.

Two rules with opposite hardness (spec §5.4):
  * the CAPACITY is hard, taken under `SELECT … FOR UPDATE` so two people can
    never take the same last place. The director raises it in one touch;
  * the AGE BRACKET is a warning. Birthdays move people out of their bracket in
    the middle of the year; refusing would be wrong every September.

Everything here only *stages* its change on the caller's session (rows and
audit), so the router commits a change and its audit row together.
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubMembership, ClubUnit, Organization, User
from app.people import age_in_years, is_minor_user
from app.schemas.unit import MemberUnitOut, PersonRef, UnitOut, UnitRef
from app.security import CLUB_DIRECTOR, CLUB_SECRETARY, COUNSELOR, INSTRUCTOR
from app.security import utcnow
from app.services.audit import record_audit

ACTIVE = "active"
ARCHIVED = "archived"
UNIT = "UNIT"

# Who may be put in charge of a unit: an adult with an ACTIVE membership of the
# same club and a role that carries responsibility. A STUDENT never leads a
# group of minors; the director grants COUNSELOR first (decision D7).
COUNSELOR_ROLES = (COUNSELOR, INSTRUCTOR, CLUB_SECRETARY, CLUB_DIRECTOR)

UNIT_NOT_FOUND = "Unidad no encontrada en este club"
UNIT_FULL = "Unidad llena: sube el cupo o elige otra unidad."
DUPLICATE_NAME = "Ya existe una unidad activa con ese nombre en el club."


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------
async def get_unit(
    db: AsyncSession, club: Organization, unit_id: uuid.UUID, *, lock: bool = False
) -> ClubUnit:
    """404 for a unit of another club: an outsider learns nothing."""
    stmt = select(ClubUnit).where(ClubUnit.id == unit_id, ClubUnit.club_id == club.id)
    if lock:
        # Serializes two people taking the last place of the same unit.
        stmt = stmt.with_for_update()
    unit = (await db.execute(stmt)).scalar_one_or_none()
    if unit is None or unit.status != ACTIVE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNIT_NOT_FOUND)
    return unit


async def active_units(db: AsyncSession, club_id: uuid.UUID) -> list[ClubUnit]:
    stmt = (
        select(ClubUnit)
        .where(ClubUnit.club_id == club_id, ClubUnit.status == ACTIVE)
        .order_by(ClubUnit.name, ClubUnit.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def member_counts(db: AsyncSession, club_id: uuid.UUID) -> dict[uuid.UUID, int]:
    stmt = (
        select(ClubMembership.unit_id, func.count())
        .where(
            ClubMembership.club_id == club_id,
            ClubMembership.status == "ACTIVE",
            ClubMembership.unit_id.is_not(None),
        )
        .group_by(ClubMembership.unit_id)
    )
    return {unit_id: count for unit_id, count in (await db.execute(stmt)).all()}


async def count_members(db: AsyncSession, unit_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(
        ClubMembership.unit_id == unit_id, ClubMembership.status == "ACTIVE"
    )
    return (await db.execute(stmt)).scalar_one()


async def counselor_unit_ids(db: AsyncSession, club_id: uuid.UUID, user_id: uuid.UUID) -> list[uuid.UUID]:
    """The units this person leads. A person may lead more than one unit; a unit
    has one counselor."""
    stmt = select(ClubUnit.id).where(
        ClubUnit.club_id == club_id,
        ClubUnit.counselor_id == user_id,
        ClubUnit.status == ACTIVE,
    )
    return list((await db.execute(stmt)).scalars().all())


async def units_by_id(db: AsyncSession, club_id: uuid.UUID) -> dict[uuid.UUID, ClubUnit]:
    stmt = select(ClubUnit).where(ClubUnit.club_id == club_id)
    return {row.id: row for row in (await db.execute(stmt)).scalars().all()}


async def membership_context(
    db: AsyncSession, membership: ClubMembership | None
) -> tuple[ClubUnit | None, User | None]:
    """The unit of a membership and whoever leads it, for `GET /memberships/me`
    and for the answer to accepting an invitation."""
    if membership is None or membership.unit_id is None:
        return None, None
    unit = await db.get(ClubUnit, membership.unit_id)
    if unit is None or unit.counselor_id is None:
        return unit, None
    return unit, await db.get(User, unit.counselor_id)


def unit_ref(unit: ClubUnit | None) -> UnitRef | None:
    return UnitRef(id=str(unit.id), name=unit.name) if unit is not None else None


async def as_out(db: AsyncSession, unit: ClubUnit, *, members: int | None = None) -> UnitOut:
    counselor = await db.get(User, unit.counselor_id) if unit.counselor_id else None
    return UnitOut(
        id=str(unit.id),
        club_id=str(unit.club_id),
        name=unit.name,
        min_age=unit.min_age,
        max_age=unit.max_age,
        capacity=unit.capacity,
        members=await count_members(db, unit.id) if members is None else members,
        counselor=(
            PersonRef(id=str(counselor.id), name=counselor.name) if counselor is not None else None
        ),
        status=unit.status,
    )


# ----------------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------------
async def _name_is_free(
    db: AsyncSession, club_id: uuid.UUID, name: str, *, except_id: uuid.UUID | None = None
) -> bool:
    stmt = select(ClubUnit.id).where(
        ClubUnit.club_id == club_id,
        ClubUnit.status == ACTIVE,
        func.lower(ClubUnit.name) == name.lower(),
    )
    if except_id is not None:
        stmt = stmt.where(ClubUnit.id != except_id)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is None


async def create(
    db: AsyncSession,
    *,
    club: Organization,
    actor: User,
    name: str,
    min_age: int | None,
    max_age: int | None,
    capacity: int | None,
    request: Request | None = None,
) -> ClubUnit:
    name = " ".join(name.split())
    if not await _name_is_free(db, club.id, name):
        raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE_NAME)
    now = utcnow()
    unit = ClubUnit(
        id=uuid.uuid4(),
        club_id=club.id,
        name=name,
        min_age=min_age,
        max_age=max_age,
        capacity=capacity,
        status=ACTIVE,
        created_at=now,
        updated_at=now,
    )
    db.add(unit)
    await db.flush()
    record_audit(
        db,
        action="UNIT_CREATE",
        entity_type=UNIT,
        entity_id=unit.id,
        actor=actor,
        details=f"Unit {unit.name} of club {club.id}",
        metadata={"club_id": str(club.id), "capacity": capacity},
        request=request,
    )
    return unit


async def update(
    db: AsyncSession,
    unit: ClubUnit,
    *,
    actor: User,
    changes: dict,
    request: Request | None = None,
) -> ClubUnit:
    if "name" in changes:
        if changes["name"] is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "El nombre no puede quedar vacío")
        changes["name"] = " ".join(changes["name"].split())
        if not await _name_is_free(db, unit.club_id, changes["name"], except_id=unit.id):
            raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE_NAME)
    if changes.get("capacity") is not None:
        current = await count_members(db, unit.id)
        if changes["capacity"] < current:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"La unidad ya tiene {current} miembros: mueve a alguien antes de bajar el cupo.",
            )
    # `min_age`/`max_age` are checked together, whichever of the two arrived.
    low = changes.get("min_age", unit.min_age)
    high = changes.get("max_age", unit.max_age)
    if low is not None and high is not None and low > high:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "La edad mínima no puede superar la máxima"
        )

    for column in ("name", "min_age", "max_age", "capacity"):
        if column in changes:
            setattr(unit, column, changes[column])
    unit.updated_at = utcnow()
    record_audit(
        db,
        action="UNIT_UPDATE",
        entity_type=UNIT,
        entity_id=unit.id,
        actor=actor,
        details=f"Updated fields: {', '.join(sorted(changes))}",
        metadata={"club_id": str(unit.club_id), "fields": sorted(changes)},
        request=request,
    )
    return unit


async def archive(
    db: AsyncSession, unit: ClubUnit, *, actor: User, request: Request | None = None
) -> ClubUnit:
    """Nothing is deleted: the year's history stays and the name is freed."""
    remaining = await count_members(db, unit.id)
    if remaining:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"La unidad todavía tiene {remaining} miembros: muévelos antes de archivarla.",
        )
    unit.status = ARCHIVED
    unit.counselor_id = None
    unit.updated_at = utcnow()
    record_audit(
        db,
        action="UNIT_ARCHIVE",
        entity_type=UNIT,
        entity_id=unit.id,
        actor=actor,
        metadata={"club_id": str(unit.club_id)},
        request=request,
    )
    return unit


async def set_counselor(
    db: AsyncSession,
    unit: ClubUnit,
    *,
    membership: ClubMembership | None,
    actor: User,
    request: Request | None = None,
) -> ClubUnit:
    """Put somebody in charge of a unit, or take the post away.

    Whoever leads a unit is alone with minors, so the bar is deliberately high:
    an adult, with an ACTIVE membership of THIS club, holding a role that
    carries responsibility and — once E7's switch is on — a church letter in
    force (`rbac.may_handle_minors`, the single gate).
    """
    from app.rbac import may_handle_minors  # local: rbac must not import services

    person: User | None = None
    if membership is not None:
        person = await db.get(User, membership.user_id)
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada en este club")
        if membership.status != "ACTIVE":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Sólo un miembro activo del club puede ser consejero de una unidad.",
            )
        if is_minor_user(person):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Un menor de edad no puede ser consejero de una unidad.",
            )
        if membership.role not in COUNSELOR_ROLES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Concede primero el rol de consejero(a), instructor(a) o secretaría a esta persona.",
            )
        if not may_handle_minors(person):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Sin verificar: falta la carta de la iglesia para hacerse cargo de una unidad.",
            )

    unit.counselor_id = person.id if person is not None else None
    unit.updated_at = utcnow()
    record_audit(
        db,
        action="UNIT_COUNSELOR",
        entity_type=UNIT,
        entity_id=unit.id,
        actor=actor,
        details=f"Counselor of {unit.name}: {person.id if person else 'none'}",
        metadata={
            "club_id": str(unit.club_id),
            "counselor_id": str(person.id) if person is not None else None,
        },
        request=request,
    )
    return unit


def age_warning(unit: ClubUnit | None, member: User) -> bool:
    """A warning, never a refusal (spec §5.4). Unknown age never warns."""
    if unit is None:
        return False
    age = age_in_years(member.birth_date)
    if age is None:
        return False
    if unit.min_age is not None and age < unit.min_age:
        return True
    return unit.max_age is not None and age > unit.max_age


async def assign_member(
    db: AsyncSession,
    *,
    club: Organization,
    membership: ClubMembership,
    unit_id: uuid.UUID | None,
    actor: User,
    request: Request | None = None,
) -> MemberUnitOut:
    """Put a member in a unit, move them, or take them out (`unit_id = null`)."""
    member = await db.get(User, membership.user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada en este club")
    if membership.status != "ACTIVE":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Sólo un miembro activo del club entra en una unidad."
        )

    unit: ClubUnit | None = None
    if unit_id is not None:
        unit = await _unit_of_this_club(db, club, unit_id, lock=True)
        if membership.unit_id != unit.id and not _has_room(unit, await count_members(db, unit.id)):
            raise HTTPException(status.HTTP_409_CONFLICT, UNIT_FULL)

    previous = membership.unit_id
    membership.unit_id = unit.id if unit is not None else None
    membership.updated_at = utcnow()
    record_audit(
        db,
        action="UNIT_ASSIGN",
        entity_type="MEMBERSHIP",
        entity_id=membership.id,
        actor=actor,
        details=f"Unit {previous} -> {membership.unit_id}",
        metadata={
            "club_id": str(club.id),
            "from": str(previous) if previous else None,
            "to": str(membership.unit_id) if membership.unit_id else None,
        },
        request=request,
    )
    return MemberUnitOut(
        membership_id=str(membership.id),
        unit_id=str(unit.id) if unit is not None else None,
        unit=unit_ref(unit),
        age_warning=age_warning(unit, member),
    )


async def _unit_of_this_club(
    db: AsyncSession, club: Organization, unit_id: uuid.UUID, *, lock: bool = False
) -> ClubUnit:
    """A unit and a member always belong to the same club (rule 5). A unit of
    somebody else's club is a 400, not a 404: the caller named it themselves."""
    stmt = select(ClubUnit).where(ClubUnit.id == unit_id)
    if lock:
        stmt = stmt.with_for_update()
    unit = (await db.execute(stmt)).scalar_one_or_none()
    if unit is None or unit.club_id != club.id or unit.status != ACTIVE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Esa unidad no es de este club o ya está archivada."
        )
    return unit


def _has_room(unit: ClubUnit, members: int) -> bool:
    return unit.capacity is None or members < unit.capacity


async def place_on_activation(
    db: AsyncSession, membership: ClubMembership, unit_id: uuid.UUID | None
) -> bool:
    """An invitation may carry a unit: the membership enters it when it becomes
    ACTIVE, IF there is room. A full unit never stops somebody joining the club
    — they land without a unit and the club is told (spec §5.4)."""
    if unit_id is None or membership.unit_id is not None:
        return False
    stmt = select(ClubUnit).where(ClubUnit.id == unit_id).with_for_update()
    unit = (await db.execute(stmt)).scalar_one_or_none()
    if unit is None or unit.club_id != membership.club_id or unit.status != ACTIVE:
        return False
    if not _has_room(unit, await count_members(db, unit.id)):
        return False
    membership.unit_id = unit.id
    return True


async def detach_member(db: AsyncSession, membership: ClubMembership) -> None:
    """Leaving the club leaves the unit too."""
    membership.unit_id = None


async def release_counselor_posts(db: AsyncSession, club_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Somebody who is no longer in the club leads none of its units."""
    stmt = select(ClubUnit).where(ClubUnit.club_id == club_id, ClubUnit.counselor_id == user_id)
    for unit in (await db.execute(stmt)).scalars().all():
        unit.counselor_id = None
        unit.updated_at = utcnow()


async def unit_of_club_or_400(
    db: AsyncSession, club: Organization, unit_id: uuid.UUID | None
) -> ClubUnit | None:
    """Used when a unit id arrives on something else (an invitation)."""
    if unit_id is None:
        return None
    return await _unit_of_this_club(db, club, unit_id)
