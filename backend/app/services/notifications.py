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

from sqlalchemy import select
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

MEMBERSHIP = "MEMBERSHIP"
INVITATION = "INVITATION"
ORGANIZATION = "ORGANIZATION"

# A minor's club must never become a way to send somebody mail.
CONSENT_RESENDS_PER_DAY = 3


async def count_recent(
    db: AsyncSession, *, kind: str, entity_id, within: timedelta = timedelta(days=1)
) -> int:
    stmt = select(NotificationLog.id).where(
        NotificationLog.kind == kind,
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
