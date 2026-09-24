"""Who gets which notice, through which channel, and how often (spec E §5.10).

Two channels, one factory:

* E-mail. `services/email.py` renders and sends; this module decides. Every send
  leaves a row in `notification_log` — kind, address and the row it is about,
  never the body — which is what lets a cap exist without a scheduler and what
  stops the same notice going out twice. A send never fails the request that
  caused it: the router hands these to `BackgroundTasks` after the commit,
  exactly as `org.py` already does.
* «Avisos», the in-app inbox (017_notifications.sql). Every event that writes to
  a person who has an account also leaves a row in `notifications`, staged in the
  caller's transaction by `stage_inbox`. The inbox is never capped and never
  silenced by `users.notify_progress` (that preference is about e-mail): instead,
  while a notice is unread, the next one of the same kind about the same entity
  updates it (`count` goes up), so ten submissions are ONE notice with «10».

Recipients follow the rules that were already here, and nothing new:
* progress of the member's own work (a requirement, hours, a class, XP) goes to
  the member — every account has its own address (`users.email` is NOT NULL) —
  and the e-mail only when `users.notify_progress` is on;
* a minor's approved guardians hear about what closes a story or is decided
  about the minor: a certificate or investiture, a membership decision, an
  annulled certificate. Never the day-to-day progress.
"""
import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Notification, NotificationLog
from app.security import utcnow
from app.services import email as email_service
from app.services import memberships

logger = logging.getLogger(__name__)

# kinds
CONSENT_REQUEST = "CONSENT_REQUEST"
CONSENT_RESEND = "CONSENT_RESEND"
CLUB_INVITATION = "CLUB_INVITATION"
PENDING_REQUESTS = "PENDING_REQUESTS"
MEMBERSHIP_DECISION = "MEMBERSHIP_DECISION"

LEADER_LETTER_SUBMITTED = "LEADER_LETTER_SUBMITTED"
LEADER_VERIFY_DECISION = "LEADER_VERIFY_DECISION"
LEADER_LETTER_EXPIRING = "LEADER_LETTER_EXPIRING"

PROGRESS_INCOMPLETE = "PROGRESS_INCOMPLETE"
PROGRESS_READY = "PROGRESS_READY"
PROGRESS_CERTIFIED = "PROGRESS_CERTIFIED"
PROGRESS_KINDS = (PROGRESS_INCOMPLETE, PROGRESS_READY, PROGRESS_CERTIFIED)

# Bloque D · I7: not a progress notice — a decision about a document with the person's name.
CERTIFICATE_REVOKED = "CERTIFICATE_REVOKED"

# «Avisos»: requirements the members of a club sent, waiting for its reviewers.
PENDING_REVIEWS = "PENDING_REVIEWS"
# A requirement signed COMPLETE that does not finish the enrollment: in-app only (see E9 below).
REQUIREMENT_APPROVED = "REQUIREMENT_APPROVED"
# Service hours or attendance approved (or recorded already approved by the director).
HOURS_APPROVED = "HOURS_APPROVED"
# Points the club's staff gave the member (Bloque G §4.1).
XP_AWARDED = "XP_AWARDED"

MEMBERSHIP = "MEMBERSHIP"
INVITATION = "INVITATION"
ORGANIZATION = "ORGANIZATION"
CHURCH_LETTER = "CHURCH_LETTER"
ENROLLMENT = "ENROLLMENT"
CERTIFICATE = "CERTIFICATE"
USER = "USER"

# A minor's club must never become a way to send somebody mail.
CONSENT_RESENDS_PER_DAY = 3
# At most one progress notice per enrollment in this window (spec §5.10).
PROGRESS_WINDOW = timedelta(hours=12)
# The same window for everything else this module caps: one «requisitos por revisar» per
# club, one «horas aprobadas» and one «puntos» per member.
NOTICE_WINDOW = timedelta(hours=12)


async def count_recent(
    db: AsyncSession, *, kind: str | tuple[str, ...], entity_id, within: timedelta = timedelta(days=1)
) -> int:
    kinds = (kind,) if isinstance(kind, str) else tuple(kind)
    stmt = select(NotificationLog.id).where(
        NotificationLog.kind.in_(kinds),
        NotificationLog.entity_id == str(entity_id),
        NotificationLog.sent_at > utcnow() - within,
    )
    return len((await db.execute(stmt)).scalars().all())


def stage_log(
    db: AsyncSession,
    *,
    kind: str,
    email: str,
    entity_type: str,
    entity_id,
    user_id: uuid.UUID | None = None,
    ok: bool = True,
) -> NotificationLog:
    """Add the record to the CALLER's transaction, so a send that is about to
    be queued is counted even if the mail provider later refuses it: the cap
    protects the recipient, not the delivery rate."""
    row = NotificationLog(
        id=uuid.uuid4(),
        user_id=user_id,
        email=email,
        kind=kind,
        entity_type=entity_type,
        entity_id=str(entity_id),
        sent_at=utcnow(),
        ok=ok,
    )
    db.add(row)
    return row


async def record_failure(log_id: uuid.UUID) -> None:
    """Mark a queued notification as not delivered. Runs after the response, in
    its own session, and swallows everything: bookkeeping never breaks a flow."""
    try:
        async with SessionLocal() as db:
            row = await db.get(NotificationLog, log_id)
            if row is not None:
                row.ok = False
                await db.commit()
    except Exception:
        logger.exception("could not mark notification %s as failed", log_id)


async def send_and_record(send, log_id: uuid.UUID, *args, **kwargs) -> None:
    """Background task wrapper: send, and note it when the provider says no."""
    try:
        sent = await send(*args, **kwargs)
    except Exception:
        logger.exception("notification %s raised", log_id)
        sent = False
    if not sent:
        await record_failure(log_id)


# ----------------------------------------------------------------------------
# «Avisos»: the in-app inbox (017_notifications.sql)
# ----------------------------------------------------------------------------
def _plural(count: int, one: str, other: str) -> str:
    return one if count == 1 else other


def _hours(value) -> str:
    number = float(value or 0)
    return f"{number:g}".replace(".", ",")


def _award_label(data: dict) -> str:
    return data.get("award") or ("tu clase" if data.get("type") == "program" else "tu especialidad")


def inbox_text(kind: str, data: dict, count: int) -> tuple[str, str | None]:
    """The Spanish title and body of a notice. The app paints its own, in the reader's
    language, from `kind` + `data`; this is what stays in the row as the fallback."""
    award = _award_label(data)
    who = data.get("member")
    program = data.get("type") == "program"
    if kind == PENDING_REVIEWS:
        n = int(data.get("pending") or count)
        what = _plural(n, "un requisito enviado", f"{n} requisitos enviados")
        return "Requisitos por revisar", f"{data.get('club') or 'Tu club'} tiene {what} esperando revisión."
    if kind == PENDING_REQUESTS:
        n = int(data.get("pending") or count)
        what = _plural(n, "una solicitud", f"{n} solicitudes")
        return "Solicitudes por revisar", f"{data.get('club') or 'Tu club'} tiene {what} de ingreso esperando tu decisión."
    if kind == REQUIREMENT_APPROVED:
        title = _plural(count, "Requisito aprobado", f"{count} requisitos aprobados")
        return title, f"En {award}."
    if kind == PROGRESS_INCOMPLETE:
        note = data.get("note")
        return "Tienes una observación", f"Corrige un requisito de {award}." + (f" «{note}»" if note else "")
    if kind == PROGRESS_READY:
        if program:
            return "¡Terminaste todos los requisitos!", f"{award} está lista para la investidura."
        return "¡Terminaste todos los requisitos!", f"{award} está lista para certificar."
    if kind == PROGRESS_CERTIFIED:
        if program:
            title = "¡Investidura!"
            body = f"{who} recibió la investidura de {award}." if who else f"Recibiste la investidura de {award}."
        else:
            title = "¡Especialidad certificada!"
            body = f"{who} ya tiene el certificado de {award}." if who else f"Ya puedes descargar el certificado de {award}."
        return title, body
    if kind == HOURS_APPROVED:
        parts = []
        if float(data.get("service") or 0):
            parts.append(f"{_hours(data['service'])} h de servicio")
        if float(data.get("attendance") or 0):
            meetings = int(float(data["attendance"]))
            parts.append(_plural(meetings, "1 asistencia", f"{meetings} asistencias"))
        return "Horas aprobadas", "Te aprobaron " + (" y ".join(parts) or "horas") + "."
    if kind == XP_AWARDED:
        points = int(data.get("points") or 0)
        club = data.get("club") or "tu club"
        return f"¡Ganaste {points} XP!", f"La dirección de {club} te otorgó {points} puntos."
    if kind == MEMBERSHIP_DECISION:
        club = data.get("club") or "el club"
        if data.get("approved"):
            return "¡Ya eres parte del club!", (f"{club} aceptó el ingreso de {who}." if who else f"{club} aceptó tu ingreso.")
        return "Novedades sobre tu membresía", (
            f"La membresía de {who} en {club} no sigue adelante por ahora." if who
            else f"Tu membresía en {club} no sigue adelante por ahora."
        )
    if kind == LEADER_LETTER_SUBMITTED:
        return "Carta de iglesia por validar", f"{data.get('applicant') or 'Un líder'} presentó la carta de su iglesia."
    if kind == LEADER_VERIFY_DECISION:
        status_ = data.get("status")
        if status_ == "AUTHORIZED":
            return "Tu carta fue validada", "Tu carta de la iglesia quedó autorizada."
        if status_ == "REVOKED":
            return "Se retiró tu validación", "La administración revocó la validación de tu carta de la iglesia."
        return "Tu carta no fue validada", "La administración revisó tu carta de la iglesia y por ahora no la aceptó."
    if kind == CERTIFICATE_REVOKED:
        folio = data.get("certificate_no") or ""
        return "Certificado anulado", f"El certificado {folio} de {award} ya no es válido.".replace("  ", " ")
    return "Aviso", None


async def stage_inbox(
    db: AsyncSession,
    *,
    user_id,
    kind: str,
    data: dict | None = None,
    link: str | None = None,
    entity_type: str | None = None,
    entity_id=None,
    count: int | None = None,
    add: tuple[str, ...] = (),
) -> Notification:
    """Put a notice in `user_id`'s inbox, in the CALLER's transaction.

    While a notice of the same kind about the same entity is unread, it is updated instead
    of piling up another row: `count` goes up by one (or takes `count` when the caller knows
    the total, like a queue length), the keys named in `add` are summed (hours, points) and
    it moves to the top. `link` is a path inside the app, never an external URL.
    """
    if link is not None and (not link.startswith("/") or link.startswith("//")):
        link = None
    payload = {key: value for key, value in (data or {}).items() if value is not None}
    entity = str(entity_id) if entity_id is not None else None
    existing = None
    if entity is not None:
        existing = (
            await db.execute(
                select(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.kind == kind,
                    Notification.entity_id == entity,
                    Notification.read_at.is_(None),
                )
                .order_by(Notification.created_at.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
    if existing is not None:
        merged = dict(existing.data or {})
        for key in add:
            payload[key] = round(float(merged.get(key) or 0) + float(payload.get(key) or 0), 1)
        merged.update(payload)
        existing.data = merged
        existing.count = count if count is not None else existing.count + 1
        existing.title, existing.body = inbox_text(kind, merged, existing.count)
        existing.link = link or existing.link
        existing.created_at = utcnow()
        return existing
    total = count if count is not None else 1
    title, body = inbox_text(kind, payload, total)
    row = Notification(
        id=uuid.uuid4(),
        user_id=user_id,
        kind=kind,
        title=title[:200],
        body=body,
        link=link,
        entity_type=entity_type,
        entity_id=entity,
        count=total,
        data=payload,
        created_at=utcnow(),
    )
    db.add(row)
    return row


async def _email_allowed(db: AsyncSession, *, kind, entity_id, within: timedelta = NOTICE_WINDOW) -> bool:
    """The 12-hour cap of this module: nothing of `kind` about `entity_id` sent inside the window."""
    return await count_recent(db, kind=kind, entity_id=entity_id, within=within) == 0


# ----------------------------------------------------------------------------
# Guardian consent (E3)
# ----------------------------------------------------------------------------
def consent_url(token: str) -> str:
    """Single purpose link: it opens the consent screen and nothing else."""
    return f"{settings.frontend_url}/consent?t={token}"


async def consent_recipients(db: AsyncSession, membership, member) -> list[str]:
    """The address the minor gave, or the guardians who already authorized them."""
    if membership.guardian_email:
        return [membership.guardian_email]
    guardians = await memberships.approved_guardians(db, member.id)
    return [guardian.email for guardian in guardians]


async def queue_consent_request(
    db: AsyncSession,
    background,
    *,
    membership,
    member,
    club,
    token: str,
    recipients: list[str],
    kind: str = CONSENT_REQUEST,
) -> None:
    """
    Stage the `notification_log` rows in the CALLER's transaction and queue the
    sends for after its commit, so a mail outage can never fail the request
    that caused it (the pattern `org.py` already uses).
    """
    for address in recipients:
        log = stage_log(
            db,
            kind=kind,
            email=address,
            entity_type=MEMBERSHIP,
            entity_id=membership.id,
        )
        background.add_task(
            send_and_record,
            email_service.send_consent_request_email,
            log.id,
            address,
            member.name,
            club.name,
            consent_url(token),
        )


# ----------------------------------------------------------------------------
# Join requests and decisions (E4)
# ----------------------------------------------------------------------------
def club_panel_url() -> str:
    return f"{settings.frontend_url}/panel/club"


async def queue_pending_requests_notice(
    db: AsyncSession, background, *, club, pending: int
) -> None:
    """
    Tell the club's directors there is something to decide, and ONLY when the
    queue went from nothing to something: a club with a steady trickle is not
    e-mailed on every request. The message carries a count and a link, never a
    name — some of the people waiting are minors (spec §5.10).

    The inbox follows the queue instead: one unread notice per director and club whose
    count is the length of the queue right now.
    """
    directors = await memberships.club_directors(db, club.id)
    for director in directors:
        await stage_inbox(
            db,
            user_id=director.id,
            kind=PENDING_REQUESTS,
            data={"club": club.name, "pending": int(pending)},
            count=max(int(pending), 1),
            link="/panel/club",
            entity_type=ORGANIZATION,
            entity_id=club.id,
        )
    if pending != 1:
        return
    for director in directors:
        log = stage_log(
            db,
            kind=PENDING_REQUESTS,
            email=director.email,
            entity_type=ORGANIZATION,
            entity_id=club.id,
            user_id=director.id,
        )
        background.add_task(
            send_and_record,
            email_service.send_pending_requests_email,
            log.id,
            director.email,
            director.name,
            club.name,
            pending,
            club_panel_url(),
        )


# ----------------------------------------------------------------------------
# The church letter of a club leader (E7)
# ----------------------------------------------------------------------------
def letters_queue_url() -> str:
    return f"{settings.admin_url}/admin/cartas"


async def letter_validators(db: AsyncSession, letter) -> list:
    """Whoever may decide on this letter: the coordinators of its zone and,
    when the club has no zone yet, the administration of the association.

    The list is built from the tree and then passed through the very rule that
    decides (`rbac.org_in_review_scope`), so nobody is written to who could not
    act on it anyway.
    """
    from app.models import Organization, User
    from app.rbac import org_in_review_scope
    from app.workflow import ZONE_REVIEWERS

    organization = await db.get(Organization, letter.organization_id)
    if organization is None or not organization.path:
        return []
    ancestors = select(Organization.id).where(Organization.path.op("@>")(organization.path))
    association_path = (
        await db.execute(
            select(Organization.path)
            .where(
                Organization.type == "association",
                Organization.path.op("@>")(organization.path),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    scopes = [User.organization_id.in_(ancestors)]
    if association_path:
        zones = select(Organization.id).where(
            Organization.type == "zone", Organization.path.op("<@")(association_path)
        )
        scopes.append(User.organization_id.in_(zones))
    candidates = (
        await db.execute(
            select(User).where(
                User.role.in_(ZONE_REVIEWERS),
                User.status == "ACTIVE",
                User.id != letter.user_id,
                or_(*scopes),
            )
        )
    ).scalars().all()
    return [
        person
        for person in candidates
        if await org_in_review_scope(db, person, letter.organization_id)
    ]


async def queue_letter_submitted(db: AsyncSession, background, *, letter, applicant) -> None:
    """A letter reached the queue. The message names the applicant — an adult
    presenting their own document — and never a minor."""
    if background is None:
        return
    for reviewer in await letter_validators(db, letter):
        await stage_inbox(
            db,
            user_id=reviewer.id,
            kind=LEADER_LETTER_SUBMITTED,
            data={"applicant": applicant.name},
            link="/review/letters",
            entity_type=CHURCH_LETTER,
            entity_id=letter.id,
        )
        log = stage_log(
            db,
            kind=LEADER_LETTER_SUBMITTED,
            email=reviewer.email,
            entity_type=CHURCH_LETTER,
            entity_id=letter.id,
            user_id=reviewer.id,
        )
        background.add_task(
            send_and_record,
            email_service.send_letter_submitted_email,
            log.id,
            reviewer.email,
            reviewer.name,
            applicant.name,
            letter.role_requested,
            letters_queue_url(),
        )


async def queue_letter_decision(db: AsyncSession, background, *, letter, applicant) -> None:
    """The leader is always told what happened to their letter: it decides
    whether they may be trusted with minors."""
    if background is None or applicant is None:
        return
    await stage_inbox(
        db,
        user_id=applicant.id,
        kind=LEADER_VERIFY_DECISION,
        data={"status": letter.status},
        link="/panel",
        entity_type=CHURCH_LETTER,
        entity_id=letter.id,
    )
    log = stage_log(
        db,
        kind=LEADER_VERIFY_DECISION,
        email=applicant.email,
        entity_type=CHURCH_LETTER,
        entity_id=letter.id,
        user_id=applicant.id,
    )
    background.add_task(
        send_and_record,
        email_service.send_letter_decision_email,
        log.id,
        applicant.email,
        applicant.name,
        letter.status,
        letter.valid_until.isoformat() if letter.valid_until else None,
        letter.decision_note,
    )


async def queue_membership_decision(
    db: AsyncSession,
    background,
    *,
    membership,
    member,
    club,
    approved: bool,
    reason: str | None = None,
) -> None:
    """The person is always told; a minor's guardians are told too, because the
    decision is about a minor in their care."""
    recipients = [(member.id, member.email, member.name)]
    if member.is_minor or member.birth_date is not None:
        for guardian in await memberships.approved_guardians(db, member.id):
            recipients.append((guardian.id, guardian.email, member.name))

    for user_id, address, name in recipients:
        await stage_inbox(
            db,
            user_id=user_id,
            kind=MEMBERSHIP_DECISION,
            data={
                "club": club.name,
                "approved": bool(approved),
                "member": member.name if user_id != member.id else None,
            },
            link="/panel",
            entity_type=MEMBERSHIP,
            entity_id=membership.id,
        )
        log = stage_log(
            db,
            kind=MEMBERSHIP_DECISION,
            email=address,
            entity_type=MEMBERSHIP,
            entity_id=membership.id,
            user_id=user_id,
        )
        background.add_task(
            send_and_record,
            email_service.send_membership_decision_email,
            log.id,
            address,
            name,
            club.name,
            approved,
            reason,
        )


# ----------------------------------------------------------------------------
# Portfolio progress (E9) — the e-mails block A deliberately left to E.
#
# Three moments and no more: a requirement came back INCOMPLETE with an
# observation, an enrollment is READY to certify, a certificate was issued. A
# plain COMPLETE sends nothing: it is the normal rhythm of the portfolio and
# would turn into noise.
#
# «Avisos» keeps that rule for e-mail and adds the inbox: every one of those
# three moments leaves a row, and so does a plain COMPLETE (REQUIREMENT_APPROVED,
# in-app only, one unread row per enrollment whose count goes up) — the inbox
# is where the normal rhythm of the portfolio belongs.
# ----------------------------------------------------------------------------
def portfolio_url(enrollment_id) -> str:
    return f"{settings.frontend_url}/portfolio/enrollments/{enrollment_id}"


def portfolio_path(enrollment_id) -> str:
    return f"/portfolio/enrollments/{enrollment_id}"


def award_type(enrollment) -> str:
    """`program` for a class or program (Bloque F), `honor` otherwise: only the wording changes."""
    return "program" if getattr(enrollment, "program_id", None) is not None else "honor"


async def _progress_allowed(db: AsyncSession, enrollment_id, kind: str) -> bool:
    """At most one progress notice per enrollment every 12 hours.

    A CERTIFIED notice is never held back: an enrollment is certified once and
    it is the one message of the three that closes the story.
    """
    if kind == PROGRESS_CERTIFIED:
        return True
    return (
        await count_recent(
            db, kind=PROGRESS_KINDS, entity_id=enrollment_id, within=PROGRESS_WINDOW
        )
        == 0
    )


async def queue_progress_notice(
    db: AsyncSession,
    background,
    *,
    enrollment,
    member,
    kind: str,
    honor_name: str,
    note: str | None = None,
) -> bool:
    """Stage the inbox rows and the log rows in the CALLER's transaction and queue
    the sends for after its commit. Returns whether an e-mail was queued at all.

    The member decides with `users.notify_progress`; their guardians are only
    written to when a certificate was issued, which is the moment a family
    actually wants to hear about. No e-mail ever carries evidence, an image or
    anything about another member.

    The inbox is not capped and not switched off: the member always finds the
    notice in «Avisos», and so do the guardians of a certificate.
    """
    if member is None:
        return False
    kind_of_award = award_type(enrollment)
    guardians = (
        await memberships.approved_guardians(db, member.id) if kind == PROGRESS_CERTIFIED else []
    )
    for user_id, on_behalf in [(member.id, None)] + [(g.id, member.name) for g in guardians]:
        await stage_inbox(
            db,
            user_id=user_id,
            kind=kind,
            data={"award": honor_name, "type": kind_of_award, "note": note, "member": on_behalf},
            link=portfolio_path(enrollment.id) if on_behalf is None else f"/portfolio/{member.id}",
            entity_type=ENROLLMENT,
            entity_id=enrollment.id,
        )

    if background is None:
        return False
    if not await _progress_allowed(db, enrollment.id, kind):
        return False

    recipients = []
    if member.notify_progress:
        recipients.append((member.id, member.email, member.name))
    for guardian in guardians:
        if guardian.notify_progress:
            recipients.append((guardian.id, guardian.email, member.name))
    if not recipients:
        return False

    # The wording of a class only when it is one, so the call of an honor is exactly E9's.
    extra = {"award_type": kind_of_award} if kind_of_award == "program" else {}
    for user_id, address, name in recipients:
        log = stage_log(
            db,
            kind=kind,
            email=address,
            entity_type=ENROLLMENT,
            entity_id=enrollment.id,
            user_id=user_id,
        )
        background.add_task(
            send_and_record,
            email_service.send_progress_email,
            log.id,
            address,
            name,
            kind,
            honor_name,
            note,
            portfolio_url(enrollment.id),
            **extra,
        )
    return True


async def _enrollment_context(db: AsyncSession, enrollment_id):
    """The three rows a progress notice needs. Imported lazily so block E keeps
    working on a database where `007_portfolio.sql` has not been applied."""
    from app.models import Honor, HonorEnrollment, User

    enrollment = await db.get(HonorEnrollment, enrollment_id)
    if enrollment is None:
        return None, None, ""
    member = await db.get(User, enrollment.user_id)
    honor = await db.get(Honor, enrollment.honor_id) if enrollment.honor_id else None
    if honor is not None:
        return enrollment, member, honor.name
    if enrollment.program_id is not None:
        # Bloque F: the same notice, naming the class or the program the member is doing.
        from app.services import curriculum

        award = await curriculum.award_for(db, enrollment)
        return enrollment, member, award.name
    return enrollment, member, "tu especialidad"


async def queue_requirements_approved(
    db: AsyncSession, *, enrollment, member, honor_name: str, signed: int = 1
) -> None:
    """A requirement (or a block of them) signed COMPLETE without finishing the enrollment:
    the inbox only, one unread notice per enrollment that counts them (E9: no e-mail)."""
    if member is None or signed <= 0:
        return
    existing = (
        await db.execute(
            select(Notification.count).where(
                Notification.user_id == member.id,
                Notification.kind == REQUIREMENT_APPROVED,
                Notification.entity_id == str(enrollment.id),
                Notification.read_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    await stage_inbox(
        db,
        user_id=member.id,
        kind=REQUIREMENT_APPROVED,
        data={"award": honor_name, "type": award_type(enrollment)},
        count=(existing or 0) + signed,
        link=portfolio_path(enrollment.id),
        entity_type=ENROLLMENT,
        entity_id=enrollment.id,
    )


async def queue_review_outcome(
    db: AsyncSession, background, *, enrollment_id, verdict: str, note: str | None
) -> None:
    """After a verdict: «lista para certificar» when the enrollment just became
    READY, «observación» when a requirement came back INCOMPLETE. A plain
    COMPLETE that does not finish the honor sends no e-mail (spec §5.10); it
    only counts in the member's inbox."""
    enrollment, member, honor_name = await _enrollment_context(db, enrollment_id)
    if enrollment is None or member is None:
        return
    if enrollment.status == "READY":
        kind, body = PROGRESS_READY, None
    elif verdict == "INCOMPLETE":
        kind, body = PROGRESS_INCOMPLETE, note
    elif verdict == "COMPLETE":
        await queue_requirements_approved(
            db, enrollment=enrollment, member=member, honor_name=honor_name
        )
        return
    else:
        return
    await queue_progress_notice(
        db, background, enrollment=enrollment, member=member, kind=kind,
        honor_name=honor_name, note=body,
    )


async def queue_block_signed(
    db: AsyncSession, background, *, signed_by_enrollment: dict, since
) -> None:
    """Bloque F · F3, «firma en bloque» of a class: per member card, the requirements just
    signed land in the inbox, and a card that became READY with them gets E9's «lista»
    notice (and its e-mail) — the same two outcomes a single verdict has."""
    for enrollment_id, signed in signed_by_enrollment.items():
        enrollment, member, honor_name = await _enrollment_context(db, enrollment_id)
        if enrollment is None or member is None:
            continue
        became_ready = (
            enrollment.status == "READY" and enrollment.ready_at is not None and enrollment.ready_at >= since
        )
        if became_ready:
            await queue_progress_notice(
                db, background, enrollment=enrollment, member=member, kind=PROGRESS_READY,
                honor_name=honor_name,
            )
        else:
            await queue_requirements_approved(
                db, enrollment=enrollment, member=member, honor_name=honor_name, signed=signed
            )


async def queue_ready_since(db: AsyncSession, background, *, user_ids, since) -> None:
    """Enrollments that became READY on their own during this request — a `HOURS`
    requirement completed by approved hours, a linked honor — get the same «lista para
    certificar» notice a verdict would have produced. `ready_at` is written by
    `portfolio.recompute_ready` with the request's clock, so `>= since` is exact."""
    from app.models import HonorEnrollment

    ids = [uid for uid in dict.fromkeys(user_ids) if uid is not None]
    if not ids:
        return
    rows = (
        await db.execute(
            select(HonorEnrollment.id).where(
                HonorEnrollment.user_id.in_(ids),
                HonorEnrollment.status == "READY",
                HonorEnrollment.ready_at >= since,
            )
        )
    ).scalars().all()
    for enrollment_id in rows:
        enrollment, member, honor_name = await _enrollment_context(db, enrollment_id)
        if enrollment is None or member is None:
            continue
        await queue_progress_notice(
            db, background, enrollment=enrollment, member=member, kind=PROGRESS_READY,
            honor_name=honor_name,
        )


async def queue_certificate_issued(db: AsyncSession, background, *, enrollment_id) -> None:
    """The one progress notice a family actually waits for, so it also reaches
    the guardians of a minor."""
    enrollment, member, honor_name = await _enrollment_context(db, enrollment_id)
    if enrollment is None or member is None:
        return
    await queue_progress_notice(
        db, background, enrollment=enrollment, member=member, kind=PROGRESS_CERTIFIED,
        honor_name=honor_name,
    )


# ----------------------------------------------------------------------------
# A certificate was annulled (Bloque D · I7, spec §5.5)
# ----------------------------------------------------------------------------
async def queue_certificate_revoked(
    db: AsyncSession, background, *, certificate, reason: str | None = None
) -> None:
    """The holder is always told, and the guardians of a minor with them.

    Not a progress notice: `users.notify_progress` switches off the rhythm of the
    portfolio, not a decision taken about a document that carries the person's name, so
    this one is never held back and never capped.

    The message says WHICH certificate and that it was annulled. It does not carry the
    reason, whoever decided it, the course or the enrollment: the reason is a judgement
    about a person, it may name a third party, and the screen the link opens shows it to
    whoever is entitled to read it. An e-mail is not a private channel.
    """
    from app.models import User

    if background is None or certificate.user_id is None:
        return
    member = await db.get(User, certificate.user_id)
    if member is None:
        return
    recipients = [(member.id, member.email, member.name)]
    if member.is_minor or member.birth_date is not None:
        for guardian in await memberships.approved_guardians(db, member.id):
            recipients.append((guardian.id, guardian.email, member.name))

    for user_id, address, name in recipients:
        await stage_inbox(
            db,
            user_id=user_id,
            kind=CERTIFICATE_REVOKED,
            data={
                "certificate_no": certificate.certificate_no,
                "award": certificate.honor_name_snapshot,
                "member": member.name if user_id != member.id else None,
            },
            link="/portfolio" if user_id == member.id else f"/portfolio/{member.id}",
            entity_type=CERTIFICATE,
            entity_id=certificate.id,
        )
        log = stage_log(
            db,
            kind=CERTIFICATE_REVOKED,
            email=address,
            entity_type=CERTIFICATE,
            entity_id=certificate.id,
            user_id=user_id,
        )
        background.add_task(
            send_and_record,
            email_service.send_certificate_revoked_email,
            log.id,
            address,
            name,
            certificate.certificate_no,
            certificate.honor_name_snapshot,
        )


# ----------------------------------------------------------------------------
# «Avisos» — requirements waiting for the club's reviewers
# ----------------------------------------------------------------------------
def review_queue_url() -> str:
    """The review queue of the administration (`/portafolio` on admin.adventist.club)."""
    return f"{settings.admin_url}/admin/portafolio"


async def club_pending_reviews(db: AsyncSession, club_id) -> int:
    """What the club's queue holds right now: requirements SENT by its members, in cards
    that are still open. The same rows `portfolio.review_queue` lists to its staff."""
    from app.models import HonorEnrollment, RequirementProgress, User

    stmt = (
        select(func.count())
        .select_from(RequirementProgress)
        .join(HonorEnrollment, HonorEnrollment.id == RequirementProgress.enrollment_id)
        .join(User, User.id == HonorEnrollment.user_id)
        .where(
            RequirementProgress.status == "SUBMITTED",
            HonorEnrollment.status == "IN_PROGRESS",
            User.organization_id == club_id,
        )
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def club_reviewers(db: AsyncSession, club, enrollment) -> list:
    """Who may give a verdict on THIS card among the club's staff: directors and verified
    instructors attached to the club, passed through the very rule that decides
    (`rbac.can_review`) — so nobody without a church letter hears about a minor's work and
    nobody is told about their own submission."""
    from app.models import User
    from app.rbac import CLUB_REVIEW_ROLES, can_review

    candidates = (
        await db.execute(
            select(User).where(
                User.organization_id == club.id,
                User.role.in_(CLUB_REVIEW_ROLES),
                User.status == "ACTIVE",
                User.id != enrollment.user_id,
            )
        )
    ).scalars().all()
    return [person for person in candidates if await can_review(db, person, enrollment)]


async def queue_pending_reviews(db: AsyncSession, background, *, enrollment_id) -> bool:
    """A member sent a requirement to review. Its club's reviewers get:

    * in the inbox, ONE unread notice per club whose count is the queue right now;
    * by e-mail, at most one message per club every 12 hours (`notification_log`, kind
      PENDING_REVIEWS, entity = the club), with the count and the link to the queue.

    Like the join requests of E4 it carries a count and never a name: some of the people
    in that queue are minors. Returns whether an e-mail was queued.
    """
    from app.models import HonorEnrollment, User
    from app.rbac import member_club

    enrollment = await db.get(HonorEnrollment, enrollment_id)
    if enrollment is None:
        return False
    member = await db.get(User, enrollment.user_id)
    club = await member_club(db, member)
    if club is None:
        return False
    reviewers = await club_reviewers(db, club, enrollment)
    if not reviewers:
        return False
    pending = await club_pending_reviews(db, club.id)
    if pending == 0:
        return False
    for reviewer in reviewers:
        await stage_inbox(
            db,
            user_id=reviewer.id,
            kind=PENDING_REVIEWS,
            data={"club": club.name, "pending": pending},
            count=pending,
            link="/panel",
            entity_type=ORGANIZATION,
            entity_id=club.id,
        )
    if background is None or not await _email_allowed(db, kind=PENDING_REVIEWS, entity_id=club.id):
        return False
    for reviewer in reviewers:
        log = stage_log(
            db,
            kind=PENDING_REVIEWS,
            email=reviewer.email,
            entity_type=ORGANIZATION,
            entity_id=club.id,
            user_id=reviewer.id,
        )
        background.add_task(
            send_and_record,
            email_service.send_pending_reviews_email,
            log.id,
            reviewer.email,
            reviewer.name,
            club.name,
            pending,
            review_queue_url(),
        )
    return True


# ----------------------------------------------------------------------------
# «Avisos» — service hours and attendance approved (Bloque F · F2)
# ----------------------------------------------------------------------------
def hours_url() -> str:
    return f"{settings.frontend_url}/portfolio/horas"


async def _queue_member_email(
    db: AsyncSession, background, *, kind: str, member, send, args: tuple
) -> bool:
    """The e-mail half of a member's progress notice: only when the member keeps
    `notify_progress` on, and at most one of `kind` per member every 12 hours."""
    if background is None or member is None or not member.notify_progress:
        return False
    if not await _email_allowed(db, kind=kind, entity_id=member.id):
        return False
    log = stage_log(
        db, kind=kind, email=member.email, entity_type=USER, entity_id=member.id, user_id=member.id
    )
    background.add_task(send_and_record, send, log.id, member.email, member.name, *args)
    return True


async def queue_hours_approved(db: AsyncSession, background, *, log_ids) -> None:
    """Hours approved by the director — one by one from the queue, or an outing recorded
    already approved for several members. Per member: the inbox (service hours and
    meetings summed while unread) and, capped, an e-mail with what was approved now.

    What the hours were and where is NOT in the notice: it says where a minor was, and it
    stays in the portfolio behind a session."""
    from app.models import ActivityLog, User

    ids = list(dict.fromkeys(log_ids))
    if not ids:
        return
    logs = (
        await db.execute(
            select(ActivityLog).where(ActivityLog.id.in_(ids), ActivityLog.status == "APPROVED")
        )
    ).scalars().all()
    totals: dict = {}
    for log in logs:
        service, attendance = totals.get(log.user_id, (0.0, 0.0))
        if log.category == "SERVICE":
            service += float(log.quantity)
        else:
            attendance += float(log.quantity)
        totals[log.user_id] = (service, attendance)
    for user_id, (service, attendance) in totals.items():
        member = await db.get(User, user_id)
        if member is None:
            continue
        await stage_inbox(
            db,
            user_id=member.id,
            kind=HOURS_APPROVED,
            data={"service": round(service, 1), "attendance": round(attendance, 1)},
            add=("service", "attendance"),
            link="/portfolio/horas",
            entity_type=USER,
            entity_id=member.id,
        )
        await _queue_member_email(
            db, background, kind=HOURS_APPROVED, member=member,
            send=email_service.send_hours_approved_email,
            args=(round(service, 1), round(attendance, 1), hours_url()),
        )


# ----------------------------------------------------------------------------
# «Avisos» — points the club's staff gave the member (Bloque G §4.1)
# ----------------------------------------------------------------------------
def profile_url() -> str:
    return f"{settings.frontend_url}/profile"


async def queue_xp_awarded(db: AsyncSession, background, *, award, member, club) -> None:
    """Only a POSITIVE award is announced. A negative one is a correction or a conduct
    penalty: it is visible in the member's own history (`/profiles/me/xp`), and a message
    about it — to a minor's inbox that a family may share — is a decision for the club,
    not for an automatic notice. The note is never copied: it is a judgement about a
    person."""
    if award is None or member is None or int(award.points) <= 0:
        return
    await stage_inbox(
        db,
        user_id=member.id,
        kind=XP_AWARDED,
        data={"points": int(award.points), "club": club.name, "category": award.category},
        add=("points",),
        link="/profile",
        entity_type=USER,
        entity_id=member.id,
    )
    await _queue_member_email(
        db, background, kind=XP_AWARDED, member=member,
        send=email_service.send_xp_awarded_email,
        args=(int(award.points), club.name, award.category, profile_url()),
    )
