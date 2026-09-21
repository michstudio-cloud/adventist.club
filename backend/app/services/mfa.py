"""Second factor: the MFA policy and the recovery codes (spec E §5.8).

The policy is decided in ONE place (`policy_error`) and applied by
`app/deps.py`; nothing else in the code may re-implement it.
"""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import MfaRecoveryCode, User
from app.security import (
    MFA_REQUIRED_ROLES,
    RECOVERY_CODE_COUNT,
    generate_recovery_code,
    normalize_recovery_code,
    sha256_hex,
    utcnow,
)

ENROLLMENT_REQUIRED_DETAIL = "Debes activar la verificación en dos pasos para continuar."
REAUTH_REQUIRED_DETAIL = "Vuelve a iniciar sesión con tu código de verificación."


def mfa_required_for(user: User) -> bool:
    """Does this account's role oblige it to hold a second factor?"""
    return user.role in MFA_REQUIRED_ROLES


def enrollment_pending(user: User) -> bool:
    """True for an obliged account that has not enrolled yet. Reported at login
    so the client can walk the owner through it *before* enforcement is on."""
    return mfa_required_for(user) and not user.mfa_enabled


def policy_error(user: User, token_born_of_mfa: bool) -> HTTPException | None:
    """
    The single MFA gate. Returns the error to raise, or None when the request
    may proceed.

    While `MASTER_MFA_ENFORCED` is off nothing is refused: the code can be
    deployed before the owner enrols, which is the whole point of the switch.
    """
    if not settings.MASTER_MFA_ENFORCED or not mfa_required_for(user):
        return None
    if not user.mfa_enabled:
        # 403, not 401: the credentials are fine, the account is not.
        return HTTPException(status.HTTP_403_FORBIDDEN, ENROLLMENT_REQUIRED_DETAIL)
    if not token_born_of_mfa:
        # The session predates the second factor (or came from a bare password).
        return HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            REAUTH_REQUIRED_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return None


# ----------------------------------------------------------------------------
# Recovery codes. Shown once, stored only as SHA-256.
# ----------------------------------------------------------------------------
async def issue_recovery_codes(db: AsyncSession, user: User) -> list[str]:
    """Replace every outstanding code with a fresh set. The caller commits."""
    await db.execute(
        delete(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.used_at.is_(None)
        )
    )
    codes: list[str] = []
    while len(codes) < RECOVERY_CODE_COUNT:
        code = generate_recovery_code()
        if code not in codes:
            codes.append(code)
            db.add(
                MfaRecoveryCode(
                    id=uuid.uuid4(), user_id=user.id, code_hash=sha256_hex(code), created_at=utcnow()
                )
            )
    return codes


async def claim_recovery_code(db: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    """
    Consume one unused code. A single UPDATE ... RETURNING, like
    `verification._claim`: two concurrent logins cannot spend the same code.
    """
    code_hash = sha256_hex(normalize_recovery_code(code))
    stmt = (
        update(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user_id,
            MfaRecoveryCode.code_hash == code_hash,
            MfaRecoveryCode.used_at.is_(None),
        )
        .values(used_at=utcnow())
        .returning(MfaRecoveryCode.id)
    )
    return (await db.execute(stmt)).scalars().first() is not None


async def count_unused_codes(db: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(MfaRecoveryCode.id).where(
        MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)
    )
    return len((await db.execute(stmt)).scalars().all())


async def clear_second_factor(db: AsyncSession, user: User) -> None:
    """Take the second factor off an account (disable or peer reset). Every
    recovery code dies with it, used ones included: they prove nothing once the
    secret is gone and the account will get a new set on the next enrolment."""
    user.mfa_enabled = False
    user.mfa_secret = None
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
