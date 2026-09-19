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
from app.security import TOKEN_ACCESS, decode_token, require_auth_configured

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


async def get_current_user(
    _: None = Depends(require_auth_configured),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise credentials_error()
    user = await load_user_by_subject(db, decode_token(credentials.credentials, TOKEN_ACCESS))
    if user is None:
        raise credentials_error()
    if user.status != "ACTIVE":
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Account is {user.status}")
    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """For public endpoints that show more to signed-in users. Never raises."""
    if credentials is None or not settings.auth_configured:
        return None
    user = await load_user_by_subject(db, decode_token(credentials.credentials, TOKEN_ACCESS))
    if user is None or user.status != "ACTIVE":
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
