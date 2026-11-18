"""Passkeys (WebAuthn): the ceremonies, the stored credentials and their rules.

Migration 032, docs/PASSKEYS.md. Everything here is off (and the 032 tables are
never touched) unless `settings.webauthn_enabled`.

What a passkey proves: every ceremony demands *user verification* (Face ID, a
fingerprint, the device PIN), so a passkey login is possession + biometrics in
one step. That is why its tokens carry the `mfa` claim (see `routers/passkeys`).

What it never needs: a per-account lockout. There is no secret to guess — a
forged signature is not a matter of attempts — so counting failures per account
would only hand out a way to lock somebody else out. The bounds are the per
address limit, the single-use challenge and the audit trail.
"""
import re
import time
import uuid
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, options_to_json_dict
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import settings
from app.models import User, WebauthnChallenge, WebauthnCredential
from app.security import MFA_REQUIRED_ROLES, RECENT_AUTH_SECONDS, utcnow

PURPOSE_REGISTER = "register"
PURPOSE_LOGIN = "login"
CHALLENGE_MINUTES = 5
MAX_PASSKEYS = 10
NAME_MAX = 60
DEFAULT_NAME = "Passkey"

# ES256 first (every platform authenticator); RS256 for Windows Hello on older TPMs.
ALGORITHMS = [
    COSEAlgorithmIdentifier.ECDSA_SHA_256,
    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
]

CHALLENGE_EXPIRED_DETAIL = "La verificación expiró. Vuelve a intentarlo."
NOT_RECOGNIZED_DETAIL = "No reconocemos esa passkey."
ALREADY_REGISTERED_DETAIL = "Esa passkey ya está registrada."
TOO_MANY_DETAIL = f"Puedes tener hasta {MAX_PASSKEYS} passkeys. Quita una para añadir otra."
REAUTH_DETAIL = "Por seguridad, vuelve a iniciar sesión para gestionar tus passkeys."
LAST_PASSKEY_DETAIL = (
    "Es tu único segundo factor: tu rol no puede quedarse sin él. Añade otra passkey o una "
    "aplicación de autenticación antes de quitar esta."
)

# Who made the authenticator, for the label in the profile. Community list:
# github.com/passkeydeveloper/passkey-authenticator-aaguids. Unknown = no label.
PROVIDERS = {
    "fbfc3007-154e-4ecc-8c0b-6e020557d7bd": "iCloud",  # Apple Passwords (iCloud Keychain)
    "dd4ec289-e01d-41c9-bb89-70fa845d4bf2": "iCloud",  # iCloud Keychain (managed)
    "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4": "Google",  # Google Password Manager
    "adce0002-35bc-c60a-648b-0b25f1f05503": "Chrome",  # Chrome on Mac
    "771b48fd-d3d4-4f74-9232-fc157ab0507a": "Edge",  # Edge on Mac
    "53414d53-554e-4700-0000-000000000000": "Samsung Pass",
    "08987058-cadc-4b81-b6e1-30de50dcbe96": "Windows Hello",
    "9ddd1817-af5a-4672-a2b9-3e3dd95000a9": "Windows Hello",
    "6028b017-b1d4-4c02-b4b3-afcdafc96bb2": "Windows Hello",
    "d3452668-01fd-4c12-926c-83a4204853aa": "Microsoft",  # Microsoft Password Manager
    "bada5566-a7aa-401f-bd96-45619a55120d": "1Password",
    "d548826e-79b4-db40-a3d8-11116f7e8349": "Bitwarden",
    "531126d6-e717-415c-9320-3d9aa6981239": "Dashlane",
    "50726f74-6f6e-5061-7373-50726f746f6e": "Proton Pass",
}


class CeremonyError(Exception):
    """A ceremony that did not verify. `reason` goes to the audit log, never to the client."""

    def __init__(self, reason: str, user: User | None = None):
        super().__init__(reason)
        self.reason = reason
        self.user = user


def require_enabled() -> None:
    """FastAPI dependency: without the configuration the passkey routes do not exist."""
    if not settings.webauthn_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


def provider_of(credential: WebauthnCredential) -> str | None:
    return PROVIDERS.get(str(credential.aaguid)) if credential.aaguid else None


def clean_name(name: str | None) -> str:
    return re.sub(r"\s+", " ", name or "").strip()[:NAME_MAX]


# ----------------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------------
async def count_for(db: AsyncSession, user_id: uuid.UUID) -> int:
    """How many passkeys the account holds. 0 (and no query) while the feature is off."""
    if not settings.webauthn_enabled:
        return 0
    stmt = select(func.count()).select_from(WebauthnCredential).where(WebauthnCredential.user_id == user_id)
    return int((await db.execute(stmt)).scalar_one())


async def list_for(db: AsyncSession, user_id: uuid.UUID) -> list[WebauthnCredential]:
    stmt = (
        select(WebauthnCredential)
        .where(WebauthnCredential.user_id == user_id)
        .order_by(WebauthnCredential.created_at, WebauthnCredential.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def second_factor_count(db: AsyncSession, user: User) -> int:
    """The passkeys that matter to the MFA policy: only an account whose ROLE obliges a second
    factor counts them (D2 of docs/PASSKEYS.md). Nobody else pays the query."""
    if user.role not in MFA_REQUIRED_ROLES:
        return 0
    return await count_for(db, user.id)


# ----------------------------------------------------------------------------
# Rules for adding and removing
# ----------------------------------------------------------------------------
def require_fresh_session(user: User, passkeys: int) -> None:
    """
    Adding or removing a passkey needs a recent login, and a session born of the second factor
    when the account has one: a stolen session must not be able to hang its own key on the
    account (or take the owner's away).
    """
    has_factor = user.mfa_enabled or (user.role in MFA_REQUIRED_ROLES and passkeys > 0)
    stale = time.time() - (getattr(user, "token_auth_time", 0) or 0) > RECENT_AUTH_SECONDS
    if stale or (has_factor and not getattr(user, "token_born_of_mfa", False)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "passkey_reauth_required", "message": REAUTH_DETAIL},
            headers={"X-Reauth-Required": "1"},
        )


def is_last_factor(user: User, passkeys: int) -> bool:
    """Would removing one passkey leave an obliged account without any second factor?"""
    return user.role in MFA_REQUIRED_ROLES and not user.mfa_enabled and passkeys <= 1


# ----------------------------------------------------------------------------
# Challenges: stored, short lived, single use
# ----------------------------------------------------------------------------
async def _new_challenge(
    db: AsyncSession, purpose: str, challenge: bytes, user_id: uuid.UUID | None
) -> WebauthnChallenge:
    now = utcnow()
    # Housekeeping rides on the write that is happening anyway (index on expires_at).
    await db.execute(delete(WebauthnChallenge).where(WebauthnChallenge.expires_at < now - timedelta(hours=1)))
    row = WebauthnChallenge(
        id=uuid.uuid4(),
        challenge=challenge,
        purpose=purpose,
        user_id=user_id,
        expires_at=now + timedelta(minutes=CHALLENGE_MINUTES),
    )
    db.add(row)
    await db.commit()
    return row


async def _claim_challenge(db: AsyncSession, challenge_id: uuid.UUID, purpose: str):
    """
    Spend a challenge. One UPDATE ... RETURNING, like `mfa.claim_recovery_code`: two requests
    cannot both use it. Committed at once, BEFORE anything is verified, so that a failed
    verification can never roll the claim back and leave the challenge reusable.
    """
    now = utcnow()
    stmt = (
        update(WebauthnChallenge)
        .where(
            WebauthnChallenge.id == challenge_id,
            WebauthnChallenge.purpose == purpose,
            WebauthnChallenge.used_at.is_(None),
            WebauthnChallenge.expires_at > now,
        )
        .values(used_at=now)
        .returning(WebauthnChallenge.challenge, WebauthnChallenge.user_id)
    )
    row = (await db.execute(stmt)).first()
    await db.commit()
    return row


def _transports(values) -> list[AuthenticatorTransport]:
    known = {t.value: t for t in AuthenticatorTransport}
    return [known[v] for v in (values or []) if v in known]


def _descriptors(credentials: list[WebauthnCredential]) -> list[PublicKeyCredentialDescriptor]:
    return [
        PublicKeyCredentialDescriptor(id=c.credential_id, transports=_transports(c.transports) or None)
        for c in credentials
    ]


def _reason(exc: Exception) -> str:
    """A short, stable word for the audit log out of the library's message."""
    text = str(exc).lower()
    for needle, reason in (
        ("origin", "origin"),
        ("rp id", "rp_id"),
        ("sign count", "counter"),
        ("user verification", "user_verification"),
        ("signature", "signature"),
        ("challenge", "challenge"),
    ):
        if needle in text:
            return reason
    return "malformed"


# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------
async def registration_options(db: AsyncSession, user: User) -> tuple[str, dict]:
    existing = await list_for(db, user.id)
    if len(existing) >= MAX_PASSKEYS:
        raise HTTPException(status.HTTP_409_CONFLICT, TOO_MANY_DETAIL)
    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.WEBAUTHN_RP_NAME,
        # The WebAuthn user handle: the account's UUID. Opaque, stable, no personal data.
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.name,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Discoverable: the passkey itself says whose it is, so login needs no e-mail.
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        # The same authenticator is not offered twice for one account.
        exclude_credentials=_descriptors(existing),
        supported_pub_key_algs=ALGORITHMS,
        timeout=CHALLENGE_MINUTES * 60 * 1000,
    )
    row = await _new_challenge(db, PURPOSE_REGISTER, options.challenge, user.id)
    return str(row.id), options_to_json_dict(options)


async def register(
    db: AsyncSession, user: User, challenge_id: uuid.UUID, credential: dict, name: str | None
) -> WebauthnCredential:
    """Verify the answer to `registration_options` and stage the credential. The caller commits."""
    claimed = await _claim_challenge(db, challenge_id, PURPOSE_REGISTER)
    if claimed is None or claimed.user_id != user.id:
        raise CeremonyError("challenge", user)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=claimed.challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origins,
            require_user_verification=True,
            supported_pub_key_algs=ALGORITHMS,
        )
    except Exception as exc:  # noqa: BLE001 - untrusted input: any parsing failure is a refusal
        raise CeremonyError(_reason(exc), user) from exc

    if await count_for(db, user.id) >= MAX_PASSKEYS:
        raise HTTPException(status.HTTP_409_CONFLICT, TOO_MANY_DETAIL)

    try:
        aaguid = uuid.UUID(verified.aaguid) if verified.aaguid else None
    except ValueError:
        aaguid = None
    response = credential.get("response") if isinstance(credential, dict) else None
    transports = response.get("transports") if isinstance(response, dict) else None
    row = WebauthnCredential(
        id=uuid.uuid4(),
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        aaguid=aaguid,
        transports=[t.value for t in _transports(transports if isinstance(transports, list) else [])],
        backup_eligible=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
        backed_up=verified.credential_backed_up,
        name=DEFAULT_NAME,
        created_at=utcnow(),
    )
    row.name = clean_name(name) or provider_of(row) or DEFAULT_NAME
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_REGISTERED_DETAIL) from exc
    return row


# ----------------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------------
async def authentication_options(db: AsyncSession, user: User | None) -> tuple[str, dict]:
    """
    Without `user` (passwordless login): discoverable credentials, the authenticator offers
    whatever passkeys it holds for this RP. With `user` (second step after the password):
    only that account's passkeys.
    """
    allow = None
    if user is not None:
        allow = _descriptors(await list_for(db, user.id))
        if not allow:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Esta cuenta no tiene passkeys.")
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
        timeout=CHALLENGE_MINUTES * 60 * 1000,
    )
    row = await _new_challenge(db, PURPOSE_LOGIN, options.challenge, user.id if user else None)
    return str(row.id), options_to_json_dict(options)


async def authenticate(db: AsyncSession, challenge_id: uuid.UUID, credential: dict) -> tuple[User, WebauthnCredential]:
    """
    Verify an assertion and return whose it is. Raises `CeremonyError` (with the account, when
    it could be told) for anything that does not verify. Stages the counter and `last_used_at`;
    the caller commits.
    """
    claimed = await _claim_challenge(db, challenge_id, PURPOSE_LOGIN)
    if claimed is None:
        raise CeremonyError("challenge")

    raw_id = credential.get("id") if isinstance(credential, dict) else None
    try:
        credential_id = base64url_to_bytes(raw_id) if isinstance(raw_id, str) and raw_id else b""
    except Exception as exc:  # noqa: BLE001
        raise CeremonyError("malformed") from exc
    stored = None
    if credential_id:
        stmt = select(WebauthnCredential).where(WebauthnCredential.credential_id == credential_id)
        stored = (await db.execute(stmt)).scalar_one_or_none()
    if stored is None:
        raise CeremonyError("unknown_credential")
    user = await db.get(User, stored.user_id)
    if user is None:
        raise CeremonyError("unknown_credential")
    # A second-step challenge belongs to the account whose password was just typed.
    if claimed.user_id is not None and claimed.user_id != user.id:
        raise CeremonyError("other_account", user)
    # A discoverable credential names its account (the user handle given at registration). When
    # the authenticator sends it, it must be the owner we found by the credential id.
    response = credential.get("response") if isinstance(credential, dict) else None
    handle = response.get("userHandle") if isinstance(response, dict) else None
    if isinstance(handle, str) and handle:
        try:
            claimed_handle = base64url_to_bytes(handle)
        except Exception as exc:  # noqa: BLE001
            raise CeremonyError("malformed", user) from exc
        if claimed_handle != user.id.bytes:
            raise CeremonyError("user_handle", user)

    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=claimed.challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origins,
            credential_public_key=stored.public_key,
            # Refuses a counter that did not advance (a cloned authenticator). Synced passkeys
            # report 0 on both sides and are not subject to it.
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001 - untrusted input: any parsing failure is a refusal
        raise CeremonyError(_reason(exc), user) from exc

    stored.sign_count = verified.new_sign_count
    stored.backed_up = verified.credential_backed_up
    stored.last_used_at = utcnow()
    return user, stored


async def delete_all(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Peer reset of the second factor: the passkeys go too (the device may be in other hands)."""
    if settings.webauthn_enabled:
        await db.execute(delete(WebauthnCredential).where(WebauthnCredential.user_id == user_id))
