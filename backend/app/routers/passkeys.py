"""Passkeys (WebAuthn): register, list, rename, remove and sign in (032, docs/PASSKEYS.md).

Two families of routes:

* `register/*`, the list, rename and delete act on the signed-in account;
* `login/*` are public and end in a token pair that carries the `mfa` claim: a passkey
  ceremony always verifies the user (biometrics or the device PIN), so the session it opens
  is born of a second factor. `login/verify` returns tokens: the web app's `/api/v1` proxy
  must deny it and reach it only from its session bridge, like `/auth/mfa/verify`.

Without `WEBAUTHN_RP_ID` + `WEBAUTHN_ORIGINS` every route but `config` answers 404.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_authenticated_user, get_current_user, load_user_by_subject
from app.models import User, WebauthnCredential
from app.rate_limit import account_or_ip, client_ip, limiter
from app.schemas.auth import (
    MessageResponse,
    PasskeyConfig,
    PasskeyLoginOptionsRequest,
    PasskeyLoginVerifyRequest,
    PasskeyOptionsResponse,
    PasskeyOut,
    PasskeyRegisterResponse,
    PasskeyRegisterVerifyRequest,
    PasskeyRename,
    TokenPairResponse,
)
from app.security import (
    TOKEN_TEMP_MFA,
    code_attempts,
    create_access_token,
    create_refresh_token,
    decode_token,
    require_auth_configured,
    utcnow,
)
from app.services import device_tracker
from app.services import email as email_service
from app.services import mfa as mfa_service
from app.services import passkeys as passkey_service
from app.services.audit import record_audit
from app.services.passkeys import CeremonyError

router = APIRouter(prefix="/api/v1/auth/passkeys", tags=["auth"])

# Everything but `config`: the feature must be configured (else 404) and so must auth (else 503).
ENABLED = [Depends(passkey_service.require_enabled), Depends(require_auth_configured)]

REGISTER_FAILED_DETAIL = "No fue posible registrar la passkey. Vuelve a intentarlo."


def _out(row: WebauthnCredential) -> PasskeyOut:
    return PasskeyOut(
        id=str(row.id),
        name=row.name,
        provider=passkey_service.provider_of(row),
        synced=row.backed_up,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


async def _own_passkey(db: AsyncSession, user: User, passkey_id: uuid.UUID) -> WebauthnCredential:
    row = await db.get(WebauthnCredential, passkey_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passkey no encontrada")
    return row


@router.get("/config", response_model=PasskeyConfig)
async def passkey_config():
    """Public: lets the web app decide whether to show any passkey control at all."""
    enabled = settings.webauthn_enabled and settings.auth_configured
    return PasskeyConfig(enabled=enabled, rp_id=settings.webauthn_rp_id if enabled else None)


# ----------------------------------------------------------------------------
# Register
# ----------------------------------------------------------------------------
@router.post("/register/options", response_model=PasskeyOptionsResponse, dependencies=ENABLED)
@limiter.limit(settings.RATE_LIMIT_PASSKEY_REGISTER, key_func=account_or_ip)
async def register_options(
    request: Request,
    # Not `get_current_user`: an account that still owes its second factor enrols here.
    current_user: User = Depends(get_authenticated_user),
    db: AsyncSession = Depends(get_db),
):
    passkey_service.require_fresh_session(
        current_user, await passkey_service.second_factor_count(db, current_user)
    )
    challenge_id, options = await passkey_service.registration_options(db, current_user)
    return PasskeyOptionsResponse(challenge_id=challenge_id, options=options)


@router.post("/register/verify", response_model=PasskeyRegisterResponse, dependencies=ENABLED)
@limiter.limit(settings.RATE_LIMIT_PASSKEY_REGISTER, key_func=account_or_ip)
async def register_verify(
    request: Request,
    payload: PasskeyRegisterVerifyRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_authenticated_user),
    db: AsyncSession = Depends(get_db),
):
    before = await passkey_service.second_factor_count(db, current_user)
    passkey_service.require_fresh_session(current_user, before)
    try:
        row = await passkey_service.register(
            db, current_user, payload.challenge_id, payload.credential, payload.name
        )
    except CeremonyError as exc:
        record_audit(
            db,
            action="PASSKEY_REGISTER_FAILED",
            entity_type="USER",
            entity_id=current_user.id,
            actor=current_user,
            metadata={"reason": exc.reason},
            request=request,
        )
        await db.commit()
        detail = (
            passkey_service.CHALLENGE_EXPIRED_DETAIL if exc.reason == "challenge" else REGISTER_FAILED_DETAIL
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail) from exc

    # The first second factor of an account whose role obliges one IS its enrolment: the
    # recovery codes are issued here, once, exactly as `/mfa/verify-setup` does for TOTP.
    enrolment = (
        mfa_service.mfa_required_for(current_user) and not current_user.mfa_enabled and before == 0
    )
    codes = await mfa_service.issue_recovery_codes(db, current_user) if enrolment else None
    record_audit(
        db,
        action="PASSKEY_REGISTER",
        entity_type="USER",
        entity_id=current_user.id,
        actor=current_user,
        metadata={
            "passkey_id": str(row.id),
            "name": row.name,
            "provider": passkey_service.provider_of(row),
            "synced": row.backed_up,
        },
        request=request,
    )
    if enrolment:
        record_audit(
            db,
            action="MFA_ENABLE",
            entity_type="USER",
            entity_id=current_user.id,
            actor=current_user,
            metadata={"method": "passkey"},
            request=request,
        )
    await db.commit()

    # Security notice, after the commit and never able to fail the request.
    background.add_task(
        email_service.send_passkey_added_email, current_user.email, current_user.name, row.name
    )
    return PasskeyRegisterResponse(
        passkey=_out(row),
        recovery_codes=codes,
        reauth_required=enrolment and settings.MASTER_MFA_ENFORCED,
    )


# ----------------------------------------------------------------------------
# List / rename / remove
# ----------------------------------------------------------------------------
@router.get("", response_model=list[PasskeyOut], dependencies=ENABLED)
async def list_passkeys(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return [_out(row) for row in await passkey_service.list_for(db, current_user.id)]


@router.patch("/{passkey_id}", response_model=PasskeyOut, dependencies=ENABLED)
async def rename_passkey(
    passkey_id: uuid.UUID,
    payload: PasskeyRename,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _own_passkey(db, current_user, passkey_id)
    name = passkey_service.clean_name(payload.name)
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Escribe un nombre para la passkey.")
    previous, row.name = row.name, name
    record_audit(
        db,
        action="PASSKEY_RENAME",
        entity_type="USER",
        entity_id=current_user.id,
        actor=current_user,
        metadata={"passkey_id": str(row.id), "from": previous, "to": name},
        request=request,
    )
    await db.commit()
    return _out(row)


@router.delete("/{passkey_id}", response_model=MessageResponse, dependencies=ENABLED)
async def delete_passkey(
    passkey_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _own_passkey(db, current_user, passkey_id)
    count = await passkey_service.second_factor_count(db, current_user)
    passkey_service.require_fresh_session(current_user, count)
    if passkey_service.is_last_factor(current_user, count):
        raise HTTPException(status.HTTP_409_CONFLICT, passkey_service.LAST_PASSKEY_DETAIL)
    name = row.name
    await db.delete(row)
    record_audit(
        db,
        action="PASSKEY_DELETE",
        entity_type="USER",
        entity_id=current_user.id,
        actor=current_user,
        metadata={"passkey_id": str(passkey_id), "name": name},
        request=request,
    )
    await db.commit()
    background.add_task(
        email_service.send_passkey_removed_email, current_user.email, current_user.name, name
    )
    return MessageResponse(message="Passkey eliminada")


# ----------------------------------------------------------------------------
# Sign in
# ----------------------------------------------------------------------------
@router.post("/login/options", response_model=PasskeyOptionsResponse, dependencies=ENABLED)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login_options(
    request: Request, payload: PasskeyLoginOptionsRequest, db: AsyncSession = Depends(get_db)
):
    """Passwordless (no body) or, with the temp token of `/auth/login`, the second step of a
    password login: then only that account's passkeys are offered and accepted."""
    user = None
    if payload.temp_token:
        subject = decode_token(payload.temp_token, TOKEN_TEMP_MFA)
        user = await load_user_by_subject(db, subject) if subject else None
        if user is None or user.status != "ACTIVE":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    challenge_id, options = await passkey_service.authentication_options(db, user)
    return PasskeyOptionsResponse(challenge_id=challenge_id, options=options)


@router.post("/login/verify", response_model=TokenPairResponse, dependencies=ENABLED)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login_verify(
    request: Request, payload: PasskeyLoginVerifyRequest, db: AsyncSession = Depends(get_db)
):
    try:
        user, _credential = await passkey_service.authenticate(db, payload.challenge_id, payload.credential)
    except CeremonyError as exc:
        # Nothing is staged when a ceremony fails (the claim of the challenge is already
        # committed), so the audit row is the only thing this commit writes.
        record_audit(
            db,
            action="PASSKEY_LOGIN_FAILED",
            entity_type="USER",
            entity_id=exc.user.id if exc.user else "unknown",
            # Why it failed and, when the credential was known, whose it is. Never the
            # credential itself.
            metadata={"reason": exc.reason} | ({"email": exc.user.email} if exc.user else {}),
            request=request,
        )
        await db.commit()
        if exc.reason == "challenge":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, passkey_service.CHALLENGE_EXPIRED_DETAIL
            ) from exc
        # One answer for an unknown passkey, a wrong signature and another account's passkey.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            passkey_service.NOT_RECOGNIZED_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if user.status != "ACTIVE":
        account_status = user.status
        await db.rollback()  # the counter and `last_used_at` staged by `authenticate`
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Account is {account_status}")

    user.last_login = utcnow()
    await db.commit()
    device_tracker.record_login_success(client_ip(request), user.email, user.name)
    # A passkey is a full sign-in: wrong codes typed before it no longer count.
    code_attempts.reset(f"mfa:{user.id}")
    return TokenPairResponse(
        access_token=create_access_token(user.id, mfa=True),
        refresh_token=create_refresh_token(user.id, mfa=True),
    )
