"""User management and guardianships."""
import uuid

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.certificates import signatures as signature_rules
from app.config import settings
from app.db import get_db, violated_constraint
from app.deps import get_authenticated_user, get_current_user
from app.models import Guardianship, Organization, User
from app.people import age_in_years, is_minor_user
from app.rbac import (
    ADMIN_ROLES,
    CLUB_REVIEW_ROLES,
    CONSENT_GRANTED,
    profile_is_minor,
    club_staff_in_good_standing,
    member_club,
    can_manage_user,
    can_view_user,
    director_blocked,
    get_org_path,
    is_admin_role,
    is_master,
    may_handle_minors,
    org_in_user_scope,
    outranks,
)
from app.schemas.auth import MFAResetRequest, RoleName
from app.schemas.membership import as_club_ref
from app.schemas.ministry import PreferencesUpdate
from app.schemas.profile import MyProfile, ProfileUpdate
from app.schemas.user import (
    ChildGuardianship,
    GuardianshipCreate,
    GuardianshipResponse,
    MeResponse,
    OnboardingUpdate,
    UserResponse,
    UserUpdate,
)
from app.security import INSTRUCTOR, MASTER_GC, PARENT_GUARDIAN, STUDENT, utcnow
from app.services import email as email_service
from app.services import memberships as membership_service
from app.services import ministry_context
from app.services import mfa as mfa_service
from app.services import profiles as profile_service
from app.services import storage
from app.services.audit import record_audit

router = APIRouter(prefix="/api/v1/users", tags=["users"])

SELF_EDITABLE_FIELDS = {"name", "avatar_url", "notify_progress"}
ADMIN_ONLY_FIELDS = {
    "role",
    "organization_id",
    "status",
    "verification_status",
    "birth_date",
    "is_minor",
}
# What an administrator may change on their own account. The same limits apply
# as for anyone they manage: never a role at or above their own, never an
# organization outside their subtree. Nobody else can touch these on themselves.
SELF_ADMIN_FIELDS = {"role", "organization_id", "status"}
# Columns that may be set to NULL through the API.
NULLABLE_FIELDS = {"avatar_url", "organization_id", "birth_date"}


async def _get_user_or_404(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/me", response_model=MeResponse)
async def get_my_profile(
    current_user: User = Depends(get_authenticated_user),
    db: AsyncSession = Depends(get_db),
):
    """Like `GET /auth/me`, readable while the MFA enrolment is still pending."""
    return await ministry_context.me_response(db, current_user)


@router.patch("/me/preferences", response_model=MeResponse)
async def update_my_preferences(
    payload: PreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """024 · The shell's selector: the active ministry (`ministry`: a slug of
    `ministries_available`) and club (`club_id`: one of `clubs_available`). Only the fields
    present change; `null` goes back to the default. Anything not offered is 422
    (`ministry_not_available` / `club_not_available`). A preference, never a permission."""
    await ministry_context.update_preferences(db, current_user, payload)
    return await ministry_context.me_response(db, current_user)


@router.patch("/me/profile", response_model=MyProfile)
async def update_my_profile(
    payload: ProfileUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bloque G: name, @handle, bio, photo, cover and visibility of one's own profile.
    A minor can never be `public` nor have a cover (spec §1)."""
    return await profile_service.update_profile(db, current_user, payload, request)


@router.patch("/me/onboarding", response_model=UserResponse)
async def complete_my_onboarding(
    request: Request,
    payload: OnboardingUpdate | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """«Primer uso guiado» (018): the person finished or skipped `/bienvenida`, so the app
    stops offering it. Idempotent: the first moment is kept, a second call changes nothing and
    writes no second audit row. Any role may call it; which roles SEE the guide is the app's call."""
    payload = payload or OnboardingUpdate()
    if current_user.onboarding_completed_at is None:
        current_user.onboarding_completed_at = utcnow()
        record_audit(
            db,
            action="ONBOARDING_COMPLETED" if payload.outcome == "completed" else "ONBOARDING_SKIPPED",
            entity_type="USER",
            entity_id=current_user.id,
            actor=current_user,
            metadata={"step": payload.step} if payload.step is not None else None,
            request=request,
        )
        await db.commit()
        await db.refresh(current_user)
    return UserResponse.from_model(current_user, own=True)


@router.post("/me/signature", response_model=UserResponse)
async def save_my_signature(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """020: save (or replace) one's handwritten signature for the certificate assistant.

    Same R2 path as `POST /media/upload` (`storage.upload_bytes`), but its own folder
    (`signatures/`, not open to the generic upload) and its own rules: PNG, JPEG or WebP up to
    400 KB, re-encoded to a PNG (transparency kept) by `app/certificates/signatures.py`.
    Public bucket with an unguessable key, returned only to its owner (docs/CERTIFICADOS_V4.md
    «Firmas» explains why not the private bucket). A minor never saves a signature."""
    guardians = await profile_service.guardianships_of(db, current_user.id)
    if profile_is_minor(current_user, has_guardian=bool(guardians)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "minor_cannot_save_signature")
    if not settings.storage_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Almacenamiento no configurado")
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in signature_rules.MIME_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "signature_images_only")
    limit = signature_rules.MAX_SIGNATURE_BYTES
    if file.size is not None and file.size > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "signature_too_large")
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "signature_too_large")
    try:
        png = await anyio.to_thread.run_sync(signature_rules.normalize_signature_bytes, data, content_type)
    except signature_rules.SignatureError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    try:
        url, _key = await storage.upload_bytes(png, "image/png", "signatures")
    except storage.StorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Almacenamiento no configurado")
    except storage.StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    previous = storage.key_from_public_url(current_user.signature_url)
    current_user.signature_url = url
    record_audit(db, action="SIGNATURE_SAVED", entity_type="USER", entity_id=current_user.id,
                 actor=current_user, request=request)
    await db.commit()
    await db.refresh(current_user)
    if previous and previous.startswith("signatures/"):
        await storage.delete_quietly(previous)     # a replaced signature does not linger
    return UserResponse.from_model(current_user, own=True)


@router.delete("/me/signature", response_model=UserResponse)
async def delete_my_signature(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Forget the saved signature (idempotent). The object is deleted from the bucket too."""
    previous = storage.key_from_public_url(current_user.signature_url)
    if current_user.signature_url is not None:
        current_user.signature_url = None
        record_audit(db, action="SIGNATURE_DELETED", entity_type="USER", entity_id=current_user.id,
                     actor=current_user, request=request)
        await db.commit()
        await db.refresh(current_user)
    if previous and previous.startswith("signatures/") and settings.storage_configured:
        await storage.delete_quietly(previous)
    return UserResponse.from_model(current_user, own=True)


@router.post("/{user_id}/mfa-reset", response_model=UserResponse)
async def reset_mfa(
    user_id: uuid.UUID,
    payload: MFAResetRequest,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Last resort for an account that lost its authenticator and its recovery
    codes. Only another `MASTER_GC` and never on oneself: two people have to be
    involved, so a single stolen session cannot shed the second factor.
    """
    if not is_master(current_user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Sólo un MASTER_GC puede restablecer la verificación en dos pasos"
        )
    if user_id == current_user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "No puedes restablecer tu propia verificación en dos pasos"
        )
    target = await _get_user_or_404(db, user_id)

    await mfa_service.clear_second_factor(db, target)
    record_audit(
        db,
        action="MFA_RESET",
        entity_type="USER",
        entity_id=target.id,
        actor=current_user,
        details=f"MFA reset by {current_user.email}",
        metadata={"reason": payload.reason},
        request=request,
    )
    await db.commit()
    await db.refresh(target)

    # After the commit, and never able to fail the request.
    background.add_task(
        email_service.send_mfa_reset_email, target.email, target.name, payload.reason
    )
    return UserResponse.from_model(target)


# ----------------------------------------------------------------------------
# Guardianships. Declared before the `/{user_id}` routes on purpose.
# ----------------------------------------------------------------------------
@router.post(
    "/guardianships", response_model=GuardianshipResponse, status_code=status.HTTP_201_CREATED
)
async def create_guardianship(
    payload: GuardianshipCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # D9: any adult with a verified account may be a guardian. The director
    # whose own child is a member of the club does not need a second account;
    # PARENT_GUARDIAN stays as the role of whoever has no other function.
    if is_minor_user(current_user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Sólo una persona adulta puede ser tutora de un menor."
        )
    if current_user.verification_status != "VERIFIED":
        # The same bar as `memberships.decide_consent`: whoever vouches for a
        # minor proves first that the e-mail address is theirs.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Verifica tu correo antes de vincularte a un menor."
        )
    child = await _get_user_or_404(db, payload.child_id)
    if child.id == current_user.id or not child.is_minor:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Child must be marked as minor")

    duplicate = select(Guardianship.id).where(
        Guardianship.guardian_id == current_user.id, Guardianship.child_id == child.id
    )
    if (await db.execute(duplicate)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Guardianship already exists")

    row = Guardianship(
        id=uuid.uuid4(),
        guardian_id=current_user.id,
        child_id=child.id,
        relationship=payload.relationship,
        consent_status="PENDING",
        created_at=utcnow(),
    )
    db.add(row)
    record_audit(
        db,
        action="CREATE",
        entity_type="GUARDIANSHIP",
        entity_id=row.id,
        actor=current_user,
        metadata={"child_id": str(child.id), "relationship": row.relationship},
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) != "guardianships_guardian_id_child_id_key":
            raise
        raise HTTPException(status.HTTP_409_CONFLICT, "Guardianship already exists")
    return GuardianshipResponse.from_model(row)


@router.get("/guardianships/my-children", response_model=list[ChildGuardianship])
async def my_children(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """My minors: name, age, their club and whatever of theirs is waiting for
    me. Any adult may hold a guardianship (D9)."""
    if is_minor_user(current_user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Sólo una persona adulta puede tener menores a cargo."
        )
    stmt = (
        select(Guardianship, User)
        .join(User, User.id == Guardianship.child_id)
        .where(Guardianship.guardian_id == current_user.id)
        .order_by(Guardianship.created_at)
    )
    rows = (await db.execute(stmt)).all()

    children = []
    for guardianship, child in rows:
        membership = await membership_service.active_membership(db, child.id)
        pending = await membership_service.open_memberships(db, child.id)
        club = await db.get(Organization, membership.club_id) if membership else None
        children.append(
            ChildGuardianship(
                **GuardianshipResponse.from_model(guardianship).model_dump(),
                child_name=child.name,
                handle=child.handle,
                guardian_allows_avatar=bool(child.guardian_allows_avatar),
                child_age=age_in_years(child.birth_date),
                club=as_club_ref(club) if club is not None else None,
                membership_status=membership.status if membership else None,
                membership_id=str(membership.id) if membership else None,
                pending_consents=[
                    str(row.id) for row in pending if row.status == membership_service.PENDING_CONSENT
                ],
            )
        )
    return children


@router.get("/guardianships/my-guardians", response_model=list[GuardianshipResponse])
async def my_guardians(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not is_minor_user(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only minors can view guardians")
    stmt = (
        select(Guardianship)
        .where(Guardianship.child_id == current_user.id)
        .order_by(Guardianship.created_at)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [GuardianshipResponse.from_model(row) for row in rows]


async def _decide_guardianship(
    guardianship_id: uuid.UUID,
    approve: bool,
    request: Request,
    current_user: User,
    db: AsyncSession,
) -> GuardianshipResponse:
    row = await db.get(Guardianship, guardianship_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guardianship not found")
    if row.guardian_id != current_user.id:
        verb = "approve" if approve else "reject"
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only the guardian can {verb} consent")
    if approve and row.consent_status != "APPROVED":
        # SEC-01: the link was declared by this very person, so "approving" it was
        # self-consent — any adult became the approved guardian of any minor and read
        # their portfolio and photos. An approved guardianship only comes from the
        # single-use consent link e-mailed to the address the minor or the club gave
        # (`memberships.decide_consent`). Withdrawing (reject) stays self-service.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "La tutoría se confirma con el enlace de consentimiento que recibe el tutor por correo.",
        )

    row.consent_status = "APPROVED" if approve else "REJECTED"
    row.consent_granted_at = utcnow() if approve else None
    record_audit(
        db,
        action="APPROVE" if approve else "REJECT",
        entity_type="GUARDIANSHIP",
        entity_id=row.id,
        actor=current_user,
        request=request,
    )
    await db.commit()
    return GuardianshipResponse.from_model(row)


@router.post("/guardianships/{guardianship_id}/approve", response_model=GuardianshipResponse)
async def approve_guardianship(
    guardianship_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _decide_guardianship(guardianship_id, True, request, current_user, db)


@router.post("/guardianships/{guardianship_id}/reject", response_model=GuardianshipResponse)
async def reject_guardianship(
    guardianship_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _decide_guardianship(guardianship_id, False, request, current_user, db)


# ----------------------------------------------------------------------------
# Users
# ----------------------------------------------------------------------------
@router.get("", response_model=list[UserResponse])
@router.get("/", response_model=list[UserResponse], include_in_schema=False)
async def list_users(
    organization_id: uuid.UUID | None = Query(None, description="Root of the subtree to list"),
    org_node_id: uuid.UUID | None = Query(None, description="Legacy alias of organization_id"),
    role: RoleName | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Users of an organization subtree. MASTER_GC may list everything; everyone
    else is confined to the subtree of their own organization.
    """
    requested_org = organization_id or org_node_id
    stmt = select(User)

    if is_master(current_user) and requested_org is None:
        pass  # global
    else:
        if requested_org is not None and not is_master(current_user):
            if not await org_in_user_scope(db, current_user, requested_org):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "You can only view users in your organization"
                )
        scope_org = requested_org or current_user.organization_id
        scope_path = await get_org_path(db, scope_org)
        if current_user.role in CLUB_REVIEW_ROLES:
            # Club staff see their own club only: an instructor who hangs off a field (a
            # virtual instructor) is not staff of every club beneath it.
            club = await member_club(db, current_user)
            is_staff = club is not None and club_staff_in_good_standing(current_user)
            if is_staff and requested_org is not None and requested_org != club.id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo puedes ver a los miembros de tu club")
        else:
            is_staff = is_master(current_user) or current_user.role in ADMIN_ROLES
        if scope_path is None or director_blocked(current_user) or not is_staff:
            # No organization (or one outside the tree), a director whose club is
            # not approved yet, or a plain member: you only see yourself. The
            # directory carries e-mails and birth dates, minors included.
            stmt = stmt.where(User.id == current_user.id)
        else:
            stmt = stmt.join(Organization, Organization.id == User.organization_id).where(
                Organization.path.op("<@")(scope_path)
            )

    if role:
        stmt = stmt.where(User.role == role)
    stmt = stmt.order_by(User.name, User.id).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    if current_user.role in CLUB_REVIEW_ROLES and not may_handle_minors(current_user):
        # SEC-11 (E7): the same rule as `can_view_user` — staff without a valid church letter
        # (or a director out of grace) read no record of a minor, e-mail and birth date included.
        rows = [row for row in rows if row.id == current_user.id or not is_minor_user(row)]
    return [UserResponse.from_model(row) for row in rows]


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await _get_user_or_404(db, user_id)
    if not await can_view_user(db, current_user, target):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You do not have permission to view this user"
        )
    return UserResponse.from_model(target)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await _get_user_or_404(db, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No update data provided")

    for field, value in changes.items():
        if value is None and field not in NULLABLE_FIELDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field} cannot be null")

    is_self = target.id == current_user.id
    manages_target = await can_manage_user(db, current_user, target)
    guardianships = await profile_service.guardianships_of(db, target.id)
    # Bloque G: the photo of a minor is the guardian's decision (spec §1, rule 4). An
    # approved guardian (or MASTER_GC) sets the flag; the minor never does it themselves.
    is_guardian = any(
        guardian_id == current_user.id and consent == CONSENT_GRANTED
        for guardian_id, consent in guardianships
    )
    if "guardian_allows_avatar" in changes and not (
        is_guardian or (is_master(current_user) and not is_self)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "guardian_allows_avatar_forbidden")
    guardian_only = is_guardian and set(changes) == {"guardian_allows_avatar"}
    if not is_self and not manages_target and not guardian_only:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You do not have permission to modify this user"
        )
    new_avatar = changes.get("avatar_url")
    if new_avatar is not None and new_avatar != target.avatar_url:
        # SEC-08: the same rule as `PATCH /users/me/profile` — a file of the platform's public
        # bucket, never a third-party URL that every roster and profile viewer would load.
        changes["avatar_url"] = profile_service._check_media_url(
            new_avatar, profile_service.AVATARS_FOLDER, "invalid_avatar_url"
        )
    if changes.get("avatar_url") is not None and profile_is_minor(
        target, has_guardian=bool(guardianships)
    ) and not changes.get("guardian_allows_avatar", target.guardian_allows_avatar):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "minor_avatar_not_allowed")

    if manages_target:
        allowed_admin_fields = ADMIN_ONLY_FIELDS
    elif is_self and is_admin_role(current_user):
        allowed_admin_fields = SELF_ADMIN_FIELDS
    else:
        allowed_admin_fields = set()

    admin_changes = ADMIN_ONLY_FIELDS.intersection(changes)
    forbidden = admin_changes - allowed_admin_fields
    if forbidden:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Only an administrator of this user can update: {', '.join(sorted(forbidden))}",
        )

    if admin_changes:
        await _validate_admin_changes(db, current_user, target, changes)

    # `organization_id` and the club role of an account are written by ONE
    # service (spec §6, rule 1), which also keeps `club_memberships` in step:
    # moving somebody between clubs from here is a transfer, not an assignment.
    delegated = await membership_service.apply_admin_change(
        db, actor=current_user, target=target, changes=changes, request=request
    )
    skip = {"organization_id", "role"} if delegated else set()

    for field in (SELF_EDITABLE_FIELDS | ADMIN_ONLY_FIELDS | {"guardian_allows_avatar"}) - skip:
        if field in changes:
            value = changes[field]
            setattr(target, field, value.strip() if field == "name" else value)

    record_audit(
        db,
        action="UPDATE",
        entity_type="USER",
        entity_id=target.id,
        actor=current_user,
        details=f"Updated fields: {', '.join(sorted(changes))}",
        metadata={"fields": sorted(changes)},
        request=request,
    )
    await db.commit()
    await db.refresh(target)
    return UserResponse.from_model(target)


async def _validate_admin_changes(
    db: AsyncSession, actor: User, target: User, changes: dict
) -> None:
    new_role = changes.get("role", target.role)
    will_be_minor = changes.get("is_minor", target.is_minor)

    if "role" in changes and new_role != target.role:
        if not outranks(actor, new_role):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "You cannot grant a role equal to or above your own"
            )
        if new_role == MASTER_GC and not target.mfa_enabled:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "MFA must be enabled before assigning MASTER_GC role"
            )
        if new_role == INSTRUCTOR and not target.child_protection_completed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Child protection certification required for INSTRUCTOR role",
            )

    if will_be_minor and new_role != STUDENT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minors can only hold the STUDENT role")

    new_org = changes.get("organization_id")
    if new_org is not None:
        if await db.get(Organization, new_org) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Organization node not found")
        if not await org_in_user_scope(db, actor, new_org):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Target organization is outside your scope"
            )
    elif "organization_id" in changes and not is_master(actor):
        # Detaching a user removes them from every scoped admin's reach.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only MASTER_GC can remove a user's organization"
        )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete: the account becomes INACTIVE."""
    target = await _get_user_or_404(db, user_id)
    if not await can_manage_user(db, current_user, target):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You do not have permission to delete this user"
        )
    target.status = "INACTIVE"
    record_audit(
        db,
        action="DELETE",
        entity_type="USER",
        entity_id=target.id,
        actor=current_user,
        details="Soft delete (status INACTIVE)",
        request=request,
    )
    await db.commit()


@router.post("/{user_id}/verify", response_model=UserResponse)
async def verify_user(
    user_id: uuid.UUID,
    request: Request,
    approved: bool = Query(..., description="True to approve, False to reject"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not is_admin_role(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only administrators can verify users")
    target = await _get_user_or_404(db, user_id)
    if not await can_manage_user(db, current_user, target):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This user is outside your scope")

    target.verification_status = "VERIFIED" if approved else "REJECTED"
    record_audit(
        db,
        action="APPROVE" if approved else "REJECT",
        entity_type="USER",
        entity_id=target.id,
        actor=current_user,
        details=f"verification_status -> {target.verification_status}",
        request=request,
    )
    await db.commit()
    await db.refresh(target)
    return UserResponse.from_model(target)


@router.post("/{user_id}/child-protection-cert", response_model=UserResponse)
async def update_child_protection_cert(
    user_id: uuid.UUID,
    request: Request,
    completed: bool = Query(..., description="True if completed"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not is_admin_role(current_user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only administrators can update child protection certification",
        )
    target = await _get_user_or_404(db, user_id)
    if not await can_manage_user(db, current_user, target):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This user is outside your scope")
    if target.is_minor:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Child protection certification does not apply to minors"
        )

    target.child_protection_completed = completed
    target.child_protection_completed_at = utcnow() if completed else None
    record_audit(
        db,
        action="UPDATE",
        entity_type="USER",
        entity_id=target.id,
        actor=current_user,
        details=f"child_protection_completed -> {completed}",
        request=request,
    )
    await db.commit()
    await db.refresh(target)
    return UserResponse.from_model(target)
