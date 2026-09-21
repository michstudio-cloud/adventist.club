import logging
from datetime import date
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on", "y", "t"}
_FALSE_VALUES = {"0", "false", "no", "off", "n", "f"}


class Settings(BaseSettings):
    APP_NAME: str = "Adventist Club API"
    ENV: str = "development"
    # Legacy name for ENV. When set it wins over ENV (see `environment`).
    ENVIRONMENT: str | None = None
    DATABASE_URL: str
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    PUBLIC_WEB_URL: str = "http://localhost:5173"
    CORS_ORIGINS: str = "http://localhost:5173,https://conquistadores.app"

    # --- Auth (optional: without JWT_SECRET protected endpoints answer 503) ---
    JWT_SECRET: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    MFA_ISSUER: str = "ADVENTIST.CLUB"
    # Deploy switch for the MFA policy of `security.MFA_REQUIRED_ROLES` (E1).
    # Off by default: the code ships first and enforcement is turned on once
    # every seeded MASTER_GC has enrolled, so nobody is ever locked out.
    MASTER_MFA_ENFORCED: bool = False
    # `organizations.code` of the entity that issues certificates (Unión/Asociación).
    # PROTOTYPE keeps the self-created placeholder until the real one exists.
    ISSUER_ORGANIZATION_CODE: str = "PROTOTYPE"
    # Deploy switch of the church letter for club leaders (E7), as an ISO date.
    # UNSET (the default) means `rbac.may_handle_minors` is always true, so
    # production behaves exactly as before the letter existed. The owner sets
    # the day enforcement starts once every active association has at least one
    # validator; from that day a director's 60-day grace is counted too.
    LEADER_VERIFICATION_ENFORCED_FROM: date | None = None

    # --- Cloudflare R2 (optional: without it media upload answers 503) ---
    R2_ACCOUNT_ID: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET_NAME: str = "adventist-media"
    R2_PUBLIC_URL: str = "https://media.adventist.club"
    # Second bucket, no public domain: portfolio evidence (photos of minors). Same credentials.
    # Without it the evidence endpoints answer 503 and everything else works.
    R2_PRIVATE_BUCKET_NAME: str | None = None

    # --- Email via Resend (optional: without it emails are logged and skipped) ---
    RESEND_API_KEY: str | None = None
    EMAIL_FROM: str = "Adventist.Club <hi@adventist.club>"
    EMAIL_FROM_NAME: str = "Adventist.Club"
    FRONTEND_URL: str | None = None

    # --- Monitoring (optional: no-op without SENTRY_DSN) ---
    SENTRY_DSN: str | None = None
    SENTRY_DEBUG_MODE: bool = False
    SERVER_NAME: str | None = None

    # --- Rate limiting ---
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_REGISTER: str = "3/hour"
    RATE_LIMIT_FORGOT_PASSWORD: str = "3/hour"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator(
        "ENVIRONMENT",
        "JWT_SECRET",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_PRIVATE_BUCKET_NAME",
        "RESEND_API_KEY",
        "FRONTEND_URL",
        "SENTRY_DSN",
        "SERVER_NAME",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        """`FOO=` in the environment means "not configured", not an empty secret."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "JWT_ALGORITHM",
        "MFA_ISSUER",
        "ISSUER_ORGANIZATION_CODE",
        "R2_BUCKET_NAME",
        "R2_PUBLIC_URL",
        "EMAIL_FROM",
        "EMAIL_FROM_NAME",
        "RATE_LIMIT_LOGIN",
        "RATE_LIMIT_REGISTER",
        "RATE_LIMIT_FORGOT_PASSWORD",
        mode="before",
    )
    @classmethod
    def _blank_uses_default(cls, value: Any, info) -> Any:
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @field_validator(
        "SENTRY_DEBUG_MODE", "RATE_LIMIT_ENABLED", "MASTER_MFA_ENFORCED", mode="before"
    )
    @classmethod
    def _lenient_bool(cls, value: Any, info) -> Any:
        """A malformed optional flag must never stop the service from booting."""
        default = cls.model_fields[info.field_name].default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
        if text:
            logger.warning("Invalid boolean for %s; using default %s", info.field_name, default)
        return default

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES", "REFRESH_TOKEN_EXPIRE_DAYS", mode="before")
    @classmethod
    def _lenient_positive_int(cls, value: Any, info) -> Any:
        default = cls.model_fields[info.field_name].default
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            logger.warning("Invalid integer for %s; using default %s", info.field_name, default)
            return default
        return number if number > 0 else default

    @field_validator("LEADER_VERIFICATION_ENFORCED_FROM", mode="before")
    @classmethod
    def _lenient_date(cls, value: Any) -> Any:
        """A malformed switch must never stop the service from booting, and it
        must never turn enforcement ON by accident: anything unreadable means
        "not configured", which is the permissive setting."""
        if value is None or isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            logger.warning("Invalid date for LEADER_VERIFICATION_ENFORCED_FROM; ignoring it")
            return None

    @property
    def cors_list(self) -> list[str]:
        return [x.strip() for x in self.CORS_ORIGINS.split(",") if x.strip()]

    @property
    def environment(self) -> str:
        return self.ENVIRONMENT or self.ENV

    @property
    def frontend_url(self) -> str:
        """Base URL used in emailed links."""
        return (self.FRONTEND_URL or self.PUBLIC_WEB_URL).rstrip("/")

    @property
    def auth_configured(self) -> bool:
        return bool(self.JWT_SECRET)

    @property
    def storage_configured(self) -> bool:
        return bool(self.R2_ACCOUNT_ID and self.R2_ACCESS_KEY_ID and self.R2_SECRET_ACCESS_KEY)

    @property
    def private_storage_configured(self) -> bool:
        return bool(self.storage_configured and self.R2_PRIVATE_BUCKET_NAME)

    @property
    def email_configured(self) -> bool:
        return bool(self.RESEND_API_KEY)


settings = Settings()
