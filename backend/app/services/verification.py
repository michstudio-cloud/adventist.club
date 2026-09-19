"""One-time email tokens (email verification and password reset)."""
import uuid
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailVerification
from app.security import generate_numeric_code, generate_url_token, utcnow
from app.services.email import PASSWORD_RESET_EXPIRES_HOURS, VERIFICATION_EXPIRES_HOURS

EMAIL_VERIFICATION = "email_verification"
PASSWORD_RESET = "password_reset"

_LIFETIME_HOURS = {
    EMAIL_VERIFICATION: VERIFICATION_EXPIRES_HOURS,
    PASSWORD_RESET: PASSWORD_RESET_EXPIRES_HOURS,
}


def stage_token(db: AsyncSession, user_id: uuid.UUID, token_type: str) -> EmailVerification:
    """Add a fresh token to the session. The caller commits."""
    row = EmailVerification(
        id=uuid.uuid4(),
        user_id=user_id,
        token=generate_url_token(),
        code=generate_numeric_code(),
        type=token_type,
        used=False,
        expires_at=utcnow() + timedelta(hours=_LIFETIME_HOURS[token_type]),
    )
    db.add(row)
    return row


async def invalidate_tokens(db: AsyncSession, user_id: uuid.UUID, token_type: str) -> None:
    await db.execute(
        update(EmailVerification)
        .where(
            EmailVerification.user_id == user_id,
            EmailVerification.type == token_type,
            EmailVerification.used.is_(False),
        )
        .values(used=True, used_at=utcnow())
    )


async def _claim(db: AsyncSession, token_type: str, *conditions) -> uuid.UUID | None:
    """
    Atomically consume one unused, unexpired token and return its user id.
    A single UPDATE ... RETURNING: two concurrent requests cannot both win.
    """
    now = utcnow()
    stmt = (
        update(EmailVerification)
        .where(
            EmailVerification.type == token_type,
            EmailVerification.used.is_(False),
            EmailVerification.expires_at > now,
            *conditions,
        )
        .values(used=True, used_at=now)
        .returning(EmailVerification.user_id)
    )
    return (await db.execute(stmt)).scalars().first()


async def claim_by_token(db: AsyncSession, token: str, token_type: str) -> uuid.UUID | None:
    return await _claim(db, token_type, EmailVerification.token == token)


async def claim_by_code(
    db: AsyncSession, user_id: uuid.UUID, code: str, token_type: str
) -> uuid.UUID | None:
    return await _claim(
        db,
        token_type,
        EmailVerification.user_id == user_id,
        EmailVerification.code == code,
    )
