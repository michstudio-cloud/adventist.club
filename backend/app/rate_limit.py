"""Rate limiting for the abuse-prone endpoints (docs/RATE_LIMITS.md).

Two layers on the credential endpoints:

* per client address — slowapi decorators keyed by `client_ip`;
* per account — `account_limit` keyed by the normalized e-mail address, so a venue NAT (or the
  web app's server, if the trusted address is missing) never makes people block each other, and
  rotating addresses never buys an attacker more guesses at one account.
"""
import hmac
import ipaddress
import logging
import math
import time

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.config import settings

logger = logging.getLogger(__name__)

# Set by the web app's server (Next.js) on its calls to the API. The address is believed only
# when the secret matches PROXY_SHARED_SECRET.
CLIENT_IP_HEADER = "x-proxy-client-ip"
PROXY_AUTH_HEADER = "x-proxy-auth"

_warned_bad_proxy_secret = False


def _parse_ip(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None


def trusted_forwarded_ip(request: Request) -> str | None:
    """The client address named by the web app's server, or None when it cannot be trusted
    (no secret configured, header missing, wrong secret, or not an IP address)."""
    global _warned_bad_proxy_secret
    secret = settings.proxy_shared_secret
    if not secret:
        return None
    supplied = request.headers.get(PROXY_AUTH_HEADER)
    if not supplied:
        return None
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        if not _warned_bad_proxy_secret:
            _warned_bad_proxy_secret = True
            logger.warning("X-Proxy-Auth does not match PROXY_SHARED_SECRET; ignoring X-Proxy-Client-IP")
        return None
    return _parse_ip(request.headers.get(CLIENT_IP_HEADER))


def client_ip(request: Request) -> str:
    """
    Client address for rate limiting and the audit log.

    1. The address the web app's server forwards, when it proves itself with the shared secret.
       Every login goes through that server, so without this all visitors share its address.
    2. Otherwise the first X-Forwarded-For hop (the hosting platform's proxy sits in front, so the
       socket peer is the proxy). That header is client-controlled beyond what the proxy appends,
       so this is abuse damping, not a security boundary: the per-account limits are.
    3. Otherwise the socket peer.
    """
    trusted = trusted_forwarded_ip(request)
    if trusted:
        return trusted
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


# ----------------------------------------------------------------------------
# Per-account limits (keyed by the normalized e-mail, whether or not the account exists, so the
# answer never says which addresses are registered).
# ----------------------------------------------------------------------------
_account_storage = MemoryStorage()
_account_limiter = FixedWindowRateLimiter(_account_storage)


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AccountLimit:
    """One per-account limit, e.g. `AccountLimit("login", settings.RATE_LIMIT_LOGIN_ACCOUNT)`."""

    def __init__(self, scope: str, value: str) -> None:
        self.scope = scope
        self.item: RateLimitItem = parse(value)

    def _key(self, email: str) -> tuple[str, str]:
        return (f"account:{self.scope}", normalize_email(email))

    def hit(self, email: str) -> None:
        """Count one attempt; 429 (with Retry-After) once the window is used up."""
        if not limiter.enabled:
            return
        key = self._key(email)
        if not _account_limiter.hit(self.item, *key):
            logger.warning("Per-account rate limit %s exceeded (%s)", self.item, self.scope)
            raise too_many_requests(self.retry_after(email))

    def clear(self, email: str) -> None:
        """Forget the attempts (the right password was given)."""
        _account_limiter.clear(self.item, *self._key(email))

    def retry_after(self, email: str) -> int:
        reset_time, _remaining = _account_limiter.get_window_stats(self.item, *self._key(email))
        return max(1, math.ceil(reset_time - time.time()))


def reset_account_limits() -> None:
    """Tests only."""
    _account_storage.reset()


# ----------------------------------------------------------------------------
# The 429 answer
# ----------------------------------------------------------------------------
def _wait_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} segundo{'s' if seconds != 1 else ''}"
    minutes = math.ceil(seconds / 60)
    return f"{minutes} minuto{'s' if minutes != 1 else ''}"


def too_many_requests_message(seconds: int) -> str:
    return f"Demasiados intentos. Espera {_wait_text(seconds)} y vuelve a intentarlo."


def too_many_requests(seconds: int) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        too_many_requests_message(seconds),
        headers={"Retry-After": str(seconds)},
    )


def _slowapi_retry_after(request: Request) -> int:
    current = getattr(request.state, "view_rate_limit", None)
    if current:
        try:
            item, args = current
            reset_time, _remaining = limiter.limiter.get_window_stats(item, *args)
            return max(1, math.ceil(reset_time - time.time()))
        except Exception:  # noqa: BLE001 - the answer must still be a clean 429
            logger.exception("Could not compute Retry-After")
    return 60


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    logger.warning("Rate limit exceeded on %s %s", request.method, request.url.path)
    seconds = _slowapi_retry_after(request)
    return JSONResponse(
        status_code=429,
        content={"detail": too_many_requests_message(seconds)},
        headers={"Retry-After": str(seconds)},
    )
