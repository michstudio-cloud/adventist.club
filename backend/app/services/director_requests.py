"""«Pedir ser director de este club»: an EXISTING club, chosen at sign-up, and the
administration that approves it — plus the e-mail that tells that administration.

The sign-up form of «Director(a) de club» now asks for the association FIRST and then
searches its active clubs (`search_clubs`, public: only name, church and city). Choosing one
creates no club: the account is an adult INSTRUCTOR and the club gets a pending director
REQUEST — the same storage as the event's «Mi club ya está en el evento»
(`event_onboarding.request_director`): a CLUB_DIRECTOR `OrgInvitation` whose `on_accept`
says `request: true`, whose token is never shown and that sends no e-mail. No migration.

Who approves: the association's administration (ADMIN_ASSOCIATION or higher whose scope
reaches the club, `event_access.can_manage_events_of`) — the rank the event flow asks for.
«Aprobar» follows the event rule: an account that proved its address (VERIFIED) is appointed
now; otherwise the invitation is e-mailed, so only that mailbox can finish it.

The list (`pending`) shows EVERY pending request in the admin's scope, the event's ones too
(with the event's name), so the administration has one place for them; the event's Clubes
screen keeps working as before.

`queue_admin_notice` is the e-mail «hay una solicitud» to the association's administrators
(ADMIN_ASSOCIATION on the club's association, principal role or scoped assignment). It runs in
`BackgroundTasks` after the commit, in its own session, and never fails anything.

Nothing here commits, except the background task.
"""
from __future__ import annotations

import logging
import unicodedata
import uuid
from datetime import timedelta

from fastapi import BackgroundTasks, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Event, OrgInvitation, Organization, RoleAssignment, User
from app.security import (
    ADMIN_ASSOCIATION,
    ADMIN_ROLES,
    CLUB_DIRECTOR,
    generate_url_token,
    sha256_hex,
    utcnow,
)
from app.services import org_invitations
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

# `on_accept.type` of a request made from the sign-up for an existing club.
REQUEST_TYPE = "club_director_request"
# How long a request waits for «Aprobar» (the longest an invitation may live).
REQUEST_DAYS = org_invitations.MAX_DAYS
SEARCH_LIMIT = 8

# Sources of a request, as the list and the e-mail name them.
SOURCE_SIGNUP = "signup"
SOURCE_EVENT = "event"

# notification_log kinds (String(40)).
NOTICE_DIRECTOR_REQUEST = "ADMIN_DIRECTOR_REQUEST"
NOTICE_CLUB_REQUEST = "ADMIN_CLUB_REQUEST"

# Audit actions (String(40)).
AUDIT_REQUEST = "CLUB_DIRECTOR_REQUEST"
AUDIT_APPROVE = "CLUB_DIRECTOR_REQUEST_APPROVE"
AUDIT_REJECT = "CLUB_DIRECTOR_REQUEST_REJECT"

CLUB_UNAVAILABLE = "Ese club no está disponible. Elige otro o registra un club nuevo."
HAS_DIRECTOR = "Ese club ya tiene director. Si eres tú, inicia sesión con tu correo."
OTHER_PENDING = ("Ese club ya tiene una solicitud de director pendiente con otro correo. "
                 "Pide ayuda a la administración de tu asociación.")
NO_ASSOCIATION = "Ese club no tiene asociación que apruebe la solicitud. Pide ayuda a tu asociación."
MINOR = "Los menores de edad no pueden dirigir un club."
NOT_FOUND = "La solicitud ya no está pendiente."
FORBIDDEN = "Sólo la administración de la asociación del club aprueba a su director."
NOT_ELIGIBLE = "Esa cuenta no puede quedar como director."
LEADS_ANOTHER = "Esa persona ya dirige otro club."


def _fold(text: str | None) -> str:
    stripped = unicodedata.normalize("NFKD", " ".join((text or "").split()))
    return "".join(ch for ch in stripped if not unicodedata.combining(ch)).lower()


def _normalize(email: str | None) -> str:
    return (email or "").strip().lower()


def admin_requests_url() -> str:
    """Where the administration approves these requests (admin.adventist.club)."""
    return f"{settings.admin_url}/admin/directores"


def admin_pending_clubs_url() -> str:
    return f"{settings.admin_url}/admin/clubes?vista=por-aprobar"


# ----------------------------------------------------------------------------
# The tree
# ----------------------------------------------------------------------------
async def association_of(db: AsyncSession, club: Organization) -> Organization | None:
    """The association above the club, by `path` (a club may hang from its church or zone)."""
    if not club.path:
        return None
    return (
        await db.execute(
            select(Organization)
            .where(Organization.type == "association", Organization.path.op("@>")(club.path))
            .limit(1)
        )
    ).scalar_one_or_none()


async def may_decide(db: AsyncSession, actor: User, club: Organization) -> bool:
    """The association's administration (or higher) whose scope reaches the club; MASTER_GC."""
    from app.services.event_access import can_manage_events_of

    return await can_manage_events_of(db, actor, club.id)


async def _admin_paths(db: AsyncSession, actor: User) -> list[str] | None:
    """The subtrees of the actor's roles of ADMIN_ASSOCIATION rank or higher (None: everything)."""
    from app.services.admin_activity import scope_paths

    return await scope_paths(db, actor)


# ----------------------------------------------------------------------------
# Sign-up: search the association's clubs (public)
# ----------------------------------------------------------------------------
async def search_clubs(db: AsyncSession, association_id: uuid.UUID, q: str) -> list[dict]:
    """ACTIVE clubs under the association whose name contains `q` (accents and case ignored),
    two characters minimum. Only what anybody may read of a club: its name, church and city,
    and whether it can still take a director. Never a person. A club already registered in an
    open event carries that registration, so the form asks through the event (as before)."""
    from app.services import event_onboarding

    term = " ".join(_fold(q).split())
    if len(term) < 2:
        return []
    association = await db.get(Organization, association_id)
    if association is None or association.type != "association" or association.status != "active" \
            or not association.path:
        return []
    needle = "".join(c for c in unicodedata.normalize("NFKD", q.strip()) if not unicodedata.combining(c))
    clubs = list(
        (
            await db.execute(
                select(Organization)
                .where(
                    Organization.type == "club",
                    Organization.status == "active",
                    Organization.path.op("<@")(association.path),
                    func.unaccent(Organization.name).icontains(" ".join(needle.split()), autoescape=True),
                )
                .order_by(Organization.name, Organization.id)
                .limit(50)
            )
        ).scalars()
    )
    clubs.sort(key=lambda club: (not _fold(club.name).startswith(term), _fold(club.name)))
    clubs = clubs[:SEARCH_LIMIT]
    if not clubs:
        return []
    from app.services import placement

    ids = [club.id for club in clubs]
    refs = await placement.refs_for(db, clubs)
    directors = await event_onboarding._directors_by_club(db, ids)
    pending = await event_onboarding._pending_director_invitations(db, ids)
    in_events = await _open_event_registrations(db, ids)
    out = []
    for club in clubs:
        church = refs.get(club.id, {}).get(placement.CHURCH)
        declared = (club.metadata_json or {}).get("church")
        registration = in_events.get(club.id)
        out.append({
            "id": str(club.id),
            "name": club.name,
            "church": church.name if church is not None else (declared if isinstance(declared, str) else None),
            "city": club.city,
            "has_director": club.id in directors,
            "request_pending": club.id in pending,
            "event_registration_id": str(registration[0]) if registration else None,
            "event_name": registration[1] if registration else None,
        })
    return out


async def _open_event_registrations(db: AsyncSession, club_ids: list[uuid.UUID]
                                    ) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """Each club's registration in an event that takes director requests (the rule of
    `event_onboarding.open_event_clubs`): OPEN or IN_PROGRESS, not on a local node."""
    from app.models import EventRegistration

    rows = (
        await db.execute(
            select(EventRegistration.club_id, EventRegistration.id, Event.name)
            .join(Event, Event.id == EventRegistration.event_id)
            .where(
                EventRegistration.club_id.in_(club_ids),
                EventRegistration.status == "REGISTERED",
                Event.status.in_(("OPEN", "IN_PROGRESS")),
                Event.offline_locked_at.is_(None),
            )
            .order_by(Event.starts_on.desc(), Event.id)
        )
    ).all()
    found: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    for club_id, registration_id, name in rows:
        found.setdefault(club_id, (registration_id, name))
    return found


# ----------------------------------------------------------------------------
# Sign-up: the request
# ----------------------------------------------------------------------------
async def request_existing_club(db: AsyncSession, person: User, club_id: uuid.UUID,
                                request: Request | None) -> Organization:
    """Called by the sign-up, inside its transaction: a refusal raises and leaves no account
    behind. Stores a pending CLUB_DIRECTOR request for the club (no e-mail; the token is never
    shown). When the administration already invited this very address, that invitation is the
    one marked (still «Aprobar»: an unverified sign-up proves nothing about the mailbox)."""
    from app.services import event_onboarding

    club = await db.get(Organization, club_id)
    if club is None or club.type != "club" or club.status != "active" or not club.path:
        raise HTTPException(status.HTTP_409_CONFLICT, CLUB_UNAVAILABLE)
    if person.is_minor:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MINOR)
    if (await event_onboarding._directors_by_club(db, [club.id])).get(club.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, HAS_DIRECTOR)
    if await association_of(db, club) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_ASSOCIATION)
    email = _normalize(person.email)
    waiting = (await event_onboarding._pending_director_invitations(db, [club.id])).get(club.id)
    if waiting is not None:
        if _normalize(waiting.email) != email:
            raise HTTPException(status.HTTP_409_CONFLICT, OTHER_PENDING)
        waiting.on_accept = {**(waiting.on_accept or {}), "request": True,
                             "name": (waiting.on_accept or {}).get("name") or person.name}
        record_audit(db, action=AUDIT_REQUEST, entity_type="ORGANIZATION", entity_id=club.id, actor=person,
                     metadata={"club_id": str(club.id), "invitation_id": str(waiting.id), "via": "already_invited"},
                     request=request)
        return club

    now = utcnow()
    invitation = OrgInvitation(
        id=uuid.uuid4(),
        organization_id=club.id,
        role=CLUB_DIRECTOR,
        email=email,
        # A token nobody will ever see: «Aprobar» either appoints, or e-mails a NEW one.
        token_hash=sha256_hex(generate_url_token()),
        expires_at=now + timedelta(days=REQUEST_DAYS),
        created_by_id=person.id,
        created_at=now,
        return_to="/panel",
        on_accept={"type": REQUEST_TYPE, "name": person.name, "request": True},
    )
    db.add(invitation)
    record_audit(db, action=AUDIT_REQUEST, entity_type="ORGANIZATION", entity_id=club.id, actor=person,
                 details=f"Asked to direct CLUB {club.name}",
                 metadata={"club_id": str(club.id), "invitation_id": str(invitation.id), "via": SOURCE_SIGNUP},
                 request=request)
    return club


# ----------------------------------------------------------------------------
# The administration: list, approve, reject
# ----------------------------------------------------------------------------
def _pending_requests():
    return select(OrgInvitation, Organization).join(
        Organization, Organization.id == OrgInvitation.organization_id
    ).where(
        OrgInvitation.role == CLUB_DIRECTOR,
        OrgInvitation.on_accept["request"].astext == "true",
        OrgInvitation.accepted_at.is_(None),
        OrgInvitation.revoked_at.is_(None),
        OrgInvitation.expires_at > utcnow(),
    )


async def pending(db: AsyncSession, actor: User, *, limit: int = 100, offset: int = 0) -> list[dict]:
    """Every pending director request in the actor's scope, oldest first."""
    paths = await _admin_paths(db, actor)
    if paths is not None and not paths:
        return []
    stmt = _pending_requests()
    if paths is not None:
        stmt = stmt.where(or_(*(Organization.path.op("<@")(path) for path in paths)))
    stmt = stmt.order_by(OrgInvitation.created_at, OrgInvitation.id).limit(limit).offset(offset)
    rows = [(inv, club) for inv, club in (await db.execute(stmt)).all() if await may_decide(db, actor, club)]
    if not rows:
        return []
    from app.services import placement

    clubs = [club for _, club in rows]
    refs = await placement.refs_for(db, clubs)
    emails = {_normalize(inv.email) for inv, _ in rows}
    people = {
        _normalize(person.email): person
        for person in (await db.execute(select(User).where(User.email.in_(emails)))).scalars()
    }
    event_ids = set()
    for inv, _ in rows:
        try:
            if (inv.on_accept or {}).get("event_id"):
                event_ids.add(uuid.UUID(str(inv.on_accept["event_id"])))
        except ValueError:
            continue
    events = {
        event.id: event
        for event in (await db.execute(select(Event).where(Event.id.in_(event_ids)))).scalars()
    } if event_ids else {}
    out = []
    for inv, club in rows:
        on_accept = inv.on_accept or {}
        person = people.get(_normalize(inv.email))
        found = refs.get(club.id, {})
        event = None
        try:
            event = events.get(uuid.UUID(str(on_accept["event_id"]))) if on_accept.get("event_id") else None
        except ValueError:
            event = None
        association = found.get(placement.ASSOCIATION)
        church = found.get(placement.CHURCH)
        out.append({
            "id": str(inv.id),
            "club": {"id": str(club.id), "name": club.name},
            "association_name": association.name if association else None,
            "church": church.name if church else None,
            "city": club.city,
            "name": on_accept.get("name") or (person.name if person else None),
            "email": inv.email,
            "has_account": person is not None,
            "verified": bool(person is not None and person.verification_status == "VERIFIED"),
            "source": SOURCE_EVENT if on_accept.get("event_id") else SOURCE_SIGNUP,
            "event": {"id": str(event.id), "name": event.name} if event else None,
            "created_at": inv.created_at,
            "expires_at": inv.expires_at,
        })
    return out


async def _get_request(db: AsyncSession, invitation_id: uuid.UUID) -> tuple[OrgInvitation, Organization]:
    row = (await db.execute(_pending_requests().where(OrgInvitation.id == invitation_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return row[0], row[1]


async def approve(db: AsyncSession, actor: User, invitation_id: uuid.UUID, request: Request | None
                  ) -> tuple[Organization, OrgInvitation, str | None]:
    """«Aprobar». Returns `(club, invitation, token)`: with a token, the caller e-mails the
    renewed invitation after the commit; without one, the person is the director now.

    The rule of the event's «Aprobar» (`event_onboarding.approve_director_request`): only an
    account that proved its address (VERIFIED) is appointed at once."""
    from app.services import clubs as club_service
    from app.services import event_onboarding

    invitation, club = await _get_request(db, invitation_id)
    if not await may_decide(db, actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    if (await event_onboarding._directors_by_club(db, [club.id])).get(club.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, event_onboarding.HAS_OTHER_DIRECTOR)
    if club.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, CLUB_UNAVAILABLE)
    person = (await db.execute(select(User).where(User.email == _normalize(invitation.email)))).scalar_one_or_none()
    via = "email"
    if person is not None and (person.role in ADMIN_ROLES or person.is_minor or person.status != "ACTIVE"):
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_ELIGIBLE)
    if person is None or person.verification_status != "VERIFIED":
        renewed, token = await org_invitations.resend(db, invitation, organization=club, actor=actor, request=request)
        renewed.on_accept = {**(renewed.on_accept or {}), "request": False}
        result = (club, renewed, token)
        invitation_ref = renewed
    else:
        if await club_service.leads_a_club(db, person):
            raise HTTPException(status.HTTP_409_CONFLICT, LEADS_ANOTHER)
        await club_service._appoint_director(db, actor, club, person, request)
        invitation.accepted_at = utcnow()
        invitation.accepted_by_id = person.id
        via = "appoint"
        result = (club, invitation, None)
        invitation_ref = invitation
    record_audit(db, action=AUDIT_APPROVE, entity_type="ORGANIZATION", entity_id=club.id, actor=actor,
                 details=f"Approved the director request for CLUB {club.name}",
                 metadata={"club_id": str(club.id), "invitation_id": str(invitation_ref.id), "via": via,
                           "director_id": str(person.id) if person is not None and via == "appoint" else None,
                           "source": SOURCE_EVENT if (invitation.on_accept or {}).get("event_id") else SOURCE_SIGNUP},
                 request=request)
    return result


async def reject(db: AsyncSession, actor: User, invitation_id: uuid.UUID, request: Request | None
                 ) -> tuple[Organization, OrgInvitation]:
    """«Rechazar»: the request is revoked. The account stays (an adult INSTRUCTOR)."""
    invitation, club = await _get_request(db, invitation_id)
    if not await may_decide(db, actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    await org_invitations.revoke(db, invitation, actor=actor, request=request)
    record_audit(db, action=AUDIT_REJECT, entity_type="ORGANIZATION", entity_id=club.id, actor=actor,
                 details=f"Rejected the director request for CLUB {club.name}",
                 metadata={"club_id": str(club.id), "invitation_id": str(invitation.id),
                           "source": SOURCE_EVENT if (invitation.on_accept or {}).get("event_id") else SOURCE_SIGNUP},
                 request=request)
    return club, invitation


# ----------------------------------------------------------------------------
# «Avisos» to the administration: who is told, and the e-mail
# ----------------------------------------------------------------------------
async def association_admins(db: AsyncSession, club: Organization) -> list[User]:
    """ACTIVE accounts with ADMIN_ASSOCIATION on the club's association: as their principal
    role (`users.role` + `organization_id`) or as an ACTIVE scoped assignment."""
    association = await association_of(db, club)
    if association is None:
        return []
    scoped = select(RoleAssignment.user_id).where(
        RoleAssignment.role == ADMIN_ASSOCIATION,
        RoleAssignment.organization_id == association.id,
        RoleAssignment.status == "ACTIVE",
        # The primary row is only a mirror of users.role/organization_id (covered below) and can be
        # stale after a demotion until it's re-synced: never a source of authority (as in rbac).
        RoleAssignment.is_primary.is_(False),
    )
    stmt = (
        select(User)
        .where(
            User.status == "ACTIVE",
            or_(
                (User.role == ADMIN_ASSOCIATION) & (User.organization_id == association.id),
                User.id.in_(scoped),
            ),
        )
        .order_by(User.email)
    )
    return list((await db.execute(stmt)).scalars().all())


async def notify_admins(kind: str, club_id: uuid.UUID, *, person_name: str, person_email: str,
                        link: str, event_name: str | None = None) -> None:
    """Background task (after the commit): one e-mail per administrator of the club's
    association, each recorded in `notification_log` (about the club). Swallows everything."""
    from app.db import SessionLocal
    from app.services import email as email_service
    from app.services import notifications

    try:
        async with SessionLocal() as db:
            club = await db.get(Organization, club_id)
            if club is None:
                return
            association = await association_of(db, club)
            recipients = [(admin.id, admin.email, admin.name) for admin in await association_admins(db, club)]
            club_name = club.name
            association_name = association.name if association else None
            logs = []
            for admin_id, email, name in recipients:
                log = notifications.stage_log(db, kind=kind, email=email, entity_type=notifications.ORGANIZATION,
                                              entity_id=club_id, user_id=admin_id)
                logs.append((log.id, email, name))
            await db.commit()
    except Exception:
        logger.exception("could not prepare the %s notice for club %s", kind, club_id)
        return
    for log_id, email, name in logs:
        await notifications.send_and_record(
            email_service.send_admin_request_email, log_id, email, name, kind, club_name, person_name,
            person_email, link, association_name=association_name, event_name=event_name,
        )


async def event_notice(db: AsyncSession, registration_id: uuid.UUID
                       ) -> tuple[str, Organization, str, str] | None:
    """`(kind, club, link, event_name)` of a request made through an event: the link is the
    event's Clubes screen, where its administration approves it (also listed in
    `/admin/directores`)."""
    from app.models import EventRegistration
    from app.services import event_onboarding

    row = (
        await db.execute(
            select(Organization, Event)
            .join(EventRegistration, EventRegistration.club_id == Organization.id)
            .join(Event, Event.id == EventRegistration.event_id)
            .where(EventRegistration.id == registration_id)
        )
    ).first()
    if row is None:
        return None
    club, event = row
    return NOTICE_DIRECTOR_REQUEST, club, event_onboarding.event_url(event, "/clubes"), event.name


def queue_admin_notice(background: BackgroundTasks | None, kind: str, club: Organization, *, person_name: str,
                       person_email: str, link: str, event_name: str | None = None) -> None:
    """Hand the notice to `BackgroundTasks`. Call it after the commit; it never fails the request."""
    if background is None or club is None:
        return
    try:
        background.add_task(notify_admins, kind, club.id, person_name=person_name, person_email=person_email,
                            link=link, event_name=event_name)
    except Exception:  # pragma: no cover - adding a task does not fail, but a notice never breaks a flow
        logger.exception("could not queue the %s notice", kind)
