"""Public profile (Bloque G): assembly, visibility, handle and profile edits.

Spec: docs/superpowers/specs/2026-09-23-perfil-publico.md.

The profile is DERIVED: club and association from the organization tree, the current class
and the Master Guide status from the program enrollments (block F), honors from the
certificates that are not annulled, and XP, level and badges from the same rows
(app/services/xp.py). A handful of aggregate queries, whatever the size of the portfolio.

Who sees what is `rbac.profile_access`; everything that is not allowed is a 404.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import violated_constraint
from app.models import Guardianship, User
from app.rbac import CONSENT_GRANTED, profile_access, profile_is_minor, visible_avatar
from app.schemas.profile import (
    ConductBar,
    HonorEarned,
    HonorInProgress,
    MyProfile,
    ProfileAssociation,
    ProfileBadge,
    ProfileClass,
    ProfileClub,
    ProfileMasterGuide,
    ProfileStats,
    ProfileUpdate,
    ProfileWithConduct,
    ProfileXp,
    PublicProfile,
)
from app.security import utcnow
from app.services import xp
from app.services.audit import record_audit

USER_PROFILE = "USER_PROFILE"
NOT_FOUND = "profile_not_found"

HANDLE_PATTERN = re.compile(r"^[a-z0-9_.]{3,32}$")
HANDLE_LOCK = timedelta(days=30)
# Same list as users_default_handle() in migrations/014_profiles.sql. Compared without
# `.` and `_`, so `ad.min` or `master_gc` are reserved too.
RESERVED_HANDLES = frozenset({
    "admin", "administrator", "administrador", "adventist", "adventistclub", "conquistadores",
    "conquistador", "aventureros", "guiasmayores", "guiamayor", "mastergc", "master", "root",
    "system", "sistema", "staff", "soporte", "support", "help", "ayuda", "official", "oficial",
    "moderator", "moderador", "director", "clubdirector", "secretary", "clubsecretary",
    "secretario", "instructor", "counselor", "consejero", "student", "estudiante", "guardian",
    "parentguardian", "tutor", "coordinator", "coordinatorzone", "coordinador", "adminassociation",
    "admindivision", "adminunion", "me", "api", "www", "mail", "null", "undefined", "profile",
    "profiles", "perfil", "settings", "login", "logout", "register", "signup", "user", "users",
    "club", "clubs", "verify", "u",
})
RESERVED_PREFIXES = ("admin", "adventist", "conquistador", "mastergc")

AVATARS_FOLDER, COVERS_FOLDER = "avatars", "covers"
MAX_MEDIA_URL = 600

HONORS_IN_PROGRESS = ("IN_PROGRESS", "READY")
BADGE_THRESHOLDS = (
    (1, "primera-especialidad", "Primera especialidad", "Obtuvo su primera especialidad."),
    (5, "cinco-especialidades", "Cinco especialidades", "Obtuvo cinco especialidades."),
    (10, "diez-especialidades", "Diez especialidades", "Obtuvo diez especialidades."),
)
SERVICE_HOURS_BADGE = 100


# ----------------------------------------------------------------------------
# Handle
# ----------------------------------------------------------------------------
def normalize_handle(value: str) -> str:
    return value.strip().lstrip("@").lower()


def handle_problem(handle: str) -> str | None:
    """The short code that refuses `handle`, or None when it is acceptable."""
    if not HANDLE_PATTERN.match(handle):
        return "handle_invalid"
    bare = handle.replace(".", "").replace("_", "")
    if bare in RESERVED_HANDLES or bare.startswith(RESERVED_PREFIXES):
        return "handle_reserved"
    return None


def handle_locked_until(user: User) -> datetime | None:
    if user.handle_changed_at is None:
        return None
    until = user.handle_changed_at + HANDLE_LOCK
    return until if until > utcnow() else None


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------
async def find_user(db: AsyncSession, handle_or_id: str) -> User | None:
    try:
        return await db.get(User, uuid.UUID(handle_or_id))
    except ValueError:
        pass
    handle = normalize_handle(handle_or_id)
    if not HANDLE_PATTERN.match(handle):
        return None
    return (await db.execute(select(User).where(User.handle == handle))).scalar_one_or_none()


@dataclass(frozen=True)
class ClubInfo:
    id: uuid.UUID
    name: str
    city: str | None


async def _organizations(
    db: AsyncSession, organization_id: uuid.UUID | None
) -> tuple[ClubInfo | None, dict | None]:
    """(the member's active club or None, their association {id, name} or None) in ONE
    query: the organization and its association ancestor (itself, for an association)."""
    if organization_id is None:
        return None, None
    row = (
        await db.execute(
            text(
                """
                SELECT o.id, o.type, o.status, o.name, o.city,
                       a.id AS association_id, a.name AS association_name
                FROM organizations o
                LEFT JOIN organizations a ON a.type = 'association' AND a.path @> o.path
                WHERE o.id = :id
                ORDER BY nlevel(a.path) DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"id": organization_id},
        )
    ).mappings().first()
    if row is None:
        return None, None
    club = (
        ClubInfo(id=row["id"], name=row["name"], city=row["city"])
        if row["type"] == "club" and row["status"] == "active"
        else None
    )
    association = (
        {"id": row["association_id"], "name": row["association_name"]}
        if row["association_id"] is not None
        else None
    )
    return club, association


async def guardianships_of(db: AsyncSession, child_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    stmt = select(Guardianship.guardian_id, Guardianship.consent_status).where(
        Guardianship.child_id == child_id
    )
    return [(row[0], row[1]) for row in (await db.execute(stmt)).all()]


# ----------------------------------------------------------------------------
# Assembly
# ----------------------------------------------------------------------------
def _pct(complete: int, total: int) -> int:
    return int(complete * 100 // total) if total else 0


def _verify_url(certificate_no: str) -> str:
    return f"{settings.PUBLIC_WEB_URL.rstrip('/')}/verify/{certificate_no}"


def _badge_image(key: str) -> str:
    return f"{settings.R2_PUBLIC_URL.rstrip('/')}/badges/{key.replace(':', '-')}.png"


def _current_class(enrollments: list, certificates: list) -> ProfileClass | None:
    """The class being worked on (the most recently touched), else the last one invested."""
    for row in enrollments:
        if (
            row["program_kind"] == "CLASS"
            and not xp.is_master_guide(row["program_slug"])
            and row["status"] in HONORS_IN_PROGRESS
        ):
            return ProfileClass(
                program_id=str(row["program_id"]),
                name=row["program_name"],
                image_url=row["program_image"],
                progress_pct=_pct(row["complete"], row["total"]),
            )
    for row in certificates:
        if row["program_kind"] == "CLASS" and not xp.is_master_guide(row["program_slug"]):
            return ProfileClass(
                program_id=str(row["program_id"]),
                name=row["program_name"] or row["honor_name_snapshot"],
                image_url=row["program_image"],
                progress_pct=100,
            )
    return None


def _master_guide(enrollments: list, certificates: list) -> ProfileMasterGuide:
    if any(xp.is_master_guide(row["program_slug"]) for row in certificates):
        return ProfileMasterGuide(status="invested", progress_pct=100)
    for row in enrollments:
        if xp.is_master_guide(row["program_slug"]) and row["status"] in HONORS_IN_PROGRESS:
            return ProfileMasterGuide(
                status="in_progress", progress_pct=_pct(row["complete"], row["total"])
            )
    return ProfileMasterGuide(status="none", progress_pct=0)


async def _complete_categories(db: AsyncSession, user_id: uuid.UUID) -> list:
    """Categories whose every PUBLISHED honor the member holds (any version of its lineage:
    a certificate of version 1 covers version 2)."""
    rows = await db.execute(
        text(
            """
            WITH RECURSIVE earned(id) AS (
              SELECT DISTINCT c.honor_id FROM certificates c
              WHERE c.user_id = :user_id AND c.honor_id IS NOT NULL
                AND c.revoked_at IS NULL AND c.status <> :revoked
              UNION
              SELECT h.id FROM honors h JOIN earned e ON h.previous_version_id = e.id
            )
            SELECT cat.id, cat.slug, cat.name
            FROM honor_categories cat
            JOIN honors h ON h.category_id = cat.id AND h.status = 'PUBLISHED'
            WHERE cat.id IN (SELECT category_id FROM honors
                             WHERE id IN (SELECT id FROM earned) AND category_id IS NOT NULL)
            GROUP BY cat.id, cat.slug, cat.name
            HAVING bool_and(h.id IN (SELECT id FROM earned))
            """
        ),
        {"user_id": user_id, "revoked": xp.REVOKED_STATUS},
    )
    return list(rows.mappings().all())


def _badges(
    user: User, certificates: list, activity: list, categories: list
) -> list[ProfileBadge]:
    badges: list[ProfileBadge] = []
    honor_certs = sorted(
        (row for row in certificates if row["honor_id"] is not None),
        key=lambda row: row["issued_date"],
    )
    for count, key, name, description in BADGE_THRESHOLDS:
        if len(honor_certs) >= count:
            badges.append(ProfileBadge(
                key=key, name=name, description=description, image_url=_badge_image(key),
                earned_at=honor_certs[count - 1]["issued_date"],
            ))
    for category in categories:
        key = f"categoria-completa:{category['slug']}"
        dates = [row["issued_date"] for row in honor_certs if row["category_id"] == category["id"]]
        badges.append(ProfileBadge(
            key=key, name=f"Categoría completa: {category['name']}",
            description=f"Obtuvo todas las especialidades de {category['name']}.",
            image_url=_badge_image(key), earned_at=max(dates) if dates else None,
        ))
    seen: set[str] = set()
    for row in sorted(certificates, key=lambda row: row["issued_date"]):
        if row["program_id"] is None:
            continue
        if xp.is_master_guide(row["program_slug"]):
            key, name = "guia-mayor", "Guía Mayor"
            description, image = "Investido como Guía Mayor.", _badge_image("guia-mayor")
        elif row["program_kind"] == "CLASS":
            key = f"clase-{xp.base_slug(row['program_slug'])}"
            name = row["program_name"]
            description = f"Investido en la clase {row['program_name']}."
            image = row["program_image"] or _badge_image(key)
        else:
            continue
        if key not in seen:
            seen.add(key)
            badges.append(ProfileBadge(
                key=key, name=name, description=description, image_url=image,
                earned_at=row["issued_date"],
            ))
    reached = None
    cumulative = 0.0
    for row in activity:
        if row["category"] == "SERVICE":
            cumulative += float(row["quantity"])
            if cumulative >= SERVICE_HOURS_BADGE:
                reached = row["performed_on"]
                break
    if reached is not None:
        key = "100-horas-servicio"
        badges.append(ProfileBadge(
            key=key, name="100 horas de servicio",
            description="Sumó 100 horas de servicio aprobadas.",
            image_url=_badge_image(key), earned_at=reached,
        ))
    courses = [row["issued_date"] for row in honor_certs if row["enrollment_mode"] == "COURSE"]
    if courses:
        key = "curso-en-linea"
        badges.append(ProfileBadge(
            key=key, name="Curso en línea", description="Aprobó su primer curso virtual.",
            image_url=_badge_image(key), earned_at=min(courses),
        ))
    launch = settings.PUBLIC_LAUNCH_DATE
    if launch is not None and user.created_at is not None and user.created_at.date() < launch:
        key = "fundador"
        badges.append(ProfileBadge(
            key=key, name="Fundador",
            description="Tenía cuenta antes del lanzamiento público.",
            image_url=_badge_image(key), earned_at=user.created_at.date(),
        ))
    return badges


async def build_profile(
    db: AsyncSession, viewer: User | None, target: User, *, as_owner: bool = False
) -> PublicProfile | ProfileWithConduct | MyProfile:
    """The profile of `target` as `viewer` may see it; 404 when they may not see it."""
    club, association = await _organizations(db, target.organization_id)
    guardianships = await guardianships_of(db, target.id)
    is_minor = profile_is_minor(target, has_guardian=bool(guardianships))
    viewer_is_guardian = viewer is not None and any(
        guardian_id == viewer.id and consent == CONSENT_GRANTED
        for guardian_id, consent in guardianships
    )
    visible, privileged = await profile_access(
        db, viewer, target, target_club=club, is_minor=is_minor,
        viewer_is_guardian=viewer_is_guardian,
    )
    if not visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)

    enrollments = await xp.fetch_enrollments(db, target.id)
    certificates = await xp.fetch_certificates(db, target.id)
    activity = await xp.fetch_activity(db, target.id)
    awards_net, conduct_points = await xp.fetch_award_totals(db, target.id)
    breakdown = xp.cached(target.id)
    if breakdown is None:
        breakdown = xp.breakdown(xp.facts_from(enrollments, certificates, activity, awards_net))
        xp.prime(target.id, breakdown)
    has_honors = any(row["honor_id"] is not None for row in certificates)
    categories = await _complete_categories(db, target.id) if has_honors else []

    total = breakdown.total
    level = xp.level_for(total)
    service_hours = sum(float(r["quantity"]) for r in activity if r["category"] == "SERVICE")
    attendance = sum(float(r["quantity"]) for r in activity if r["category"] == "ATTENDANCE")
    honors_in_progress = [
        HonorInProgress(
            honor_id=str(row["honor_id"]),
            name=row["honor_name"],
            image_url=row["honor_image"],
            progress_pct=_pct(row["complete"], row["total"]),
        )
        for row in enrollments
        if row["honor_id"] is not None and row["status"] in HONORS_IN_PROGRESS
    ]
    honors_earned = [
        HonorEarned(
            honor_id=str(row["honor_id"]),
            name=row["honor_name"] or row["honor_name_snapshot"],
            image_url=row["honor_image"],
            certificate_no=row["certificate_no"],
            issued_date=row["issued_date"],
            verify_url=_verify_url(row["certificate_no"]),
        )
        for row in certificates
        if row["honor_id"] is not None
    ]
    is_me = viewer is not None and viewer.id == target.id
    fields = dict(
        id=str(target.id),
        handle=target.handle,
        name=target.name,
        avatar_url=visible_avatar(target, is_minor=is_minor),
        cover_url=None if is_minor else target.cover_url,
        bio=target.bio,
        club=ProfileClub(id=str(club.id), name=club.name, city=club.city) if club else None,
        association=(
            ProfileAssociation(id=str(association["id"]), name=association["name"])
            if association
            else None
        ),
        class_=_current_class(enrollments, certificates),
        master_guide=_master_guide(enrollments, certificates),
        stats=ProfileStats(
            honors_earned=len(honors_earned),
            honors_in_progress=len(honors_in_progress),
            service_hours=round(service_hours, 1),
            attendance=int(attendance),
        ),
        xp=ProfileXp(
            total=total if privileged else None,
            level=level.level,
            level_name=level.name,
            next_level_at=level.next_level_at,
        ),
        badges=_badges(target, certificates, activity, categories),
        honors_earned=honors_earned,
        honors_in_progress=honors_in_progress,
        visibility=target.profile_visibility,
        is_me=is_me,
        can_edit=is_me,
    )
    if not privileged:
        return PublicProfile(**fields)
    # Spec §4.1: the bar is for the member, their guardians and the club's staff (and the
    # hierarchy above) — never for the public nor for `club`-visibility peers.
    conduct = conduct_bar(xp.conduct_score(conduct_points))
    if not as_owner:
        return ProfileWithConduct(**fields, conduct=conduct)
    return MyProfile(
        **fields,
        is_minor=is_minor,
        guardian_allows_avatar=target.guardian_allows_avatar,
        handle_changed_at=target.handle_changed_at,
        handle_locked_until=handle_locked_until(target),
        conduct=conduct,
    )


def conduct_bar(score: int) -> ConductBar:
    return ConductBar(score=score, start=xp.CONDUCT_START, window_weeks=xp.CONDUCT_WEEKS)


async def profile_for(
    db: AsyncSession, viewer: User | None, handle_or_id: str
) -> PublicProfile | ProfileWithConduct:
    target = await find_user(db, handle_or_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return await build_profile(db, viewer, target)


# ----------------------------------------------------------------------------
# Editing
# ----------------------------------------------------------------------------
def _check_media_url(value: str, folder: str, code: str) -> str:
    """Only the platform's public bucket, and only its `avatars/` or `covers/` folder."""
    value = value.strip()
    prefix = f"{settings.R2_PUBLIC_URL.rstrip('/')}/{folder}/"
    if (
        not value.startswith(prefix)
        or len(value) > MAX_MEDIA_URL
        or len(value) == len(prefix)
        or ".." in value[len(prefix):]
        or any(char.isspace() for char in value)
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, code)
    return value


async def update_profile(
    db: AsyncSession, actor: User, payload: ProfileUpdate, request: Request | None
) -> MyProfile:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_changes")
    for field in ("name", "handle", "profile_visibility"):
        if field in changes and changes[field] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field}_required")

    guardianships = await guardianships_of(db, actor.id)
    is_minor = profile_is_minor(actor, has_guardian=bool(guardianships))
    applied: dict[str, object] = {}

    if "name" in changes:
        name = changes["name"].strip()
        if not name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name_required")
        applied["name"] = name
    if "bio" in changes:
        applied["bio"] = (changes["bio"] or "").strip() or None
    if "profile_visibility" in changes:
        if is_minor and changes["profile_visibility"] == "public":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "minor_cannot_be_public")
        applied["profile_visibility"] = changes["profile_visibility"]
    if "avatar_url" in changes:
        value = changes["avatar_url"]
        if value is not None:
            if is_minor and not actor.guardian_allows_avatar:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "minor_avatar_not_allowed")
            value = _check_media_url(value, AVATARS_FOLDER, "invalid_avatar_url")
        applied["avatar_url"] = value
    if "cover_url" in changes:
        value = changes["cover_url"]
        if value is not None:
            if is_minor:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "minor_cannot_have_cover")
            value = _check_media_url(value, COVERS_FOLDER, "invalid_cover_url")
        applied["cover_url"] = value

    old_handle = actor.handle
    if "handle" in changes:
        handle = normalize_handle(changes["handle"])
        if handle != actor.handle:
            problem = handle_problem(handle)
            if problem:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
            if handle_locked_until(actor) is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "handle_locked")
            taken = select(User.id).where(User.handle == handle, User.id != actor.id)
            if (await db.execute(taken)).scalar_one_or_none() is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "handle_taken")
            applied["handle"] = handle
            applied["handle_changed_at"] = utcnow()

    if not applied:
        # Nothing actually changes (e.g. the same handle again): nothing to write or audit.
        return await build_profile(db, actor, actor, as_owner=True)
    for field, value in applied.items():
        setattr(actor, field, value)
    fields = sorted(set(applied) - {"handle_changed_at"})
    metadata: dict = {"fields": fields}
    if "handle" in applied:
        metadata["handle"] = {"from": old_handle, "to": applied["handle"]}
    if "profile_visibility" in applied:
        metadata["profile_visibility"] = applied["profile_visibility"]
    record_audit(
        db,
        action="UPDATE",
        entity_type=USER_PROFILE,
        entity_id=actor.id,
        actor=actor,
        details=f"Updated profile fields: {', '.join(fields) or '-'}",
        metadata=metadata,
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) == "users_handle_key":
            raise HTTPException(status.HTTP_409_CONFLICT, "handle_taken")
        raise
    await db.refresh(actor)
    return await build_profile(db, actor, actor, as_owner=True)
