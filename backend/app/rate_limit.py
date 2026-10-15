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
from collections import OrderedDict
from datetime import datetime, timezone

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


class KeyedLimit:
    """One in-process limit keyed by arbitrary parts (an address, an e-mail and an address...)."""

    def __init__(self, scope: str, value: str) -> None:
        self.scope = scope
        self.item: RateLimitItem = parse(value)

    def _key(self, *parts: str) -> tuple[str, ...]:
        return (f"account:{self.scope}", *parts)

    def _hit(self, *parts: str) -> None:
        if not limiter.enabled:
            return
        if not _account_limiter.hit(self.item, *self._key(*parts)):
            logger.warning("Per-account rate limit %s exceeded (%s)", self.item, self.scope)
            raise too_many_requests(self._retry_after(*parts))

    def _available(self, *parts: str) -> bool:
        return not limiter.enabled or _account_limiter.test(self.item, *self._key(*parts))

    def _count(self, *parts: str) -> tuple[int, float]:
        """(attempts in the current window, when the window ends as a unix time)."""
        reset_time, remaining = _account_limiter.get_window_stats(self.item, *self._key(*parts))
        return max(0, self.item.amount - remaining), reset_time

    def _clear(self, *parts: str) -> None:
        _account_limiter.clear(self.item, *self._key(*parts))

    def _retry_after(self, *parts: str) -> int:
        reset_time, _remaining = _account_limiter.get_window_stats(self.item, *self._key(*parts))
        return max(1, math.ceil(reset_time - time.time()))


class AccountLimit(KeyedLimit):
    """One per-account limit, e.g. `AccountLimit("login", settings.RATE_LIMIT_LOGIN_ACCOUNT)`."""

    def hit(self, email: str) -> None:
        """Count one attempt; 429 (with Retry-After) once the window is used up."""
        self._hit(normalize_email(email))

    def clear(self, email: str) -> None:
        """Forget the attempts (the right password was given)."""
        self._clear(normalize_email(email))

    def retry_after(self, email: str) -> int:
        return self._retry_after(normalize_email(email))

    def count(self, email: str) -> tuple[int, float]:
        return self._count(normalize_email(email))


class AccountDeviceLimit(KeyedLimit):
    """Per e-mail AND client address (event node, LOGIN_LOCKOUT_MODE=device)."""

    def hit(self, email: str, ip: str) -> None:
        self._hit(normalize_email(email), ip)

    def clear(self, email: str, ip: str) -> None:
        self._clear(normalize_email(email), ip)

    def count(self, email: str, ip: str) -> tuple[int, float]:
        return self._count(normalize_email(email), ip)


class DeviceLimit(KeyedLimit):
    """Per client address across all accounts: only failures count (`fail`), and `check` refuses
    once they are used up, so judges sharing an address (an access point with NAT) never lock
    each other out by logging in correctly."""

    def check(self, ip: str) -> None:
        if not self._available(ip):
            logger.warning("Per-device login limit %s exceeded", self.item)
            raise too_many_requests(self._retry_after(ip))

    def fail(self, ip: str) -> None:
        if limiter.enabled:
            _account_limiter.hit(self.item, *self._key(ip))

    def count(self, ip: str) -> tuple[int, float]:
        return self._count(ip)


LOGIN_ACCOUNT_LIMIT = AccountLimit("login", settings.RATE_LIMIT_LOGIN_ACCOUNT)
LOGIN_ACCOUNT_DEVICE_LIMIT = AccountDeviceLimit("login-device", settings.RATE_LIMIT_LOGIN_ACCOUNT_DEVICE)
LOGIN_DEVICE_LIMIT = DeviceLimit("login-any-account", settings.RATE_LIMIT_LOGIN_DEVICE)

# E-mail -> client addresses it was tried from, so the node's operator can list the locked
# accounts and unlock one with every per-device counter. Bounded (least recently tried go first).
_SEEN_EMAILS_CAP = 5000
_SEEN_ADDRESSES_PER_EMAIL = 200
_seen: "OrderedDict[str, OrderedDict[str, None]]" = OrderedDict()


def _remember(email: str, ip: str) -> None:
    addresses = _seen.pop(email, None) or OrderedDict()
    addresses.pop(ip, None)
    addresses[ip] = None
    while len(addresses) > _SEEN_ADDRESSES_PER_EMAIL:
        addresses.popitem(last=False)
    _seen[email] = addresses
    while len(_seen) > _SEEN_EMAILS_CAP:
        _seen.popitem(last=False)


def login_attempt(email: str, ip: str) -> None:
    """Before the password is checked: every attempt counts (race-free); 429 when limited.
    Production (account mode): only the per-account limit, exactly as before."""
    if not limiter.enabled:
        return
    email = normalize_email(email)
    _remember(email, ip)
    if settings.login_lockout_by_device:
        LOGIN_DEVICE_LIMIT.check(ip)
        LOGIN_ACCOUNT_DEVICE_LIMIT.hit(email, ip)
    LOGIN_ACCOUNT_LIMIT.hit(email)


def login_failed(email: str, ip: str) -> None:
    if settings.login_lockout_by_device:
        LOGIN_DEVICE_LIMIT.fail(ip)


def login_succeeded(email: str, ip: str) -> None:
    """The right password forgets the attempts at that account (from that device)."""
    LOGIN_ACCOUNT_LIMIT.clear(email)
    LOGIN_ACCOUNT_DEVICE_LIMIT.clear(email, ip)


def _until(reset_time: float) -> str:
    return datetime.fromtimestamp(reset_time, tz=timezone.utc).isoformat()


def locked_accounts() -> list[dict]:
    """Accounts that cannot log in right now from everywhere (global) or from some devices."""
    accounts = []
    for email, addresses in list(_seen.items()):
        attempts, reset_time = LOGIN_ACCOUNT_LIMIT.count(email)
        devices = []
        for ip in addresses:
            device_attempts, device_reset = LOGIN_ACCOUNT_DEVICE_LIMIT.count(email, ip)
            if device_attempts >= LOGIN_ACCOUNT_DEVICE_LIMIT.item.amount:
                devices.append({"ip": ip, "intentos": device_attempts,
                                "limite": LOGIN_ACCOUNT_DEVICE_LIMIT.item.amount, "hasta": _until(device_reset)})
        locked = attempts >= LOGIN_ACCOUNT_LIMIT.item.amount
        if locked or devices:
            accounts.append({
                "email": email,
                "bloqueo_global": locked,
                "intentos": attempts,
                "limite": LOGIN_ACCOUNT_LIMIT.item.amount,
                "hasta": _until(reset_time) if attempts else None,
                "dispositivos": devices,
            })
    accounts.sort(key=lambda a: (not a["bloqueo_global"], a["email"]))
    return accounts


def unlock_account(email: str) -> dict:
    """Forget every login counter of this e-mail (global and per device). The per-address limit
    across accounts stays: a device that scanned many e-mails is still waiting."""
    email = normalize_email(email)
    cleared = 0
    attempts, _reset = LOGIN_ACCOUNT_LIMIT.count(email)
    if attempts:
        cleared += 1
    LOGIN_ACCOUNT_LIMIT.clear(email)
    addresses = list(_seen.pop(email, {}) or {})
    for ip in addresses:
        device_attempts, _device_reset = LOGIN_ACCOUNT_DEVICE_LIMIT.count(email, ip)
        if device_attempts:
            cleared += 1
        LOGIN_ACCOUNT_DEVICE_LIMIT.clear(email, ip)
    return {"email": email, "contadores_borrados": cleared, "dispositivos": addresses}


def device_locked_until(ip: str) -> str | None:
    """When the per-address limit across accounts lets this address try again (device mode)."""
    if not settings.login_lockout_by_device:
        return None
    attempts, reset_time = LOGIN_DEVICE_LIMIT.count(ip)
    return _until(reset_time) if attempts >= LOGIN_DEVICE_LIMIT.item.amount else None


def reset_account_limits() -> None:
    """Tests only."""
    _account_storage.reset()
    _seen.clear()


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
