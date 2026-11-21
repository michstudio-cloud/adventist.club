"""«QR para jueces»: a signed link, valid 30 minutes, that lets whoever scans it (with an adult
account) join the event as a judge of every activity, or only of the activities the coordination
chose when making it.

Stateless: `ejl_` + base64url(body) + `.` + base64url(HMAC(body)). Nothing is stored, so a new QR
does not revoke the previous one: each simply stops working at its expiry. Two bodies:

  * every activity (the first format, still accepted): event id ‖ issuer id ‖ expiry (36 bytes);
  * some activities: the same 36 bytes ‖ 0x02 ‖ the first 8 bytes of each activity id (37 + 8·n).

The activities travel inside the signed body, so nobody can add one to a QR. Only a prefix of each
id travels, to keep the QR small; it is resolved against the event's own activities when joining
(64 random bits: two activities of one event never share it in practice).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import NamedTuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException, status

from app.config import settings

TTL_SECONDS = 30 * 60
PREFIX = "ejl_"
INVALID = "Este QR ya no sirve. Pide a la coordinación uno nuevo."
_INFO = b"event-judge-join-link-v1"
_BASE = 36  # event id ‖ issuer id ‖ expiry
_SOME = 2  # marker byte of the «some activities» body
REF_BYTES = 8
MAX_ACTIVITIES = 24
MAX_TOKEN = 400


class JudgeLink(NamedTuple):
    event_id: uuid.UUID
    issuer_id: uuid.UUID
    expires: datetime
    # The first REF_BYTES of each chosen activity id; empty = every activity.
    activity_refs: tuple[bytes, ...] = ()


def activity_ref(activity_id: uuid.UUID) -> bytes:
    return activity_id.bytes[:REF_BYTES]


def _key() -> bytes:
    secret = settings.EVENT_PASS_SECRET or settings.JWT_SECRET
    if not secret:
        raise RuntimeError("EVENT_PASS_SECRET/JWT_SECRET is not configured")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(secret.encode())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make(event_id: uuid.UUID, issuer_id: uuid.UUID, *, activity_ids: Sequence[uuid.UUID] = (),
         now: float | None = None) -> tuple[str, datetime]:
    """No `activity_ids`: every activity (the first format). The caller checks they are the event's."""
    refs = list(dict.fromkeys(activity_ref(a) for a in activity_ids))
    if len(refs) > MAX_ACTIVITIES:
        raise ValueError(f"at most {MAX_ACTIVITIES} activities")
    expires = int(now if now is not None else time.time()) + TTL_SECONDS
    body = event_id.bytes + issuer_id.bytes + struct.pack(">I", expires)
    if refs:
        body += bytes([_SOME]) + b"".join(refs)
    sig = hmac.new(_key(), body, hashlib.sha256).digest()[:16]
    return f"{PREFIX}{_b64(body)}.{_b64(sig)}", datetime.fromtimestamp(expires, tz=timezone.utc)


def _refs(body: bytes) -> tuple[bytes, ...]:
    """The activity prefixes of a body (empty for the «every activity» format); ValueError if malformed."""
    if len(body) == _BASE:
        return ()
    rest = body[_BASE + 1:]
    count, extra = divmod(len(rest), REF_BYTES)
    if len(body) <= _BASE or body[_BASE] != _SOME or extra or not 1 <= count <= MAX_ACTIVITIES:
        raise ValueError
    return tuple(rest[i * REF_BYTES:(i + 1) * REF_BYTES] for i in range(count))


def read(token: str, *, now: float | None = None) -> JudgeLink:
    """The link's event, issuer, expiry and activities, or the same neutral 404 for anything wrong or expired."""
    try:
        value = (token or "").strip()
        if not value.startswith(PREFIX) or len(value) > MAX_TOKEN:
            raise ValueError
        body_text, sig_text = value[len(PREFIX):].split(".", 1)
        body, sig = _unb64(body_text), _unb64(sig_text)
        if not hmac.compare_digest(sig, hmac.new(_key(), body, hashlib.sha256).digest()[:16]):
            raise ValueError
        refs = _refs(body)
        (expires,) = struct.unpack(">I", body[32:_BASE])
    except (ValueError, TypeError, struct.error):
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID) from None
    if expires <= int(now if now is not None else time.time()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID)
    return JudgeLink(uuid.UUID(bytes=body[:16]), uuid.UUID(bytes=body[16:32]),
                     datetime.fromtimestamp(expires, tz=timezone.utc), refs)
