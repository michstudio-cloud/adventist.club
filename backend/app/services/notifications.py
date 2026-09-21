"""Who gets which transactional e-mail, and how often (spec E §5.10).

`services/email.py` renders and sends; this module decides. Every send leaves a
row in `notification_log` — kind, address and the row it is about, never the
body — which is what lets a daily cap exist without a scheduler and what stops
the same notice going out twice.

A send never fails the request that caused it: the router hands these to
`BackgroundTasks` after the commit, exactly as `org.py` already does.
"""
import logging
import uuid
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import NotificationLog
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

MEMBERSHIP = "MEMBERSHIP"
INVITATION = "INVITATION"
ORGANIZATION = "ORGANIZATION"
CHURCH_LETTER = "CHURCH_LETTER"
ENROLLMENT = "ENROLLMENT"
CERTIFICATE = "CERTIFICATE"

# A minor's club must never become a way to send somebody mail.
CONSENT_RESENDS_PER_DAY = 3
# At most one progress notice per enrollment in this window (spec §5.10).
PROGRESS_WINDOW = timedelta(hours=12)


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
    """
    if pending != 1:
        return
    for director in await memberships.club_directors(db, club.id):
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
    return f"{settings.frontend_url}/panel/cartas"


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
# ----------------------------------------------------------------------------
def portfolio_url(enrollment_id) -> str:
    return f"{settings.frontend_url}/portafolio/{enrollment_id}"


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
    """Stage the log rows in the CALLER's transaction and queue the sends for
    after its commit. Returns whether anything was queued at all.

    The member decides with `users.notify_progress`; their guardians are only
    written to when a certificate was issued, which is the moment a family
    actually wants to hear about. No e-mail ever carries evidence, an image or
    anything about another member.
    """
    if background is None or member is None:
        return False
    if not await _progress_allowed(db, enrollment.id, kind):
        return False

    recipients = []
    if member.notify_progress:
        recipients.append((member.id, member.email, member.name))
    if kind == PROGRESS_CERTIFIED:
        for guardian in await memberships.approved_guardians(db, member.id):
            if guardian.notify_progress:
                recipients.append((guardian.id, guardian.email, member.name))
    if not recipients:
        return False

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
    honor = await db.get(Honor, enrollment.honor_id)
    return enrollment, member, honor.name if honor is not None else "tu especialidad"


async def queue_review_outcome(
    db: AsyncSession, background, *, enrollment_id, verdict: str, note: str | None
) -> None:
    """After a verdict: «lista para certificar» when the enrollment just became
    READY, «observación» when a requirement came back INCOMPLETE. A plain
    COMPLETE that does not finish the honor sends nothing (spec §5.10)."""
    enrollment, member, honor_name = await _enrollment_context(db, enrollment_id)
    if enrollment is None or member is None:
        return
    if enrollment.status == "READY":
        kind, body = PROGRESS_READY, None
    elif verdict == "INCOMPLETE":
        kind, body = PROGRESS_INCOMPLETE, note
    else:
        return
    await queue_progress_notice(
        db, background, enrollment=enrollment, member=member, kind=kind,
        honor_name=honor_name, note=body,
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
