"""Password hashing, JWT handling, TOTP and role constants."""
import base64
import hashlib
import io
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import anyio
import bcrypt
import jwt
import pyotp
from fastapi import HTTPException, status

from app.config import settings

# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------
MASTER_GC = "MASTER_GC"
ADMIN_DIVISION = "ADMIN_DIVISION"
ADMIN_UNION = "ADMIN_UNION"
ADMIN_ASSOCIATION = "ADMIN_ASSOCIATION"
COORDINATOR_ZONE = "COORDINATOR_ZONE"
CLUB_DIRECTOR = "CLUB_DIRECTOR"
CLUB_SECRETARY = "CLUB_SECRETARY"
INSTRUCTOR = "INSTRUCTOR"
COUNSELOR = "COUNSELOR"
STUDENT = "STUDENT"
PARENT_GUARDIAN = "PARENT_GUARDIAN"

ALL_ROLES = (
    MASTER_GC,
    ADMIN_DIVISION,
    ADMIN_UNION,
    ADMIN_ASSOCIATION,
    COORDINATOR_ZONE,
    CLUB_DIRECTOR,
    CLUB_SECRETARY,
    INSTRUCTOR,
    COUNSELOR,
    STUDENT,
    PARENT_GUARDIAN,
)

# Roles that only make sense as a member of a club: whoever holds one has (or
# had) a row in `club_memberships` and their `organization_id` is that club.
CLUB_LEVEL_ROLES = (CLUB_DIRECTOR, CLUB_SECRETARY, INSTRUCTOR, COUNSELOR, STUDENT)
# ...of those, the ones that exist ONLY inside a club: on the way out they fall
# back to STUDENT, while INSTRUCTOR and STUDENT belong to the person.
CLUB_SCOPED_ROLES = (CLUB_SECRETARY, COUNSELOR)

# Roles allowed to administer users and the organization tree.
ADMIN_ROLES = (MASTER_GC, ADMIN_DIVISION, ADMIN_UNION, ADMIN_ASSOCIATION, COORDINATOR_ZONE)

# Roles anyone may pick when signing up. Everything else is granted by an admin
# or by the club (CLUB_SECRETARY and COUNSELOR never appear here).
# CLUB_DIRECTOR is self-service too, but the club they create stays `pending`
# until a coordinator of its association approves it (see routers/org.py).
SELF_REGISTRATION_ROLES = (STUDENT, PARENT_GUARDIAN, INSTRUCTOR, CLUB_DIRECTOR)

# users.club_approval
CLUB_PENDING = "PENDING"
CLUB_APPROVED = "APPROVED"
CLUB_REJECTED = "REJECTED"

# Higher number = more authority. Used so an admin can never create or edit
# a peer or a superior.
ROLE_RANK = {
    MASTER_GC: 100,
    ADMIN_DIVISION: 90,
    ADMIN_UNION: 80,
    ADMIN_ASSOCIATION: 70,
    COORDINATOR_ZONE: 60,
    CLUB_DIRECTOR: 50,
    CLUB_SECRETARY: 45,
    INSTRUCTOR: 40,
    COUNSELOR: 30,
    PARENT_GUARDIAN: 20,
    STUDENT: 10,
}

# Roles that cannot operate without a second factor (spec E §5.8). Widening
# this tuple is the only change needed to demand MFA of another role.
MFA_REQUIRED_ROLES = (MASTER_GC,)

TOKEN_ACCESS = "access"
TOKEN_REFRESH = "refresh"
TOKEN_TEMP_MFA = "temp_mfa"
TEMP_MFA_TOKEN_MINUTES = 5
# Claim that says "this session was born of a second factor". Absent on every
# token minted before E1 and on tokens issued by plain password login.
MFA_CLAIM = "mfa"

AUTH_NOT_CONFIGURED_DETAIL = "Auth no configurado"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 8 characters and contain uppercase, lowercase and a number"
)


def validate_password_strength(password: str) -> None:
    """The single password policy, shared by register and reset-password."""
    valid = (
        len(password) >= 8
        and re.search(r"[A-Z]", password)
        and re.search(r"[a-z]", password)
        and re.search(r"\d", password)
    )
    if not valid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, PASSWORD_POLICY_MESSAGE)


def _password_bytes(password: str) -> bytes:
    # bcrypt only looks at the first 72 bytes. The legacy system relied on the
    # library truncating silently; newer bcrypt releases raise instead, so
    # truncate explicitly to keep every migrated hash verifiable.
    return password.encode("utf-8")[:72]


def hash_password_sync(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password_sync(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(password), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash in the database: treat as a failed login, not a 500.
        return False


# A real hash used to spend the same time on unknown emails as on known ones.
_DUMMY_HASH = hash_password_sync(secrets.token_urlsafe(16))


async def hash_password(password: str) -> str:
    return await anyio.to_thread.run_sync(hash_password_sync, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    """bcrypt is deliberately slow: keep it off the event loop."""
    if password_hash is None:
        await anyio.to_thread.run_sync(verify_password_sync, password, _DUMMY_HASH)
        return False
    return await anyio.to_thread.run_sync(verify_password_sync, password, password_hash)


# --------------------------------------------------------------------------
# JWT
# --------------------------------------------------------------------------
def require_auth_configured() -> None:
    """FastAPI dependency: answer 503 instead of crashing when JWT_SECRET is unset."""
    if not settings.auth_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUTH_NOT_CONFIGURED_DETAIL)


def _create_token(
    subject: str, token_type: str, lifetime: timedelta, *, mfa: bool = False
) -> str:
    require_auth_configured()
    claims = {"sub": str(subject), "exp": utcnow() + lifetime, "type": token_type}
    if mfa:
        # Only ever added, never set to False: an old token must stay readable.
        claims[MFA_CLAIM] = True
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id, *, mfa: bool = False) -> str:
    return _create_token(
        user_id, TOKEN_ACCESS, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), mfa=mfa
    )


def create_refresh_token(user_id, *, mfa: bool = False) -> str:
    return _create_token(
        user_id, TOKEN_REFRESH, timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS), mfa=mfa
    )


def create_temp_mfa_token(user_id) -> str:
    return _create_token(user_id, TOKEN_TEMP_MFA, timedelta(minutes=TEMP_MFA_TOKEN_MINUTES))


def decode_claims(token: str, expected_type: str) -> dict | None:
    """Return the token's claims, or None when the token is invalid for this use."""
    require_auth_configured()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload


def decode_token(token: str, expected_type: str) -> str | None:
    """Return the token subject, or None when the token is invalid for this use."""
    claims = decode_claims(token, expected_type)
    return claims.get("sub") if claims else None


def born_of_mfa(claims: dict | None) -> bool:
    return bool(claims and claims.get(MFA_CLAIM) is True)


# --------------------------------------------------------------------------
# MFA (TOTP)
# --------------------------------------------------------------------------
def generate_mfa_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=settings.MFA_ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def qr_code_data_url(uri: str) -> str:
    """PNG QR code as a data URL (same format the legacy API returned)."""
    import qrcode

    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(uri)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


# --------------------------------------------------------------------------
# One-time codes
# --------------------------------------------------------------------------
def generate_numeric_code(length: int = 6) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def generate_url_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    """The one hashing function for every URL-grade secret of this codebase
    (recovery codes, invitation and consent tokens). The secret itself is long
    and random, so a plain digest is enough: there is nothing to brute-force."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# No 0/O nor 1/I/L: these codes are read from a screen and typed back by hand.
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
RECOVERY_CODE_COUNT = 10
_RECOVERY_GROUP = 5


def generate_recovery_code() -> str:
    groups = [
        "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(_RECOVERY_GROUP))
        for _ in range(2)
    ]
    return "-".join(groups)


def normalize_recovery_code(code: str) -> str:
    """People type them in lower case, with spaces, or without the dash."""
    cleaned = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if len(cleaned) != _RECOVERY_GROUP * 2:
        return cleaned
    return f"{cleaned[:_RECOVERY_GROUP]}-{cleaned[_RECOVERY_GROUP:]}"


class FailedAttemptTracker:
    """
    Counts wrong one-time codes per key in process memory. A 6-digit code is
    only safe if guesses are bounded, and the per-IP rate limit alone does not
    bound guesses against a single account. When `record_failure` returns True
    the caller must invalidate that account's outstanding codes.
    """

    def __init__(self, max_failures: int = 5, window_seconds: int = 3600):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        # SEC-09: keys that reached `max_failures`, until when. Only callers that have no
        # code to invalidate (the TOTP of the second factor) need to ask `is_locked`.
        self._locked: dict[str, float] = {}

    def record_failure(self, key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._failures.get(key, []) if now - t < self.window_seconds]
        recent.append(now)
        if len(recent) >= self.max_failures:
            self._failures.pop(key, None)
            self._locked[key] = now + self.window_seconds
            if len(self._locked) > 10_000:
                self._locked = {k: t for k, t in self._locked.items() if t > now}
            return True
        self._failures[key] = recent
        if len(self._failures) > 10_000:
            self._failures.clear()
        return False

    def is_locked(self, key: str) -> bool:
        until = self._locked.get(key)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._locked.pop(key, None)
            return False
        return True

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked.pop(key, None)


code_attempts = FailedAttemptTracker()
