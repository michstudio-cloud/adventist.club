"""Authentication: register, login, MFA, email verification, password reset."""
import logging
import uuid
from datetime import date

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_db, violated_constraint
from app.deps import get_current_user, load_user_by_subject
from app.models import EmailVerification, Organization, User
from app.rate_limit import limiter
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    MFACodeRequest,
    MFASetupResponse,
    MFAVerifyRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    TokenPairResponse,
    VerifyEmailCodeRequest,
    VerifyEmailTokenRequest,
)
from app.schemas.user import UserResponse
from app.security import (
    INSTRUCTOR,
    MASTER_GC,
    SELF_REGISTRATION_ROLES,
    STUDENT,
    TOKEN_REFRESH,
    TOKEN_TEMP_MFA,
    code_attempts,
    create_access_token,
    create_refresh_token,
    create_temp_mfa_token,
    decode_token,
    generate_mfa_secret,
    hash_password,
    qr_code_data_url,
    require_auth_configured,
    totp_provisioning_uri,
    utcnow,
    validate_password_strength,
    verify_password,
    verify_totp,
)
from app.services import email as email_service
from app.services import verification
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["auth"],
    dependencies=[Depends(require_auth_configured)],
)

FORGOT_PASSWORD_MESSAGE = "If the email exists, a password reset code has been sent"
INVALID_RESET_MESSAGE = "Invalid or expired reset code"
ADULT_AGE = 18


def _is_minor(birth_date: date | None, declared_minor: bool) -> bool:
    """A birth date under 18 makes the account a minor regardless of the checkbox."""
    if birth_date is None:
        return declared_minor
    today = utcnow().date()
    had_birthday = (today.month, today.day) >= (birth_date.month, birth_date.day)
    age = today.year - birth_date.year - (0 if had_birthday else 1)
    return declared_minor or age < ADULT_AGE


# ----------------------------------------------------------------------------
# Register / login / refresh / me
# ----------------------------------------------------------------------------
@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_REGISTER)
async def register(
    request: Request,
    payload: RegisterRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    validate_password_strength(payload.password)

    if payload.birth_date and payload.birth_date > utcnow().date():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "birth_date cannot be in the future")

    if payload.role not in SELF_REGISTRATION_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This role cannot be self-assigned. Register and ask an administrator to grant it.",
        )

    is_minor = _is_minor(payload.birth_date, payload.is_minor)
    if is_minor and payload.role != STUDENT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minors can only register as STUDENT")

    email = payload.email.strip().lower()
    existing = await db.execute(select(User.id).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    if payload.organization_id:
        org = await db.get(Organization, payload.organization_id)
        if org is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Organization node not found")

    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=await hash_password(payload.password),
        name=payload.name.strip(),
        role=payload.role,
        organization_id=payload.organization_id,
        is_minor=is_minor,
        birth_date=payload.birth_date,
        mfa_enabled=False,
        verification_status="PENDING",
        child_protection_completed=False,
        status="ACTIVE",
    )
    db.add(user)
    try:
        # The ORM has no relationship() metadata to order inserts by foreign
        # key, so the user row is flushed before the rows that reference it.
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) != "users_email_key":
            raise
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    # Still one transaction: user, verification token and audit row commit together.
    token_row = verification.stage_token(db, user.id, verification.EMAIL_VERIFICATION)
    record_audit(
        db,
        action="REGISTER",
        entity_type="USER",
        entity_id=user.id,
        actor=user,
        details=f"Self-registration as {user.role}",
        request=request,
    )
    await db.commit()

    # Only after the commit, and never able to fail the request.
    background.add_task(
        email_service.send_verification_email,
        user.email,
        user.name,
        token_row.code,
        token_row.token,
    )

    message = "User created successfully. "
    if user.is_minor:
        message += "Guardianship consent required. "
    if user.role == INSTRUCTOR:
        message += "Child protection certification required. "
    message += "Please check your email to verify your account."

    return RegisterResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        message=message,
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/login", response_model=LoginResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.email == payload.email.strip().lower())
    user = (await db.execute(stmt)).scalar_one_or_none()

    # Always run bcrypt, even for unknown emails, so timing reveals nothing.
    password_ok = await verify_password(payload.password, user.password_hash if user else None)
    if user is None or not password_ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.status != "ACTIVE":
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Account is {user.status}")

    if user.mfa_enabled:
        return LoginResponse(
            access_token="",
            refresh_token="",
            mfa_required=True,
            temp_token=create_temp_mfa_token(user.id),
        )

    user.last_login = utcnow()
    await db.commit()
    return LoginResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        mfa_required=False,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    subject = decode_token(payload.refresh_token, TOKEN_REFRESH)
    if subject is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    user = await load_user_by_subject(db, subject)
    if user is None or user.status != "ACTIVE":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return RefreshResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.from_model(current_user)


# ----------------------------------------------------------------------------
# MFA
# ----------------------------------------------------------------------------
@router.post("/mfa/setup", response_model=MFASetupResponse)
async def mfa_setup(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Generate a TOTP secret. MFA only becomes active after /mfa/verify-setup."""
    if current_user.mfa_enabled:
        # Replacing a live secret with a bare access token would let a stolen
        # session take over the second factor.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "MFA is already enabled. Disable it before a new setup."
        )
    secret = generate_mfa_secret()
    uri = totp_provisioning_uri(secret, current_user.email)
    qr_code_url = await anyio.to_thread.run_sync(qr_code_data_url, uri)

    current_user.mfa_secret = secret
    await db.commit()
    return MFASetupResponse(secret=secret, otpauth_uri=uri, qr_code_url=qr_code_url)


@router.post("/mfa/verify-setup", response_model=MessageResponse)
async def mfa_verify_setup(
    payload: MFACodeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.mfa_secret or current_user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA not set up")
    if not verify_totp(current_user.mfa_secret, payload.totp_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid TOTP code")

    current_user.mfa_enabled = True
    record_audit(
        db,
        action="MFA_ENABLE",
        entity_type="USER",
        entity_id=current_user.id,
        actor=current_user,
        request=request,
    )
    await db.commit()
    return MessageResponse(message="MFA enabled successfully")


@router.post("/mfa/verify", response_model=TokenPairResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def mfa_verify(
    request: Request, payload: MFAVerifyRequest, db: AsyncSession = Depends(get_db)
):
    """Second login step: exchange the temp token + TOTP code for real tokens."""
    subject = decode_token(payload.temp_token, TOKEN_TEMP_MFA)
    if subject is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await load_user_by_subject(db, subject)
    if user is None or user.status != "ACTIVE":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if not user.mfa_enabled or not user.mfa_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA not enabled")
    if not verify_totp(user.mfa_secret, payload.totp_code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid TOTP code")

    user.last_login = utcnow()
    await db.commit()
    return TokenPairResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/mfa/disable", response_model=MessageResponse)
async def mfa_disable(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == MASTER_GC:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "MASTER_GC role requires MFA enabled")
    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    record_audit(
        db,
        action="MFA_DISABLE",
        entity_type="USER",
        entity_id=current_user.id,
        actor=current_user,
        request=request,
    )
    await db.commit()
    return MessageResponse(message="MFA disabled successfully")


# ----------------------------------------------------------------------------
# Email verification
# ----------------------------------------------------------------------------
@router.post("/send-verification-email")
async def send_verification_email(
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.verification_status == "VERIFIED":
        return {"message": "Email already verified", "email": current_user.email}

    await verification.invalidate_tokens(db, current_user.id, verification.EMAIL_VERIFICATION)
    token_row = verification.stage_token(db, current_user.id, verification.EMAIL_VERIFICATION)
    await db.commit()

    background.add_task(
        email_service.send_verification_email,
        current_user.email,
        current_user.name,
        token_row.code,
        token_row.token,
    )
    return {"message": "Verification email sent successfully", "email": current_user.email}


async def _mark_verified(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Same transaction as the token claim. Deliberately does not touch
    `status`: verifying an email must not reactivate a suspended account."""
    user = await db.get(User, user_id)
    if user is not None:
        user.verification_status = "VERIFIED"
    return user


@router.post("/verify-email")
@limiter.limit("10/minute")
async def verify_email_with_code(
    request: Request,
    payload: VerifyEmailCodeRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    attempts_key = f"verify:{current_user.id}"
    claimed = await verification.claim_by_code(
        db, current_user.id, payload.code, verification.EMAIL_VERIFICATION
    )
    if claimed is None:
        if code_attempts.record_failure(attempts_key):
            await verification.invalidate_tokens(
                db, current_user.id, verification.EMAIL_VERIFICATION
            )
            await db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired verification code")

    code_attempts.reset(attempts_key)
    await _mark_verified(db, current_user.id)
    await db.commit()

    background.add_task(
        email_service.send_welcome_email, current_user.email, current_user.name, current_user.role
    )
    return {"message": "Email verified successfully", "status": "VERIFIED"}


async def _verify_email_by_token(token: str, background: BackgroundTasks, db: AsyncSession) -> dict:
    user_id = await verification.claim_by_token(db, token, verification.EMAIL_VERIFICATION)

    if user_id is None:
        # Idempotent for double clicks: an already-used token of a verified
        # user is a success, anything else is an error.
        stmt = (
            select(User)
            .join(EmailVerification, EmailVerification.user_id == User.id)
            .where(
                EmailVerification.token == token,
                EmailVerification.type == verification.EMAIL_VERIFICATION,
                EmailVerification.used.is_(True),
                User.verification_status == "VERIFIED",
            )
        )
        user = (await db.execute(stmt)).scalar_one_or_none()
        if user is not None:
            return {
                "message": "Email already verified. You can now login.",
                "email": user.email,
                "already_verified": True,
            }
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "El link de verificación es inválido o ha expirado. Solicita uno nuevo.",
        )

    user = await _mark_verified(db, user_id)
    await db.commit()

    background.add_task(email_service.send_welcome_email, user.email, user.name, user.role)
    return {
        "message": "Email verified successfully. You can now login.",
        "email": user.email,
        "already_verified": False,
    }


@router.post("/verify-email-link")
async def verify_email_link_post(
    payload: VerifyEmailTokenRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    The emailed link opens the front end, which posts the token here. The
    token is a secret, so it is only accepted in a JSON body, never in a URL
    (URLs end up in access logs, browser history and Referer headers).
    """
    return await _verify_email_by_token(payload.token, background, db)


# ----------------------------------------------------------------------------
# Password reset
# ----------------------------------------------------------------------------
async def _process_forgot_password(email: str) -> None:
    """
    Runs after the response has been sent, so the response time is identical
    whether or not the account exists. Own session, one transaction.
    """
    try:
        async with SessionLocal() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is None:
                return
            await verification.invalidate_tokens(db, user.id, verification.PASSWORD_RESET)
            token_row = verification.stage_token(db, user.id, verification.PASSWORD_RESET)
            await db.commit()
        code_attempts.reset(f"reset:{user.id}")
        await email_service.send_password_reset_email(
            user.email, user.name, token_row.code, token_row.token
        )
    except Exception:
        logger.exception("forgot-password processing failed")


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit(settings.RATE_LIMIT_FORGOT_PASSWORD)
async def forgot_password(
    request: Request, payload: ForgotPasswordRequest, background: BackgroundTasks
):
    background.add_task(_process_forgot_password, payload.email.strip().lower())
    return MessageResponse(message=FORGOT_PASSWORD_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("10/minute")
async def reset_password(
    request: Request, payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
):
    # Policy first: a weak password must not burn the one-time token.
    validate_password_strength(payload.new_password)

    if payload.token:
        user_id = await verification.claim_by_token(db, payload.token, verification.PASSWORD_RESET)
    else:
        stmt = select(User.id).where(User.email == payload.email.strip().lower())
        known_user_id = (await db.execute(stmt)).scalar_one_or_none()
        user_id = None
        if known_user_id is not None:
            user_id = await verification.claim_by_code(
                db, known_user_id, payload.code, verification.PASSWORD_RESET
            )
            if user_id is None and code_attempts.record_failure(f"reset:{known_user_id}"):
                # Too many wrong guesses: the outstanding code dies.
                await verification.invalidate_tokens(
                    db, known_user_id, verification.PASSWORD_RESET
                )
                await db.commit()

    user = await db.get(User, user_id) if user_id else None
    if user is None:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_RESET_MESSAGE)

    user.password_hash = await hash_password(payload.new_password)
    await verification.invalidate_tokens(db, user.id, verification.PASSWORD_RESET)
    record_audit(
        db,
        action="PASSWORD_RESET",
        entity_type="USER",
        entity_id=user.id,
        actor=user,
        request=request,
    )
    await db.commit()
    code_attempts.reset(f"reset:{user.id}")

    return MessageResponse(
        message="Password reset successfully. You can now login with your new password."
    )
