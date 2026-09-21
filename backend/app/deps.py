"""Authentication and authorization dependencies."""
import re
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User
from app.security import TOKEN_ACCESS, born_of_mfa, decode_claims, require_auth_configured
from app.services import mfa as mfa_service

bearer_scheme = HTTPBearer(auto_error=False)

_LEGACY_MONGO_ID = re.compile(r"^[0-9a-f]{24}$")


def credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def load_user_by_subject(db: AsyncSession, subject: str | None) -> User | None:
    """
    Token subjects are user UUIDs. Tokens issued by the legacy API before the
    cut-over carry the Mongo ObjectId instead; migrated users keep it in
    `legacy_mongo_id`, so those sessions survive the migration.
    """
    if not subject:
        return None
    try:
        return await db.get(User, uuid.UUID(subject))
    except ValueError:
        pass
    if _LEGACY_MONGO_ID.match(subject):
        stmt = select(User).where(User.legacy_mongo_id == subject)
        return (await db.execute(stmt)).scalar_one_or_none()
    return None


async def get_authenticated_user(
    _: None = Depends(require_auth_configured),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    A valid access token and an active account, and nothing else. Only the
    handful of endpoints that let an obliged account enrol its second factor
    depend on this directly; everything else uses `get_current_user`.
    """
    if credentials is None:
        raise credentials_error()
    claims = decode_claims(credentials.credentials, TOKEN_ACCESS)
    user = await load_user_by_subject(db, claims.get("sub") if claims else None)
    if user is None:
        raise credentials_error()
    if user.status != "ACTIVE":
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Account is {user.status}")
    # Transient, not a column: whether THIS session passed a second factor.
    user.token_born_of_mfa = born_of_mfa(claims)
    return user


async def get_current_user(user: User = Depends(get_authenticated_user)) -> User:
    """Authenticated *and* compliant with the MFA policy (spec E §5.8)."""
    error = mfa_service.policy_error(user, getattr(user, "token_born_of_mfa", False))
    if error is not None:
        raise error
    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """For public endpoints that show more to signed-in users. Never raises."""
    if credentials is None or not settings.auth_configured:
        return None
    claims = decode_claims(credentials.credentials, TOKEN_ACCESS)
    user = await load_user_by_subject(db, claims.get("sub") if claims else None)
    if user is None or user.status != "ACTIVE":
        return None
    # An account that owes a second factor is treated as anonymous here rather
    # than refused: these endpoints are public and must keep answering.
    if mfa_service.policy_error(user, born_of_mfa(claims)) is not None:
        return None
    return user


def require_roles(*roles: str):
    """Dependency factory: the current user must hold one of `roles`."""

    async def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Insufficient permissions. Required roles: {list(roles)}",
            )
        return current_user

    return checker
