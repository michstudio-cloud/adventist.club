"""Map providers: the short-lived token MapKit JS (Apple Maps) needs.

Apple's private key stays on the server: the browser only ever sees a token signed
here that expires in minutes. The endpoint is public — the map on /clubs is public —
and the token is worthless outside the origins it is bound to, so there is nothing
to protect beyond the key itself. Without the Apple settings it answers 503 and the
frontend falls back to its other map.
"""
import time
from urllib.parse import urlsplit

import jwt
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/api/v1/maps", tags=["maps"])

NOT_CONFIGURED_DETAIL = "apple_maps_not_configured"
ALGORITHM = "ES256"


class AppleTokenOut(BaseModel):
    token: str
    expires_in: int


def _private_key_pem() -> str:
    """The .p8 contents; hosting dashboards often flatten it to one line with literal "\\n"."""
    raw = settings.APPLE_MAPKIT_PRIVATE_KEY or ""
    return raw.replace("\\n", "\n").strip() + "\n"


def allowed_origin(candidate: str | None) -> str | None:
    """The allowlisted origin `candidate` names (scheme://host[:port]), or None."""
    if not candidate:
        return None
    parts = urlsplit(candidate.strip())
    if parts.scheme not in ("https", "http") or not parts.netloc:
        return None
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    return origin if origin in settings.apple_mapkit_origins else None


def sign_apple_token(origin: str | None) -> tuple[str, int]:
    """A MapKit JS token: ES256, `kid` = key ID, `iss` = team ID, bound to `origin` when given."""
    ttl = max(60, settings.APPLE_MAPKIT_TOKEN_MINUTES * 60)
    now = int(time.time())
    claims: dict[str, object] = {"iss": settings.APPLE_MAPKIT_TEAM_ID, "iat": now, "exp": now + ttl}
    if origin:
        claims["origin"] = origin
    token = jwt.encode(claims, _private_key_pem(), algorithm=ALGORITHM, headers={"kid": settings.APPLE_MAPKIT_KEY_ID})
    return token, ttl


@router.get("/apple-token", response_model=AppleTokenOut)
async def apple_token(
    response: Response,
    origin: str | None = Query(default=None, max_length=200, description="The page's origin, to bind the token to it"),
):
    if not settings.apple_maps_configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED_DETAIL)
    try:
        token, ttl = sign_apple_token(allowed_origin(origin))
    except (ValueError, TypeError) as exc:  # an unreadable key is a deployment error, not a client one
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED_DETAIL) from exc
    response.headers["Cache-Control"] = "no-store"
    return AppleTokenOut(token=token, expires_in=ttl)
