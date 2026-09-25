"""Load an association's Adventurer clubs and their directors into the OFFICIAL tree
(spec docs/superpowers/specs/2026-09-24-eventos.md §2, «Padrón de NTAM»).

    DATABASE_URL=... python import_event_roster.py padron.xlsx --association NTAM \\
        [--actor-email owner@...] [--commit [--send-emails]] [--map campo=Encabezado ...]

Input: CSV (`,`, `;` or tab; UTF-8 or Latin-1) or XLSX (needs `openpyxl`; without it only
CSV is read and the script says so). One row per club. Columns are recognised by name,
case and accents ignored, with Spanish synonyms (see `COLUMNS` below — edit it, or pass
`--map campo=Encabezado`, when the real file names them differently):

    required   club, iglesia, zona (or distrito), correo_director
    expected   ciudad, director
    optional   codigo, telefono, ministerio (default `adventurers`; it is always added)

The mapping actually used is printed first.

Dry run by default: every row is SIMULATED with the same services the API uses, inside a
transaction that is rolled back — nothing is written to the database, no invitation
exists afterwards and no e-mail is sent. It prints a summary and writes the per-row report
as CSV next to the input (`<input>.roster-dry-run.csv`).

`--commit`: one transaction PER CLUB, so a bad row never aborts the others (failures are
summarised at the end):

  * a new club goes through `clubs.stage_admin_club` — the code of `POST
    /org-nodes/clubs/admin`: ACTIVE, ministry `adventurers`, zone and church created or
    reused by name inside the association (a church that lives under ANOTHER zone is a
    problem, never moved or duplicated), and its director appointed when the e-mail has an
    account, or a pending CLUB_DIRECTOR invitation (`org_invitations.create`) when not;
  * an existing club (same `codigo`, else same name + church inside the association) is
    left where it is; `adventurers` is added to its ministries when missing, and its
    director is appointed / invited the same way unless it already has one;
  * everything is audited with the actor (`--actor-email`, a MASTER_GC or an administrator
    of the association; default: the only active MASTER_GC).

Idempotent: a second run finds every club, leaves appointed directors alone and never
duplicates an invitation (a live pending CLUB_DIRECTOR invitation for that e-mail and
club is reported as `invitation_pending`).

E-mails go out ONLY with `--send-emails` (which requires `--commit` and the owner's
explicit approval). Without it the invitations are created and their one-time join URLs
are written to `<input>.join-urls-<UTC timestamp>.SENSITIVE.csv` (mode 600), one file
per run so an earlier file is never overwritten. Tokens are never printed.

Exit status: 0 when every row is fine, 2 when some row has a problem (see the report).

A row with ANY problem (missing field, invalid or repeated e-mail, the same director for
two clubs, a church under another zone, a director who already leads another club…) is
not touched: fix the file and run again.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime
import io
import os
import pathlib
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

# ----------------------------------------------------------------------------
# Column mapping — EDIT HERE when the real list arrives (or use --map).
# Headers are compared after `header_key`: accents, case, punctuation and extra
# spaces ignored («Correo del Director:» == «correo del director»).
# ----------------------------------------------------------------------------
COLUMNS: dict[str, tuple[str, ...]] = {
    "club": ("club", "nombre del club", "nombre club", "club de aventureros", "nombre de club"),
    "iglesia": ("iglesia", "nombre de la iglesia", "iglesia local", "congregacion", "church"),
    "zona": ("zona", "distrito", "zona distrito", "distrito zona", "zone", "district"),
    "ciudad": ("ciudad", "localidad", "municipio", "poblacion", "city"),
    "director": (
        "director", "directora", "director a", "nombre del director", "nombre director",
        "director del club", "nombre del director a", "responsable",
    ),
    "correo_director": (
        "correo director", "correo del director", "correo", "email", "e mail", "mail",
        "correo electronico", "email director", "correo del director a", "email del director",
    ),
    "codigo": ("codigo", "codigo del club", "clave", "code", "id club"),
    "telefono": ("telefono", "tel", "celular", "whatsapp", "telefono del director", "movil", "phone"),
    "ministerio": ("ministerio", "ministerios", "ministry"),
}
REQUIRED_COLUMNS = ("club", "iglesia", "zona", "correo_director")
EXPECTED_COLUMNS = ("ciudad", "director")
REQUIRED_VALUES = REQUIRED_COLUMNS

EVENT_MINISTRY = "adventurers"
MINISTRY_WORDS = {
    "adventurers": "adventurers", "aventureros": "adventurers", "aventurero": "adventurers",
    "pathfinders": "pathfinders", "conquistadores": "pathfinders", "conquistador": "pathfinders",
    "master guides": "master-guides", "master-guides": "master-guides",
    "guias mayores": "master-guides", "guia mayor": "master-guides",
}

CLUB_DIRECTOR = "CLUB_DIRECTOR"


class RosterError(SystemExit):
    """A problem with the whole run (file, association, actor): nothing is done."""


class RowProblem(Exception):
    """A problem with one row: the row is not touched."""


# ----------------------------------------------------------------------------
# Reading the file
# ----------------------------------------------------------------------------
def fold(value: str | None) -> str:
    """Accent- and case-insensitive, spaces collapsed (== `placement.normalize`)."""
    stripped = unicodedata.normalize("NFKD", " ".join((value or "").split()))
    return "".join(ch for ch in stripped if not unicodedata.combining(ch)).lower()


def header_key(value: str | None) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", fold(value)).split())


def clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # a phone or a code typed as a number in Excel
    return " ".join(str(value).split())


def xlsx_available() -> bool:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return False
    return True


def read_table(path: pathlib.Path, sheet: str | None = None) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """-> (headers, [(line number in the file, cells)]). The header is the first non-empty row."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        if not xlsx_available():
            raise RosterError(
                "Este entorno no tiene `openpyxl`: sólo se leen CSV. Exporta la hoja a CSV "
                "(UTF-8) o instala openpyxl (`pip install openpyxl`)."
            )
        import openpyxl

        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = book[sheet] if sheet else book.worksheets[0]
        raw = [(number, [clean(cell) for cell in row]) for number, row in enumerate(ws.iter_rows(values_only=True), 1)]
        book.close()
    else:
        data = path.read_bytes()
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")  # Excel «CSV» on Windows
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text, newline=""), dialect)
        raw = [(reader.line_num, [clean(cell) for cell in row]) for row in reader]

    raw = [(number, cells) for number, cells in raw if any(cells)]
    if not raw:
        raise RosterError(f"{path} está vacío")
    (_, headers), body = raw[0], raw[1:]
    return headers, body


def map_columns(headers: list[str], overrides: dict[str, str] | None = None) -> tuple[dict[str, int], list[str]]:
    """-> ({field: column index}, human-readable lines describing the mapping)."""
    keys = [header_key(h) for h in headers]
    mapping: dict[str, int] = {}
    lines: list[str] = []
    for field_name, wanted in (overrides or {}).items():
        if field_name not in COLUMNS:
            raise RosterError(f"--map: campo desconocido {field_name!r} (campos: {', '.join(COLUMNS)})")
        try:
            mapping[field_name] = keys.index(header_key(wanted))
        except ValueError:
            raise RosterError(f"--map {field_name}={wanted!r}: no hay esa columna. Columnas: {headers}")
    warnings: list[str] = []
    for field_name, synonyms in COLUMNS.items():
        if field_name in mapping:
            continue
        options = {header_key(s) for s in synonyms} | {header_key(field_name)}
        found = [i for i, key in enumerate(keys) if key in options and i not in mapping.values()]
        if found:
            mapping[field_name] = found[0]
            if len(found) > 1:
                warnings.append(
                    f"  aviso: varias columnas valen para {field_name!r}; se usa {headers[found[0]]!r}"
                )
    for field_name in COLUMNS:
        if field_name in mapping:
            lines.append(f"  {field_name:<16} <- {headers[mapping[field_name]]!r}")
        else:
            level = "FALTA (obligatoria)" if field_name in REQUIRED_COLUMNS else (
                "falta (se esperaba)" if field_name in EXPECTED_COLUMNS else "no está (opcional)"
            )
            lines.append(f"  {field_name:<16} {level}")
    lines.extend(warnings)
    unused = [h for i, h in enumerate(headers) if i not in mapping.values() and h]
    if unused:
        lines.append(f"  columnas sin usar: {unused}")
    missing = [f for f in REQUIRED_COLUMNS if f not in mapping]
    if missing:
        raise RosterError(
            "Faltan columnas obligatorias: " + ", ".join(missing)
            + "\nMapeo detectado:\n" + "\n".join(lines)
            + "\nAjusta COLUMNS en el script o pasa --map campo=Encabezado."
        )
    return mapping, lines


@dataclass
class RosterRow:
    line: int
    club: str = ""
    iglesia: str = ""
    zona: str = ""
    ciudad: str = ""
    director: str = ""
    correo_director: str = ""
    codigo: str = ""
    telefono: str = ""
    ministerio: str = ""
    ministries: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Outcome
    status: str = ""
    club_action: str = ""
    club_id: str = ""
    zone_action: str = ""
    church_action: str = ""
    ministry_action: str = ""
    director_account: str = ""
    director_action: str = ""
    invitation_id: str = ""
    invitation_expires: str = ""

    @property
    def email(self) -> str:
        return self.correo_director.strip().lower()

    @property
    def name_key(self) -> tuple[str, str]:
        return fold(self.club), fold(self.iglesia)


def parse_rows(body: list[tuple[int, list[str]]], mapping: dict[str, int]) -> list[RosterRow]:
    rows = []
    for number, cells in body:
        values = {
            name: (cells[index] if index < len(cells) else "") for name, index in mapping.items()
        }
        rows.append(RosterRow(line=number, **values))
    return rows


def _valid_email(value: str) -> bool:
    """The same check the API applies (`AdminClubCreate.director_email` is an EmailStr)."""
    from pydantic import EmailStr, TypeAdapter, ValidationError

    try:
        TypeAdapter(EmailStr).validate_python(value)
    except ValidationError:
        return False
    return True


def parse_ministries(raw: str) -> tuple[list[str], list[str]]:
    """-> (slugs, unknown words). `adventurers` is always included (this is its roster)."""
    slugs: list[str] = []
    unknown: list[str] = []
    for word in re.split(r"[,;/+]| y ", fold(raw)):
        word = word.strip()
        if not word:
            continue
        slug = MINISTRY_WORDS.get(word) or MINISTRY_WORDS.get(word.replace("-", " "))
        if slug is None:
            unknown.append(word)
        elif slug not in slugs:
            slugs.append(slug)
    if EVENT_MINISTRY not in slugs:
        slugs.insert(0, EVENT_MINISTRY)
    return slugs, unknown


def validate_rows(rows: list[RosterRow]) -> None:
    """File-level checks, before looking at the database. Fills `problems` / `notes`."""
    for row in rows:
        for name in REQUIRED_VALUES:
            if not getattr(row, name):
                row.problems.append(f"missing_{name}")
        if row.correo_director and not _valid_email(row.email):
            row.problems.append("invalid_email")
        if len(row.club) == 1:
            row.problems.append("club_name_too_short")
        if not row.director:
            row.notes.append("sin nombre de director")
        if not row.ciudad:
            row.notes.append("sin ciudad")
        row.ministries, unknown = parse_ministries(row.ministerio)
        if unknown:
            row.problems.append("unknown_ministry:" + "|".join(unknown))
        elif row.ministerio and fold(row.ministerio) not in ("adventurers", "aventureros"):
            row.notes.append(f"ministerios: {', '.join(row.ministries)}")

    # The same club twice: identical rows are harmless repeats; anything else is a conflict.
    by_club: dict[tuple[str, str], list[RosterRow]] = {}
    for row in rows:
        if row.club and row.iglesia:
            by_club.setdefault(row.name_key, []).append(row)
    for group in by_club.values():
        if len(group) < 2:
            continue
        signature = {(r.email, fold(r.zona), fold(r.codigo)) for r in group}
        lines = ", ".join(str(r.line) for r in group)
        if len(signature) == 1:
            for repeat in group[1:]:
                repeat.problems.append(f"duplicate_row(line {group[0].line})")
                repeat.status = "skipped"
        else:
            for r in group:
                r.problems.append(f"club_repeated(lines {lines})")

    by_code: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        if row.codigo:
            by_code.setdefault(fold(row.codigo), set()).add(row.name_key)
    for row in rows:
        if row.codigo and len(by_code[fold(row.codigo)]) > 1:
            row.problems.append("code_repeated")

    # One director, one club (a director never leads two live clubs).
    by_email: dict[str, list[RosterRow]] = {}
    for row in rows:
        if row.email and row.status != "skipped":
            by_email.setdefault(row.email, []).append(row)
    for email, group in by_email.items():
        clubs = {r.name_key for r in group}
        if len(clubs) > 1:
            lines = ", ".join(str(r.line) for r in group)
            for r in group:
                r.problems.append(f"same_director_for_several_clubs(lines {lines})")


# ----------------------------------------------------------------------------
# Against the database
# ----------------------------------------------------------------------------
@dataclass
class NewInvitation:
    row: RosterRow
    club_name: str
    email: str
    expires_at: datetime.datetime
    token: str = field(repr=False)  # NEVER printed
    log_id: uuid.UUID | None = None


@dataclass
class RunResult:
    association: str
    actor: str
    commit: bool
    rows: list[RosterRow]
    zones_created: list[str] = field(default_factory=list)
    churches_created: list[str] = field(default_factory=list)
    invitations: list[NewInvitation] = field(default_factory=list)
    emails_sent: int = 0
    emails_failed: int = 0


async def _association(db, reference: str):
    from sqlalchemy import or_, select

    from app.models import Organization

    try:
        as_id = uuid.UUID(reference)
    except ValueError:
        as_id = None
    stmt = select(Organization).where(
        Organization.type == "association",
        Organization.status == "active",
        or_(Organization.code == reference, Organization.id == as_id) if as_id else Organization.code == reference,
    )
    found = (await db.execute(stmt)).scalars().first()
    if found is None or not found.path:
        raise RosterError(f"No hay una asociación activa con código o id {reference!r}")
    return found


async def _actor(db, email: str | None, association):
    from sqlalchemy import select

    from app.models import User
    from app.rbac import org_in_user_scope
    from app.security import MASTER_GC
    from app.services import placement

    if email:
        actor = await db.scalar(select(User).where(User.email == email.strip().lower()))
        if actor is None:
            raise RosterError(f"--actor-email: no existe la cuenta {email!r}")
    else:
        masters = list(
            (await db.execute(select(User).where(User.role == MASTER_GC, User.status == "ACTIVE"))).scalars()
        )
        if len(masters) != 1:
            raise RosterError(
                f"Hay {len(masters)} cuentas MASTER_GC activas: indica quién firma con --actor-email"
            )
        actor = masters[0]
    if actor.status != "ACTIVE":
        raise RosterError(f"La cuenta {actor.email} no está activa")
    if not placement.is_structure_admin(actor) or not await org_in_user_scope(db, actor, association.id):
        raise RosterError(
            f"{actor.email} ({actor.role}) no administra {association.name}: usa un MASTER_GC o "
            "un ADMIN_ASSOCIATION de esa asociación"
        )
    return actor


async def _match_club(db, association, row: RosterRow):
    """The existing club of this row: by `codigo`, else by name + church inside the
    association (accents and case ignored). Raises RowProblem on a conflict."""
    from sqlalchemy import func, select

    from app.models import Organization
    from app.services import placement

    if row.codigo:
        by_code = await db.scalar(
            select(Organization).where(func.lower(Organization.code) == row.codigo.lower())
        )
        if by_code is not None:
            inside = by_code.path and by_code.path.startswith(f"{association.path}.")
            if by_code.type != "club" or not inside:
                raise RowProblem(f"code_taken_outside_association({by_code.type} {by_code.name})")
            if by_code.status != "active":
                raise RowProblem(f"club_{by_code.status}(the association decides it first)")
            if fold(by_code.name) != fold(row.club):
                row.notes.append(f"el código ya es del club «{by_code.name}»: se usa ese")
            return by_code

    stmt = select(Organization).where(
        Organization.type == "club",
        Organization.status.in_(("active", "pending")),
        Organization.path.op("<@")(association.path),
    )
    same_name = [c for c in (await db.execute(stmt)).scalars() if fold(c.name) == fold(row.club)]
    for club in same_name:
        church = placement.church_name_of(club, await placement.ancestors_of(db, club))
        if fold(church) == fold(row.iglesia):
            if club.status != "active":
                raise RowProblem(f"club_{club.status}(the association decides it first)")
            if row.codigo and club.code and fold(club.code) != fold(row.codigo):
                raise RowProblem(f"code_mismatch(existing {club.code})")
            if row.codigo and not club.code:
                row.notes.append("el club existente no tiene código: no se cambia")
            return club
    if same_name:
        raise RowProblem("club_name_taken_in_association(other church)")
    return None


async def _pending_invitations(db, club_id):
    from sqlalchemy import select

    from app.models import OrgInvitation
    from app.security import utcnow

    stmt = select(OrgInvitation).where(
        OrgInvitation.organization_id == club_id,
        OrgInvitation.role == CLUB_DIRECTOR,
        OrgInvitation.accepted_at.is_(None),
        OrgInvitation.revoked_at.is_(None),
        OrgInvitation.expires_at > utcnow(),
    )
    return list((await db.execute(stmt)).scalars())


async def _current_directors(db, club_id) -> list[uuid.UUID]:
    from sqlalchemy import select

    from app.models import ClubMembership
    from app.services import memberships as membership_service

    stmt = select(ClubMembership.user_id).where(
        ClubMembership.club_id == club_id,
        ClubMembership.role == CLUB_DIRECTOR,
        ClubMembership.status == membership_service.ACTIVE,
    )
    return list((await db.execute(stmt)).scalars())


async def _process_row(db, row: RosterRow, *, association, actor, adventurers, result: RunResult):
    from pydantic import ValidationError
    from sqlalchemy import select

    from app.models import User
    from app.schemas.org import AdminClubCreate
    from app.security import utcnow
    from app.services import clubs as club_service
    from app.services import ministries as ministry_service
    from app.services import org_invitations, placement
    from app.services.audit import record_audit

    account = await db.scalar(select(User).where(User.email == row.email))
    row.director_account = "yes" if account is not None else "no"
    zone = await placement._child_by_name(db, association, placement.ZONE, row.zona)
    church = await placement.find_church(db, association, row.iglesia)
    club = await _match_club(db, association, row)
    invited = None

    if club is None:
        row.club_action = "create"
        row.zone_action = "reuse" if zone is not None else "create"
        if church is None:
            row.church_action = "create"
        elif zone is not None and church.parent_id == zone.id:
            row.church_action = "reuse"
        elif church.parent_id == association.id:
            row.church_action = "reuse (placed into the zone)"
        else:
            raise RowProblem(f"church_in_other_zone(church {church.id})")
        try:
            payload = AdminClubCreate(
                name=row.club,
                code=row.codigo or None,
                association_id=association.id,
                zone_name=row.zona,
                church_name=row.iglesia,
                city=row.ciudad or None,
                ministries=row.ministries,
                director_email=row.email,
            )
        except ValidationError as exc:
            raise RowProblem("invalid_row: " + "; ".join(e["msg"] for e in exc.errors()))
        created, refs, director, invited = await club_service.stage_admin_club(db, actor, payload, None)
        await db.flush()
        row.club_id = str(created.id)
        row.ministry_action = "set " + "+".join(row.ministries)
        row.director_action = "appoint" if director is not None else "invite"
        if row.zone_action == "create":
            result.zones_created.append(refs[placement.ZONE].name)
        if row.church_action == "create":
            result.churches_created.append(f"{refs[placement.CHURCH].name} ({refs[placement.ZONE].name})")
        club = created
    else:
        row.club_action = "exists"
        row.club_id = str(club.id)
        ancestors = await placement.ancestors_of(db, club)
        here_zone, here_church = ancestors.get(placement.ZONE), ancestors.get(placement.CHURCH)
        row.zone_action = f"exists ({here_zone.name})" if here_zone else "exists (unplaced)"
        row.church_action = f"exists ({here_church.name})" if here_church else "exists (unplaced)"
        if here_zone is None or fold(here_zone.name) != fold(row.zona):
            row.notes.append("la zona del archivo no coincide con la del club: no se mueve")

        # The event is for Adventurers: the club must work with that ministry.
        _, current = await ministry_service.all_of_club(db, club)
        if adventurers.id in {m.id for m in current}:
            row.ministry_action = "ok"
        else:
            previous = [m.slug for m in current]
            wanted = [*current, adventurers]
            await ministry_service.set_club_ministries(db, club, wanted)
            club.updated_at = utcnow()
            record_audit(
                db,
                action="UPDATE",
                entity_type=placement.ORGANIZATION,
                entity_id=club.id,
                actor=actor,
                details="Added ministry adventurers (event roster import)",
                metadata={
                    "fields": ["ministries"],
                    "via": "import_event_roster",
                    "ministry": {"from": previous[0] if previous else None, "to": wanted[0].slug},
                    "ministries": {"from": previous, "to": [m.slug for m in wanted]},
                },
            )
            row.ministry_action = "add adventurers"

        directors = await _current_directors(db, club.id)
        pending = await _pending_invitations(db, club.id)
        if directors:
            if account is not None and account.id in directors:
                row.director_action = "already_director"
            else:
                raise RowProblem("club_has_another_director")
        elif any(inv.email.lower() == row.email for inv in pending):
            row.director_action = "invitation_pending"
            if account is not None:
                row.notes.append("ya tiene cuenta: basta con que acepte la invitación")
        elif pending:
            raise RowProblem("club_has_pending_invitation_for_another_email")
        elif account is not None:
            person, _ = await club_service._eligible_director(db, actor, row.email)
            await club_service._appoint_director(db, actor, club, person, None)
            row.director_action = "appoint"
        else:
            invited = await org_invitations.create(
                db, organization=club, actor=actor, role=CLUB_DIRECTOR, email=row.email
            )
            row.director_action = "invite"

    if invited is not None:
        invitation, token = invited
        row.invitation_id = str(invitation.id)
        row.invitation_expires = invitation.expires_at.date().isoformat()
        result.invitations.append(
            NewInvitation(row, club.name, invitation.email, invitation.expires_at, token)
        )
    await db.flush()


async def run(
    db,
    rows: list[RosterRow],
    *,
    association_ref: str,
    actor_email: str | None = None,
    commit: bool = False,
    send_emails: bool = False,
    send=None,
) -> RunResult:
    """Process `rows` (already through `validate_rows`) on the session `db`.

    Dry run: everything inside ONE transaction, a savepoint per row, rolled back at the
    end. Commit: the same savepoint per row, committed right after it (one transaction per
    club). `send(invitation)` is only called with `send_emails` and `commit`, after the
    club's commit; the default sends through `email_service` like the API does.
    """
    from fastapi import HTTPException
    from sqlalchemy import select

    from app.models import Ministry, Organization, User
    from app.services import notifications

    if send_emails and not commit:
        raise RosterError("--send-emails requiere --commit")
    association = await _association(db, association_ref)
    actor = await _actor(db, actor_email, association)
    adventurers = await db.scalar(select(Ministry).where(Ministry.slug == EVENT_MINISTRY))
    if adventurers is None:
        raise RosterError("No existe el ministerio `adventurers`")
    result = RunResult(
        association=f"{association.name} ({association.code or association.id})",
        actor=f"{actor.email} ({actor.role})",
        commit=commit,
        rows=rows,
    )
    # Kept apart: a rolled-back row expires the ORM instances and they are reloaded by id.
    association_id, actor_id, adventurers_id = association.id, actor.id, adventurers.id

    for row in rows:
        if row.problems:
            row.status = row.status or "error"
            continue
        before = len(result.invitations)
        zones, churches = len(result.zones_created), len(result.churches_created)
        try:
            async with db.begin_nested():
                await _process_row(
                    db, row, association=association, actor=actor, adventurers=adventurers, result=result
                )
                if send_emails and len(result.invitations) > before:
                    new = result.invitations[-1]
                    new.log_id = notifications.stage_log(
                        db,
                        kind=notifications.ORG_INVITATION,
                        email=new.email,
                        entity_type=notifications.ORG_INVITATION_ENTITY,
                        entity_id=row.invitation_id,
                    ).id
            if commit:
                await db.commit()
            row.status = "ok"
        except (RowProblem, HTTPException) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            row.problems.append(str(detail))
            row.status = "error"
        except Exception as exc:  # noqa: BLE001 — one bad club never stops the others
            row.problems.append(f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}")
            row.status = "failed"
        if row.status != "ok":
            del result.invitations[before:]
            del result.zones_created[zones:]
            del result.churches_created[churches:]
            row.club_id = row.invitation_id = row.invitation_expires = ""
            if commit:
                await db.rollback()
            # A rolled-back savepoint / transaction expires these: load them again.
            association = await db.get(Organization, association_id, populate_existing=True)
            actor = await db.get(User, actor_id, populate_existing=True)
            adventurers = await db.get(Ministry, adventurers_id, populate_existing=True)
        elif commit and send_emails and len(result.invitations) > before:
            ok = await (send or _send_invitation)(result.invitations[-1], actor)
            result.emails_sent += int(bool(ok))
            result.emails_failed += int(not ok)

    if not commit:
        await db.rollback()
        # Simulated ids point at nothing: only the ids of rows that already existed stay.
        for row in rows:
            if row.club_action == "create":
                row.club_id = ""
            row.invitation_id = row.invitation_expires = ""
    return result


async def _send_invitation(new: NewInvitation, actor) -> bool:
    """The e-mail of `org_invitations.queue_email`, awaited instead of queued."""
    from app.services import email as email_service
    from app.services import notifications, org_invitations

    try:
        sent = await email_service.send_org_invitation_email(
            new.email,
            new.club_name,
            CLUB_DIRECTOR,
            org_invitations.join_url(new.token),
            actor.name,
            new.expires_at.date().isoformat(),
        )
    except Exception:  # noqa: BLE001
        sent = False
    if not sent and new.log_id is not None:
        await notifications.record_failure(new.log_id)
    return bool(sent)


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
REPORT_FIELDS = (
    "line", "status", "club", "codigo", "iglesia", "zona", "ciudad", "director",
    "correo_director", "telefono", "club_action", "club_id", "zone_action", "church_action",
    "ministry_action", "director_account", "director_action", "invitation_id",
    "invitation_expires", "problems", "notes",
)


def write_report(path: pathlib.Path, rows: list[RosterRow]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(REPORT_FIELDS)
        for row in rows:
            writer.writerow(
                [
                    "; ".join(getattr(row, name)) if name in ("problems", "notes") else getattr(row, name)
                    for name in REPORT_FIELDS
                ]
            )


def write_join_urls(path: pathlib.Path, invitations: list[NewInvitation]) -> None:
    """One-time join URLs: SENSITIVE (whoever has one can take the director role)."""
    from app.services import org_invitations

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ("club", "director", "correo", "telefono", "vence", "join_url", "whatsapp_url")
        )
        for new in invitations:
            writer.writerow(
                (
                    new.club_name,
                    new.row.director,
                    new.email,
                    new.row.telefono,
                    new.expires_at.date().isoformat(),
                    org_invitations.join_url(new.token),
                    org_invitations.whatsapp_url(new.club_name, new.token),
                )
            )


def summary(result: RunResult, mapping_lines: list[str], database: str) -> str:
    rows = result.rows
    ok = [r for r in rows if r.status == "ok"]
    verb = "" if result.commit else " (simulado)"
    out = [
        f"Padrón de eventos — {result.association}",
        f"Modo: {'COMMIT' if result.commit else 'SIMULACRO: no se escribe nada'} · base {database}",
        f"Actor: {result.actor}",
        "Columnas:",
        *mapping_lines,
        "",
        f"Filas: {len(rows)} · correctas{verb} {len(ok)} · con problemas "
        f"{sum(r.status == 'error' for r in rows)} · repetidas {sum(r.status == 'skipped' for r in rows)}"
        f" · fallidas {sum(r.status == 'failed' for r in rows)}",
    ]

    def names(filtered):
        return [f"{r.club} ({r.iglesia})" for r in filtered]

    groups = [
        ("Clubes por crear", [r for r in ok if r.club_action == "create"]),
        ("Clubes ya existentes", [r for r in ok if r.club_action.startswith("exists")]),
        ("Clubes existentes a los que se añade `adventurers`", [r for r in ok if r.ministry_action == "add adventurers"]),
    ]
    for title, members in groups:
        out.append(f"{title}: {len(members)}")
        out.extend(f"  - {n}" for n in names(members))
    out.append(f"Zonas por crear: {len(result.zones_created)}")
    out.extend(f"  - {n}" for n in result.zones_created)
    out.append(f"Iglesias por crear: {len(result.churches_created)}")
    out.extend(f"  - {n}" for n in result.churches_created)
    zones_new = {fold(name) for name in result.zones_created}
    churches_new = {fold(name.rsplit(" (", 1)[0]) for name in result.churches_created}
    out.append(
        f"Zonas existentes reutilizadas: {len({fold(r.zona) for r in ok} - zones_new)} · "
        f"iglesias existentes reutilizadas: {len({fold(r.iglesia) for r in ok} - churches_new)}"
    )
    with_account = [r for r in rows if r.director_account == "yes"]
    without = [r for r in rows if r.director_account == "no"]
    out.append(f"Directores con cuenta: {len(with_account)} · sin cuenta: {len(without)}")
    for action, title in (
        ("appoint", "nombrados directamente"),
        ("invite", "invitaciones CLUB_DIRECTOR nuevas"),
        ("invitation_pending", "ya tenían invitación pendiente (no se duplica)"),
        ("already_director", "ya eran directores"),
    ):
        members = [r for r in ok if r.director_action == action]
        out.append(f"  {title}: {len(members)}")
        out.extend(f"    - {r.email} → {r.club}" for r in members)
    bad = [r for r in rows if r.status in ("error", "failed", "skipped")]
    if bad:
        out.append("Filas con problemas (NO se tocan):")
        for r in bad:
            out.append(f"  línea {r.line} · {r.club or '(sin club)'} · {r.status}: {'; '.join(r.problems)}")
    noted = [r for r in rows if r.notes]
    if noted:
        out.append("Notas:")
        out.extend(f"  línea {r.line} · {r.club}: {'; '.join(r.notes)}" for r in noted)
    if result.commit and (result.emails_sent or result.emails_failed):
        out.append(f"Correos enviados: {result.emails_sent} · fallidos: {result.emails_failed}")
    return "\n".join(out)


def _database_label() -> str:
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    return f"{url.host or 'socket'}/{url.database}"


async def _amain(args, rows, mapping_lines) -> int:
    from app.db import SessionLocal, engine

    source = pathlib.Path(args.file)
    try:
        async with SessionLocal() as db:
            result = await run(
                db,
                rows,
                association_ref=args.association,
                actor_email=args.actor_email,
                commit=args.commit,
                send_emails=args.send_emails,
            )
    finally:
        await engine.dispose()

    mode = "commit" if args.commit else "dry-run"
    report_path = source.with_name(f"{source.stem}.roster-{mode}.csv")
    write_report(report_path, rows)
    print(summary(result, mapping_lines, _database_label()))
    print(f"\nInforme por fila: {report_path}")
    if args.commit and result.invitations and not args.send_emails:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        urls_path = source.with_name(f"{source.stem}.join-urls-{stamp}.SENSITIVE.csv")
        write_join_urls(urls_path, result.invitations)
        from app.config import settings

        print(
            f"Enlaces de un solo uso de {len(result.invitations)} invitaciones: {urls_path} "
            f"(modo 600; SENSIBLE: no lo compartas ni lo subas). Base de los enlaces: {settings.frontend_url}"
        )
    elif result.invitations and not args.commit:
        print(f"Con --commit se crearían {len(result.invitations)} invitaciones (sin enviar correos).")
    return 0 if all(r.status == "ok" for r in rows) else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", help="CSV o XLSX con el padrón")
    parser.add_argument("--association", required=True, help="organizations.code (p.ej. NTAM) o id")
    parser.add_argument("--actor-email", help="MASTER_GC o ADMIN_ASSOCIATION que firma (auditoría)")
    parser.add_argument("--commit", action="store_true", help="escribir (sin esto es un simulacro)")
    parser.add_argument(
        "--send-emails", action="store_true",
        help="enviar los correos de invitación (requiere --commit y la aprobación del propietario)",
    )
    parser.add_argument("--sheet", help="hoja del XLSX (por defecto la primera)")
    parser.add_argument(
        "--map", action="append", default=[], metavar="CAMPO=ENCABEZADO",
        help="fuerza qué columna es cada campo, p.ej. --map correo_director='E-mail Dir.'",
    )
    args = parser.parse_args(argv)
    if args.send_emails and not args.commit:
        parser.error("--send-emails requiere --commit")
    if not os.environ.get("DATABASE_URL"):
        sys.exit("Define DATABASE_URL")
    overrides = {}
    for item in args.map:
        name, sep, header = item.partition("=")
        if not sep:
            parser.error(f"--map espera CAMPO=ENCABEZADO, no {item!r}")
        overrides[name.strip()] = header.strip()

    source = pathlib.Path(args.file)
    if not source.exists():
        sys.exit(f"No existe {source}")
    if source.suffix.lower() in (".xlsx", ".xlsm") and not xlsx_available():
        print("Aviso: openpyxl no está instalado: sólo se aceptan CSV.", file=sys.stderr)
    headers, body = read_table(source, args.sheet)
    mapping, mapping_lines = map_columns(headers, overrides)
    rows = parse_rows(body, mapping)
    validate_rows(rows)
    if args.send_emails:
        print("ATENCIÓN: se enviarán correos reales de invitación.", file=sys.stderr)
    return asyncio.run(_amain(args, rows, mapping_lines))


if __name__ == "__main__":
    sys.exit(main())
