"""Auth request/response schemas."""
import uuid
from datetime import date
from typing import Literal

from pydantic import AliasChoices, BaseModel, EmailStr, Field, model_validator

from app.schemas.org import ClubSignup

RoleName = Literal[
    "MASTER_GC",
    "ADMIN_DIVISION",
    "ADMIN_UNION",
    "ADMIN_ASSOCIATION",
    "COORDINATOR_ZONE",
    "CLUB_DIRECTOR",
    "CLUB_SECRETARY",
    "INSTRUCTOR",
    "COUNSELOR",
    "STUDENT",
    "PARENT_GUARDIAN",
]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    name: str = Field(min_length=1, max_length=180)
    role: RoleName = "STUDENT"
    # `org_node_id` is the legacy field name; both are accepted.
    organization_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasChoices("organization_id", "org_node_id")
    )
    is_minor: bool = False
    birth_date: date | None = None
    # CLUB_DIRECTOR only: the club to open, created `pending` in the same transaction.
    club: ClubSignup | None = None


class RegisterResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    status: str
    message: str
    organization_id: str | None = None
    club_approval: str | None = None
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    mfa_required: bool = False
    temp_token: str | None = None
    # The role obliges a second factor and this account has not enrolled yet.
    # Advisory while `MASTER_MFA_ENFORCED` is off, a hard block once it is on.
    mfa_enrollment_required: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MFASetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
    qr_code_url: str


class MFACodeRequest(BaseModel):
    totp_code: str = Field(min_length=6, max_length=10)


class MFAVerifyRequest(BaseModel):
    """Second login step: exactly one of `totp_code` or `recovery_code`."""

    temp_token: str
    totp_code: str | None = Field(default=None, min_length=6, max_length=10)
    recovery_code: str | None = Field(default=None, min_length=10, max_length=20)

    @model_validator(mode="after")
    def _one_factor(self):
        if bool(self.totp_code) == bool(self.recovery_code):
            raise ValueError("Provide either `totp_code` or `recovery_code`")
        return self


class MFAEnrollResponse(BaseModel):
    """Answer to verify-setup and to recovery-codes: the codes travel once."""

    message: str
    recovery_codes: list[str]
    # True when the current token will stop working because the policy now
    # demands a session born of the second factor.
    reauth_required: bool = False


class MFAResetRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class VerifyEmailCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class VerifyEmailTokenRequest(BaseModel):
    token: str = Field(min_length=10, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Either {token, new_password} or {email, code, new_password}."""

    token: str | None = Field(default=None, max_length=512)
    email: EmailStr | None = None
    code: str | None = Field(default=None, min_length=6, max_length=6)
    new_password: str = Field(max_length=256)

    @model_validator(mode="after")
    def _one_credential(self):
        has_token = bool(self.token)
        has_code = bool(self.email and self.code)
        if has_token == has_code:
            raise ValueError("Provide either `token` or both `email` and `code`")
        return self


class MessageResponse(BaseModel):
    message: str
