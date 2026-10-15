"""The club's pass of an event (028): a stable QR that only changes when somebody regenerates it.

Stored twice on `event_registrations`:
  * `pass_token_hash` — sha256, the ONLY thing a lookup uses (unchanged since 025);
  * `pass_token_enc`  — the token encrypted with AES-GCM under a key derived (HKDF-SHA256) from
    `EVENT_PASS_SECRET`, or `JWT_SECRET` when that is unset. It exists so that the director and
    the coordination can SHOW the pass again without rotating it (bug B1).

`decrypt` answers None for anything it cannot open (a pass from before 028, a rotated secret, a
tampered value): the caller then regenerates the pass once. The QR encodes `pass_url(token)`,
`<EVENT_PASS_URL_BASE>/<token>`; `token_from` accepts either the bare token or that URL.
"""
from __future__ import annotations

import base64
import os
import re
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings

VERSION = "v1"
_INFO = b"adventist.club/event-pass/v1"
_AAD = b"event-pass"
# Tokens are `evp_` + urlsafe base64 (new_pass_token); a little slack for older shapes.
_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{8,200}$")


def _key() -> bytes:
    secret = settings.EVENT_PASS_SECRET or settings.JWT_SECRET
    if not secret:
        raise RuntimeError("EVENT_PASS_SECRET/JWT_SECRET is not configured")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(secret.encode())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encrypt(token: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_key()).encrypt(nonce, token.encode(), _AAD)
    return f"{VERSION}.{_b64(nonce + sealed)}"


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        version, payload = value.split(".", 1)
        if version != VERSION:
            return None
        raw = _unb64(payload)
        return AESGCM(_key()).decrypt(raw[:12], raw[12:], _AAD).decode()
    except (ValueError, InvalidTag, UnicodeDecodeError, RuntimeError):
        return None


def pass_url(token: str) -> str:
    return f"{settings.event_pass_url_base}/{token}"


def token_from(value: str) -> str:
    """The token inside what a scanner read: the bare token, or a URL whose path ends in
    `/p/<token>` (any host: the hash lookup is what decides). Unknown shapes come back as they
    are and simply resolve to nothing. A typed token may keep the spaces of the printed pass
    («evp_ AbCd EfGh …», `event_passes_pdf.code_lines`): a token never contains whitespace."""
    value = (value or "").strip()
    if "/" not in value:
        return "".join(value.split())
    try:
        path = urlsplit(value).path if "://" in value else value
    except ValueError:
        return value
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[-2] == "p" and _TOKEN.match(parts[-1]):
        return parts[-1]
    return value
