"""Sentry error tracking. A strict no-op unless SENTRY_DSN is set."""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = (
    "database_url",
    "password",
    "token",
    "secret",
    "authorization",
    "code",
    "api_key",
    "apikey",
    "jwt",
    "cookie",
    "dsn",
)
UNSAMPLED_PATHS = {"/", "/docs", "/redoc", "/openapi.json", "/api/v1/health"}


def _is_sensitive(key) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def _redact(value):
    """Recursively replace the value of every sensitive-looking key."""
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive(key) else _redact(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def before_send(event, hint):
    request = event.get("request")
    if isinstance(request, dict):
        for section in ("data", "headers", "cookies", "env"):
            if section in request:
                request[section] = _redact(request[section])
        if request.get("query_string"):
            request["query_string"] = REDACTED
    for section in ("extra", "contexts"):
        if section in event:
            event[section] = _redact(event[section])
    return event


def traces_sampler(sampling_context) -> float:
    path = (sampling_context.get("asgi_scope") or {}).get("path", "")
    if path in UNSAMPLED_PATHS:
        return 0.0
    if path.startswith("/api/v1/auth/"):
        return 1.0
    if path.startswith("/api/"):
        return 0.1
    return 0.0


def init_sentry() -> bool:
    """Returns True when Sentry was initialised. Never raises."""
    if not settings.SENTRY_DSN:
        return False
    if settings.environment == "development" and not settings.SENTRY_DEBUG_MODE:
        logger.info("Skipping Sentry in development. Set SENTRY_DEBUG_MODE=true to enable.")
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.environment,
            server_name=settings.SERVER_NAME,
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            traces_sampler=traces_sampler,
            before_send=before_send,
            send_default_pii=False,
            attach_stacktrace=True,
            max_breadcrumbs=50,
        )
        logger.info("Sentry initialised (environment=%s)", settings.environment)
        return True
    except Exception:
        logger.exception("Failed to initialise Sentry; continuing without it")
        return False
