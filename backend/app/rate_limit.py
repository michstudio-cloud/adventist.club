"""slowapi rate limiting for the abuse-prone auth endpoints."""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.config import settings

logger = logging.getLogger(__name__)


def client_ip(request: Request) -> str:
    """
    Client address for rate limiting and the audit log.

    The service runs behind the hosting platform's proxy, so the socket peer
    is the proxy itself and every visitor would share one bucket. Use the
    first X-Forwarded-For hop when present. The header is client-controlled
    beyond what the proxy appends, so this is abuse damping, not a security
    boundary.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def account_or_ip(request: Request) -> str:
    """For an open endpoint that a signed-in person also reaches through the web app's proxy
    (021: the certificate assistant with a session). Behind the proxy every account would share
    the proxy's address, so a valid access token counts per account; anything else per address."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer ") and settings.auth_configured:
        from app.security import TOKEN_ACCESS, decode_claims

        claims = decode_claims(header[7:].strip(), TOKEN_ACCESS)
        if claims and claims.get("sub"):
            return f"account:{claims['sub']}"
    return client_ip(request)


# No default limits: only explicitly decorated endpoints are limited, so the
# existing public endpoints behave exactly as before.
limiter = Limiter(
    key_func=client_ip,
    storage_uri="memory://",
    enabled=settings.RATE_LIMIT_ENABLED,
)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    logger.warning("Rate limit exceeded on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=429,
        content={"detail": f"Demasiadas solicitudes. Límite: {exc.detail}"},
    )
