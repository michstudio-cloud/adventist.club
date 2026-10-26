"""Alta desde el evento (031): directores, jueces y coordinación sin salir de events.

Three paths, all from the event's screens:

  * «Vincular director» of a registered club — an ACTIVE account is appointed with the
    administration's own logic (`clubs._eligible_director` + `clubs._appoint_director`, the
    code of `POST /org-nodes/clubs/admin` and of `migrations/import_event_roster.py`); an
    address without an account gets a CLUB_DIRECTOR invitation (`org_invitations.create`)
    whose `return_to` is the event and whose `on_accept` only says it came from this event
    (so the event lists it). Who: the association's administration (`roles.admin`) AND
    rank over the club (`org_invitations.can_invite`).
  * «Importar lista» — a CSV «club, director, correo» (same header synonyms as the roster
    importer), dry run first, the same per-row logic.
  * «Invitar juez / coordinación» — an account that exists is added to `event_staff` right
    away (the path that already existed); an address without one gets an EVENT_STAFF
    invitation whose `on_accept` creates the `event_staff` rows ONCE when accepted.

Nothing here commits: the router does (and queues the e-mails after the commit).
"""
from __future__ import annotations

import csv
import importlib.util
import io
import pathlib
import sys
import urllib.parse
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, HTTPException, Request, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    ClubMembership,
    Event,
    EventActivity,
    EventRegistration,
    EventStaff,
    OrgInvitation,
    Organization,
    RoleAssignment,
    User,
)
from app.people import is_minor_user
from app.security import CLUB_DIRECTOR, utcnow
from app.services import org_invitations
from app.services.audit import record_audit
from app.services.event_access import COORDINATOR, JUDGE

EVENT_STAFF = org_invitations.EVENT_STAFF
STAFF_TYPE = "event_staff"
DIRECTOR_TYPE = "event_director"
ENTITY_STAFF = "EVENT_STAFF"

# Director states shown in the event's club list.
NONE, INVITED, ACTIVE = "NONE", "INVITED", "ACTIVE"

EVENT_GONE = "El evento de esta invitación ya no existe."
EVENT_FROZEN = "El evento ya cerró: la invitación ya no puede darte acceso."
EVENT_OFFLINE = (
    "El evento se está operando en un nodo local sin internet: acepta la invitación cuando "
    "vuelva a la nube, o pide a la coordinación que te dé de alta en el nodo."
)
ADMIN_ONLY = "Sólo la administración de la asociación vincula directores."
HAS_OTHER_DIRECTOR = (
    "El club ya tiene un director activo. Para cambiarlo, retíralo desde la administración del club."
)
OTHER_PENDING = "Hay una invitación pendiente para otro correo en este club: revócala primero."
NOT_REGISTERED = "El club no está inscrito en este evento."
INVITATION_NOT_HERE = "Invitación no encontrada en este evento"


# ----------------------------------------------------------------------------
# Links
# ----------------------------------------------------------------------------
def events_origin() -> str:
    """events.adventist.club (the host of the pass QR, `EVENT_PASS_URL_BASE`)."""
    parts = urlsplit(settings.event_pass_url_base)
    return f"{parts.scheme}://{parts.netloc}"


def event_url(event: Event, section: str = "") -> str:
    """The event on its own host: the module lives at the root there (`/<id>/juez`)."""
    return f"{events_origin()}/{event.id}{section}"


def whatsapp_text(text: str) -> str:
    return "https://wa.me/?text=" + urllib.parse.quote(text)


def _normalize(email: str | None) -> str:
    return (email or "").strip().lower()


def _valid_email(value: str) -> bool:
    return _roster().valid_email(value)


# ----------------------------------------------------------------------------
# The roster importer, reused (`migrations/import_event_roster.py`)
# ----------------------------------------------------------------------------
@dataclass
class _RosterHelpers:
    fold: object
    header_key: object
    clean: object
    columns: dict
    valid_email: object


@lru_cache(maxsize=1)
def _roster() -> _RosterHelpers:
    """The roster importer's header synonyms and normalisers, loaded from the script itself
    so the API and the CLI read a list the same way."""
    path = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "import_event_roster.py"
    spec = importlib.util.spec_from_file_location("import_event_roster_shared", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclasses look their module up while being defined
    spec.loader.exec_module(module)
    return _RosterHelpers(
        fold=module.fold, header_key=module.header_key, clean=module.clean,
        columns=module.COLUMNS, valid_email=module._valid_email,
    )


# ----------------------------------------------------------------------------
# Directors of the event's clubs
# ----------------------------------------------------------------------------
async def _directors_by_club(db: AsyncSession, club_ids: list[uuid.UUID]) -> dict[uuid.UUID, User]:
    """The ACTIVE director of each club: a CLUB_DIRECTOR membership, else (legacy) the
    account whose principal role is CLUB_DIRECTOR of that club."""
    if not club_ids:
        return {}
    rows = (
        await db.execute(
            select(ClubMembership.club_id, User)
            .join(User, User.id == ClubMembership.user_id)
            .where(
                ClubMembership.club_id.in_(club_ids),
                ClubMembership.role == CLUB_DIRECTOR,
                ClubMembership.status == "ACTIVE",
                User.status == "ACTIVE",
            )
            .order_by(ClubMembership.club_id, User.name)
        )
    ).all()
    found: dict[uuid.UUID, User] = {}
    for club_id, person in rows:
        found.setdefault(club_id, person)
    legacy = (
        await db.execute(
            select(User).where(
                User.organization_id.in_([c for c in club_ids if c not in found]),
                User.role == CLUB_DIRECTOR,
                User.status == "ACTIVE",
            )
        )
    ).scalars()
    for person in legacy:
        found.setdefault(person.organization_id, person)
    return found


async def _pending_director_invitations(db: AsyncSession, club_ids: list[uuid.UUID]) -> dict[uuid.UUID, OrgInvitation]:
    if not club_ids:
        return {}
    rows = (
        await db.execute(
            select(OrgInvitation)
            .where(
                OrgInvitation.organization_id.in_(club_ids),
                OrgInvitation.role == CLUB_DIRECTOR,
                OrgInvitation.accepted_at.is_(None),
                OrgInvitation.revoked_at.is_(None),
                OrgInvitation.expires_at > utcnow(),
            )
            .order_by(OrgInvitation.created_at.desc())
        )
    ).scalars()
    found: dict[uuid.UUID, OrgInvitation] = {}
    for row in rows:
        found.setdefault(row.organization_id, row)
    return found


def invitation_out(row: OrgInvitation, *, club: Organization | None = None,
                   registration_id: uuid.UUID | None = None) -> dict:
    """An invitation as the event's screens see it. The token is never here."""
    on_accept = row.on_accept or {}
    if row.role == EVENT_STAFF:
        kind = "judge" if on_accept.get("staff_role") == JUDGE else "coordinator"
    else:
        kind = "director"
    return {
        "id": str(row.id),
        "kind": kind,
        "email": row.email,
        "name": on_accept.get("name"),
        "state": org_invitations.state_of(row),
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "accepted_at": row.accepted_at,
        "activity_ids": on_accept.get("activity_ids"),
        "club_id": str(club.id) if club else (str(row.organization_id) if kind == "director" else None),
        "club_name": club.name if club else None,
        "registration_id": str(registration_id) if registration_id else None,
    }


async def directors(db: AsyncSession, event: Event) -> list[dict]:
    """One row per REGISTERED club: «Sin director» / «Invitado · pendiente» / «Activo»."""
    rows = (
        await db.execute(
            select(EventRegistration, Organization)
            .join(Organization, Organization.id == EventRegistration.club_id)
            .where(EventRegistration.event_id == event.id, EventRegistration.status == "REGISTERED")
            .order_by(Organization.name)
        )
    ).all()
    club_ids = [club.id for _, club in rows]
    active = await _directors_by_club(db, club_ids)
    pending = await _pending_director_invitations(db, club_ids)
    out = []
    for registration, club in rows:
        person = active.get(club.id)
        invitation = pending.get(club.id)
        state = ACTIVE if person else INVITED if invitation else NONE
        out.append({
            "registration_id": str(registration.id),
            "club_id": str(club.id),
            "club_name": club.name,
            "state": state,
            "director": {"id": str(person.id), "name": person.name, "email": person.email} if person else None,
            "invitation": invitation_out(invitation, club=club, registration_id=registration.id)
            if invitation and not person else None,
        })
    return out


# ----------------------------------------------------------------------------
# Linking one director
# ----------------------------------------------------------------------------
@dataclass
class LinkResult:
    action: str  # appoint | invite | already_director | invitation_pending
    registration_id: uuid.UUID
    club: Organization
    director: User | None = None
    invitation: OrgInvitation | None = None
    token: str | None = field(default=None, repr=False)


async def _require_director_rights(db: AsyncSession, actor: User, roles, club: Organization) -> None:
    if not roles.admin or not await org_invitations.can_invite(db, actor, CLUB_DIRECTOR, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ADMIN_ONLY)


async def registration_with_club(db: AsyncSession, event: Event, registration_id: uuid.UUID
                                 ) -> tuple[EventRegistration, Organization]:
    row = (
        await db.execute(
            select(EventRegistration, Organization)
            .join(Organization, Organization.id == EventRegistration.club_id)
            .where(EventRegistration.id == registration_id, EventRegistration.event_id == event.id)
        )
    ).first()
    if row is None or row[0].status != "REGISTERED":
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_REGISTERED)
    return row[0], row[1]


async def link_director(
    db: AsyncSession, actor: User, roles, event: Event, registration: EventRegistration,
    club: Organization, *, name: str | None, email: str, request: Request | None,
) -> LinkResult:
    """Idempotent: the same director twice is `already_director`; the same pending address
    twice is `invitation_pending` (the screen offers «Reenviar»)."""
    from app.services import clubs as club_service

    await _require_director_rights(db, actor, roles, club)
    email = _normalize(email)
    if not _valid_email(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Correo no válido.")
    name = " ".join((name or "").split())[:120] or None

    current = (await _directors_by_club(db, [club.id])).get(club.id)
    if current is not None:
        if _normalize(current.email) == email:
            return LinkResult("already_director", registration.id, club, director=current)
        raise HTTPException(status.HTTP_409_CONFLICT, HAS_OTHER_DIRECTOR)
    pending = (await _pending_director_invitations(db, [club.id])).get(club.id)
    if pending is not None:
        if _normalize(pending.email) == email:
            return LinkResult("invitation_pending", registration.id, club, invitation=pending)
        raise HTTPException(status.HTTP_409_CONFLICT, OTHER_PENDING)

    person, invite_email = await club_service._eligible_director(db, actor, email)
    if person is not None:
        await club_service._appoint_director(db, actor, club, person, request)
        record_audit(db, action="EVENT_DIRECTOR_LINK", entity_type="EVENT", entity_id=event.id, actor=actor,
                     metadata={"club_id": str(club.id), "director_id": str(person.id), "via": "appoint"},
                     request=request)
        return LinkResult("appoint", registration.id, club, director=person)

    invitation, token = await org_invitations.create(
        db, organization=club, actor=actor, role=CLUB_DIRECTOR, email=invite_email, request=request,
        return_to=event_url(event, "/club"),
        on_accept={"type": DIRECTOR_TYPE, "event_id": str(event.id), "name": name},
    )
    record_audit(db, action="EVENT_DIRECTOR_LINK", entity_type="EVENT", entity_id=event.id, actor=actor,
                 metadata={"club_id": str(club.id), "invitation_id": str(invitation.id), "via": "invite"},
                 request=request)
    return LinkResult("invite", registration.id, club, invitation=invitation, token=token)


# ----------------------------------------------------------------------------
# «Importar lista»: club, director, correo
# ----------------------------------------------------------------------------
IMPORT_COLUMNS = ("club", "director", "correo_director")
MAX_IMPORT_ROWS = 300


def _read_csv(text: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect)]
    numbered = [(index + 1, row) for index, row in enumerate(rows) if any(cell.strip() for cell in row)]
    if not numbered:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "La lista está vacía.")
    (_, header), body = numbered[0], numbered[1:]
    return header, body


def _map_columns(header: list[str]) -> dict[str, int]:
    helpers = _roster()
    keys = [helpers.header_key(cell) for cell in header]
    mapping: dict[str, int] = {}
    for field_name in IMPORT_COLUMNS:
        synonyms = {helpers.header_key(s) for s in helpers.columns[field_name]}
        for index, key in enumerate(keys):
            if key in synonyms and index not in mapping.values():
                mapping[field_name] = index
                break
    missing = [name for name in ("club", "correo_director") if name not in mapping]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Faltan columnas: " + ", ".join("correo" if m == "correo_director" else m for m in missing)
            + ". Encabezados esperados: club, director, correo.",
        )
    return mapping


async def import_directors(
    db: AsyncSession, actor: User, roles, event: Event, text: str, *, commit: bool,
    request: Request | None,
) -> tuple[list[dict], list[LinkResult]]:
    """Each row in its own savepoint (a bad row never aborts the others). Dry run: rolled
    back at the end — nothing written, no invitation, no e-mail. Returns the per-row report
    and, on commit, the results that created an invitation (the router e-mails them)."""
    if not roles.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ADMIN_ONLY)
    if len(text) > 200_000:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "La lista es demasiado grande.")
    helpers = _roster()
    header, body = _read_csv(text)
    mapping = _map_columns(header)
    if len(body) > MAX_IMPORT_ROWS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Máximo {MAX_IMPORT_ROWS} filas por lista.")

    registered = (
        await db.execute(
            select(EventRegistration, Organization)
            .join(Organization, Organization.id == EventRegistration.club_id)
            .where(EventRegistration.event_id == event.id, EventRegistration.status == "REGISTERED")
        )
    ).all()
    by_name: dict[str, list[tuple[uuid.UUID, uuid.UUID]]] = {}
    by_code: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
    for registration, club in registered:
        by_name.setdefault(helpers.fold(club.name), []).append((registration.id, club.id))
        if club.code:
            by_code[helpers.fold(club.code)] = (registration.id, club.id)

    def cell(row: list[str], name: str) -> str:
        index = mapping.get(name)
        return helpers.clean(row[index]) if index is not None and index < len(row) else ""

    report: list[dict] = []
    created: list[LinkResult] = []
    seen_emails: dict[str, int] = {}
    seen_clubs: dict[uuid.UUID, int] = {}
    actor_id, event_id = actor.id, event.id
    for line, row in body:
        club_text, director, email = cell(row, "club"), cell(row, "director"), _normalize(cell(row, "correo_director"))
        entry = {"line": line, "club": club_text, "director": director, "email": email,
                 "club_id": None, "action": None, "problems": []}
        report.append(entry)
        if not club_text:
            entry["problems"].append("Falta el club.")
        if not email:
            entry["problems"].append("Falta el correo.")
        elif not _valid_email(email):
            entry["problems"].append("Correo no válido.")
        matches = by_name.get(helpers.fold(club_text)) or ([by_code[helpers.fold(club_text)]] if helpers.fold(club_text) in by_code else [])
        if club_text and not matches:
            entry["problems"].append("El club no está inscrito en este evento.")
        elif len(matches) > 1:
            entry["problems"].append("Hay dos clubes inscritos con ese nombre: usa su código.")
        if email and email in seen_emails:
            entry["problems"].append(f"El mismo correo ya está en la fila {seen_emails[email]}.")
        if matches and len(matches) == 1 and matches[0][1] in seen_clubs:
            entry["problems"].append(f"El mismo club ya está en la fila {seen_clubs[matches[0][1]]}.")
        if email:
            seen_emails.setdefault(email, line)
        if matches and len(matches) == 1:
            seen_clubs.setdefault(matches[0][1], line)
            entry["club_id"] = str(matches[0][1])
        if entry["problems"]:
            entry["action"] = "error"
            continue
        registration_id, club_id = matches[0]
        try:
            async with db.begin_nested():
                event_row = await db.get(Event, event_id)
                actor_row = await db.get(User, actor_id)
                registration, club = await registration_with_club(db, event_row, registration_id)
                result = await link_director(db, actor_row, roles, event_row, registration, club,
                                             name=director, email=email, request=request)
            entry["action"] = result.action
            if result.director is not None:
                entry["director_name"] = result.director.name
            if result.token:
                created.append(result)
        except HTTPException as exc:
            entry["action"] = "error"
            entry["problems"].append(str(exc.detail))
    if not commit:
        await db.rollback()
        created = []
    return report, created


# ----------------------------------------------------------------------------
# Judges and coordination by e-mail
# ----------------------------------------------------------------------------
async def _activity_ids(db: AsyncSession, event: Event, ids: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    if not ids:
        return None
    unique = list(dict.fromkeys(ids))
    found = set((await db.execute(
        select(EventActivity.id).where(EventActivity.event_id == event.id, EventActivity.id.in_(unique))
    )).scalars())
    if len(found) != len(unique):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Actividad no encontrada en este evento")
    return unique


async def _add_rows(db: AsyncSession, event: Event, person: User, staff_role: str,
                    activity_ids: list[uuid.UUID] | None, actor_id: uuid.UUID | None,
                    request: Request | None, via: str, audit_actor: User | None) -> int:
    """The `event_staff` rows (one per activity, or one for «todas»), skipping what exists."""
    targets: list[uuid.UUID | None] = list(activity_ids) if activity_ids else [None]
    created = 0
    for activity_id in targets:
        duplicate = await db.scalar(select(exists().where(
            EventStaff.event_id == event.id, EventStaff.user_id == person.id, EventStaff.role == staff_role,
            EventStaff.active,
            EventStaff.activity_id.is_(None) if activity_id is None else EventStaff.activity_id == activity_id,
        )))
        if duplicate:
            continue
        row = EventStaff(id=uuid.uuid4(), event_id=event.id, user_id=person.id, role=staff_role,
                         activity_id=activity_id, active=True, created_by_id=actor_id)
        db.add(row)
        created += 1
        record_audit(db, action="EVENT_STAFF_ADD", entity_type=ENTITY_STAFF, entity_id=row.id,
                     actor=audit_actor,
                     metadata={"event_id": str(event.id), "user_id": str(person.id), "role": staff_role,
                               "activity_id": str(activity_id) if activity_id else None, "via": via},
                     request=request)
    return created


@dataclass
class StaffInviteResult:
    action: str  # added | invite
    person: User | None = None
    rows: int = 0
    invitation: OrgInvitation | None = None
    token: str | None = field(default=None, repr=False)


async def invite_staff(
    db: AsyncSession, actor: User, event: Event, *, email: str, name: str | None, staff_role: str,
    activity_ids: list[uuid.UUID] | None, request: Request | None,
) -> StaffInviteResult:
    """Coordination already checked by the router. An ACTIVE adult account is added now; an
    address without an account is invited (EVENT_STAFF + on_accept)."""
    from app.services import events as event_service

    event_service.require_editable(event)
    email = _normalize(email)
    if not _valid_email(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Correo no válido.")
    if staff_role == COORDINATOR:
        activity_ids = None
    activity_ids = await _activity_ids(db, event, activity_ids)
    person = await db.scalar(select(User).where(User.email == email))
    if person is not None:
        if person.status != "ACTIVE":
            raise HTTPException(status.HTTP_409_CONFLICT, "Esa cuenta no está activa.")
        if is_minor_user(person):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Un menor de edad no puede ser personal del evento")
        rows = await _add_rows(db, event, person, staff_role, activity_ids, actor.id, request, via="direct",
                               audit_actor=actor)
        if rows:
            # The same audit the old path wrote, attributed to whoever added them.
            record_audit(db, action="EVENT_STAFF_INVITE_DIRECT", entity_type="EVENT", entity_id=event.id,
                         actor=actor, metadata={"user_id": str(person.id), "role": staff_role, "rows": rows},
                         request=request)
        return StaffInviteResult("added", person=person, rows=rows)

    organization = await db.get(Organization, event.organization_id)
    name = " ".join((name or "").split())[:120] or None
    invitation, token = await org_invitations.create(
        db, organization=organization, actor=actor, role=EVENT_STAFF, email=email, request=request,
        audit_action="EVENT_STAFF_INVITE",
        return_to=event_url(event, "/juez" if staff_role == JUDGE else ""),
        on_accept={"type": STAFF_TYPE, "event_id": str(event.id), "staff_role": staff_role,
                   "activity_ids": [str(a) for a in activity_ids] if activity_ids else None, "name": name},
        event_authorized=True,
    )
    return StaffInviteResult("invite", invitation=invitation, token=token)


async def check_on_accept(db: AsyncSession, invitation: OrgInvitation, user: User) -> Event:
    """Before the token is spent: the event exists, still takes changes and is in the cloud."""
    from app.services import events as event_service

    on_accept = invitation.on_accept or {}
    if on_accept.get("type") != STAFF_TYPE:
        raise org_invitations.invalid_invitation()
    try:
        event = await db.get(Event, uuid.UUID(str(on_accept.get("event_id"))))
    except ValueError:
        event = None
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, EVENT_GONE)
    if event.status not in event_service.EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, EVENT_FROZEN)
    if event.offline_locked_at is not None:
        raise HTTPException(status.HTTP_423_LOCKED, EVENT_OFFLINE)
    return event


async def apply_on_accept(db: AsyncSession, invitation: OrgInvitation, user: User, inviter: User | None,
                          request: Request | None) -> int:
    """Runs right AFTER the token was spent (`org_invitations._claim`, atomic): so at most
    once per invitation. Activities deleted since the invitation are skipped."""
    event = await check_on_accept(db, invitation, user)
    on_accept = invitation.on_accept or {}
    staff_role = on_accept.get("staff_role") if on_accept.get("staff_role") in (JUDGE, COORDINATOR) else JUDGE
    wanted = on_accept.get("activity_ids") or None
    activity_ids = None
    if wanted and staff_role == JUDGE:
        ids = []
        for raw in wanted:
            try:
                ids.append(uuid.UUID(str(raw)))
            except ValueError:
                continue
        alive = set((await db.execute(
            select(EventActivity.id).where(EventActivity.event_id == event.id, EventActivity.id.in_(ids))
        )).scalars())
        activity_ids = [a for a in ids if a in alive]
        if not activity_ids:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Las actividades de esta invitación ya no existen: pide una nueva.")
    return await _add_rows(db, event, user, staff_role, activity_ids,
                           inviter.id if inviter else None, request, via="invitation",
                           audit_actor=user)


# ----------------------------------------------------------------------------
# Listing, resending and revoking the event's invitations
# ----------------------------------------------------------------------------
async def event_invitations(db: AsyncSession, event: Event, *, include_dead: bool = False) -> list[dict]:
    rows = list((await db.execute(
        select(OrgInvitation)
        .where(OrgInvitation.on_accept["event_id"].astext == str(event.id))
        .order_by(OrgInvitation.created_at.desc())
    )).scalars())
    clubs = {c.id: c for c in (await db.execute(
        select(Organization).where(Organization.id.in_({r.organization_id for r in rows if r.role == CLUB_DIRECTOR}))
    )).scalars()} if rows else {}
    out = []
    for row in rows:
        item = invitation_out(row, club=clubs.get(row.organization_id))
        if include_dead or item["state"] in (org_invitations.PENDING, org_invitations.EXPIRED):
            out.append(item)
    return out


async def get_event_invitation(db: AsyncSession, event: Event, invitation_id: uuid.UUID) -> OrgInvitation:
    row = await db.get(OrgInvitation, invitation_id)
    if row is None or (row.on_accept or {}).get("event_id") != str(event.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVITATION_NOT_HERE)
    return row


async def require_invitation_rights(db: AsyncSession, actor: User, roles, invitation: OrgInvitation) -> Organization:
    """Staff invitations: the event's coordination. Director invitations: the
    administration with rank over the club (the same rule as linking)."""
    organization = await db.get(Organization, invitation.organization_id)
    if invitation.role == CLUB_DIRECTOR:
        await _require_director_rights(db, actor, roles, organization)
    elif not roles.coordination:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para gestionar este evento")
    return organization


def share_links(event: Event, invitation: OrgInvitation, token: str) -> dict:
    url = org_invitations.join_url(token, invitation.return_to)
    on_accept = invitation.on_accept or {}
    if invitation.role == EVENT_STAFF:
        what = "como juez" if on_accept.get("staff_role") == JUDGE else "en la coordinación"
        text = f"Te invito a participar {what} en «{event.name}» (Adventist.Club Eventos): {url}"
    else:
        text = f"Te invito a dirigir tu club en «{event.name}» (Adventist.Club Eventos): {url}"
    return {"url": url, "whatsapp_url": whatsapp_text(text)}


def queue_email(background: BackgroundTasks, log, event: Event, invitation: OrgInvitation, token: str,
                organization: Organization, inviter: User) -> None:
    """After the commit. Director invitations use the platform's e-mail; staff ones say
    which event and what for."""
    from app.services import email as email_service
    from app.services import notifications

    url = org_invitations.join_url(token, invitation.return_to)
    expires = invitation.expires_at.date().isoformat()
    if invitation.role == EVENT_STAFF:
        background.add_task(
            notifications.send_and_record, email_service.send_event_staff_invitation_email, log.id,
            invitation.email, event.name, (invitation.on_accept or {}).get("staff_role") or JUDGE, url,
            inviter.name, expires,
        )
    else:
        background.add_task(
            notifications.send_and_record, email_service.send_org_invitation_email, log.id,
            invitation.email, organization.name, invitation.role, url, inviter.name, expires,
        )


# ----------------------------------------------------------------------------
# Search-as-you-type within the association
# ----------------------------------------------------------------------------
async def search_people(db: AsyncSession, event: Event, query: str, *, limit: int = 8) -> list[dict]:
    """Adult ACTIVE accounts placed in the event owner's subtree (by `organization_id` or an
    active scoped role) whose name or e-mail contains `query`. Two characters minimum."""
    term = " ".join((query or "").split())
    if len(term) < 2:
        return []
    owner = await db.get(Organization, event.organization_id)
    if owner is None or not owner.path:
        return []
    in_tree = select(Organization.id).where(Organization.path.op("<@")(owner.path))
    by_role = select(RoleAssignment.user_id).where(
        RoleAssignment.status == "ACTIVE", RoleAssignment.organization_id.in_(in_tree)
    )
    like = f"%{term.lower()}%"
    stmt = (
        select(User)
        .where(
            User.status == "ACTIVE",
            or_(User.organization_id.in_(in_tree), User.id.in_(by_role)),
            or_(func.lower(User.name).like(like), func.lower(User.email).like(like)),
        )
        .order_by(User.name)
        .limit(limit * 2)
    )
    people = [p for p in (await db.execute(stmt)).scalars() if not is_minor_user(p)][:limit]
    if not people:
        return []
    staff_rows = (await db.execute(
        select(EventStaff.user_id, EventStaff.role, EventStaff.activity_id)
        .where(EventStaff.event_id == event.id, EventStaff.active, EventStaff.user_id.in_([p.id for p in people]))
    )).all()
    staff: dict[uuid.UUID, set[str]] = {}
    for user_id, role, _ in staff_rows:
        staff.setdefault(user_id, set()).add(role)
    clubs = {}
    club_ids = [p.organization_id for p in people if p.organization_id]
    if club_ids:
        clubs = {o.id: o for o in (await db.execute(select(Organization).where(Organization.id.in_(club_ids)))).scalars()}
    out = []
    for person in people:
        org = clubs.get(person.organization_id)
        out.append({
            "id": str(person.id),
            "name": person.name,
            "email": person.email,
            "role": person.role,
            "organization": org.name if org else None,
            "directs_club": person.role == CLUB_DIRECTOR and org is not None and org.type == "club",
            "event_roles": sorted(staff.get(person.id, set())),
        })
    return out


__all__ = [
    "ACTIVE", "INVITED", "NONE", "LinkResult", "StaffInviteResult", "apply_on_accept", "check_on_accept",
    "directors", "event_invitations", "event_url", "events_origin", "get_event_invitation",
    "import_directors", "invitation_out", "invite_staff", "link_director", "queue_email",
    "registration_with_club", "require_invitation_rights", "search_people", "share_links",
]
