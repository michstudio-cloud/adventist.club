"""«QR para jueces»: a signed link, valid 30 minutes, that lets whoever scans it (with an adult
account) join the event as a judge of every activity.

Stateless: `ejl_` + base64url(event id ‖ issuer id ‖ expiry) + `.` + base64url(HMAC). Nothing is
stored, so a new QR does not revoke the previous one: each simply stops working at its expiry.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException, status

from app.config import settings

TTL_SECONDS = 30 * 60
PREFIX = "ejl_"
INVALID = "Este QR ya no sirve. Pide a la coordinación uno nuevo."
_INFO = b"event-judge-join-link-v1"


def _key() -> bytes:
    secret = settings.EVENT_PASS_SECRET or settings.JWT_SECRET
    if not secret:
        raise RuntimeError("EVENT_PASS_SECRET/JWT_SECRET is not configured")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(secret.encode())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make(event_id: uuid.UUID, issuer_id: uuid.UUID, *, now: float | None = None) -> tuple[str, datetime]:
    expires = int(now if now is not None else time.time()) + TTL_SECONDS
    body = event_id.bytes + issuer_id.bytes + struct.pack(">I", expires)
    sig = hmac.new(_key(), body, hashlib.sha256).digest()[:16]
    return f"{PREFIX}{_b64(body)}.{_b64(sig)}", datetime.fromtimestamp(expires, tz=timezone.utc)


def read(token: str, *, now: float | None = None) -> tuple[uuid.UUID, uuid.UUID, datetime]:
    """(event id, issuer id, expiry), or the same neutral 404 for anything wrong or expired."""
    try:
        value = (token or "").strip()
        if not value.startswith(PREFIX) or len(value) > 200:
            raise ValueError
        body_text, sig_text = value[len(PREFIX):].split(".", 1)
        body, sig = _unb64(body_text), _unb64(sig_text)
        if len(body) != 36 or not hmac.compare_digest(sig, hmac.new(_key(), body, hashlib.sha256).digest()[:16]):
            raise ValueError
        (expires,) = struct.unpack(">I", body[32:])
    except (ValueError, TypeError, struct.error):
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID) from None
    if expires <= int(now if now is not None else time.time()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID)
    return uuid.UUID(bytes=body[:16]), uuid.UUID(bytes=body[16:32]), datetime.fromtimestamp(expires, tz=timezone.utc)
