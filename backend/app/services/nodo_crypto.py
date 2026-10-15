"""Cryptography of the «Nodo Camporee» automatic mode (030; CONTRATO_NODO_LOGIN.md).

Self-contained (only `cryptography`): `scripts/nodo_sync.py` imports it on the node too.

  * SEALED ENVELOPE «nodo-sobre/1» — X25519 ECDH with an ephemeral sender key, HKDF-SHA256,
    AES-256-GCM. The export is sealed to the node's one-time public key (the API never keeps a
    readable copy, TLS-terminating proxies and logs see only ciphertext); the node's uploads
    are sealed to `import_key`, an X25519 key the server derives — never stores — from its
    secret and the export id.
  * SIGNATURE — every upload is signed with the Ed25519 key the node bound at export time
    (`sign_key`): only the node that prepared the event can upload for it.
  * SECRET BOX — AES-256-GCM under a key derived from a server/node secret, for values kept at
    rest (the certificate's private key in the cloud, the node's signing key on the node).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT = "nodo-sobre/1"
ALG = "X25519-HKDF-SHA256-AES256GCM"
_INFO = b"adventist.club/nodo-sobre/v1"
SIGN_CONTEXT = "nodo-import/v1"
# A signed upload is accepted this long around the server's clock.
SIGNATURE_WINDOW_SECONDS = 15 * 60


class CryptoError(Exception):
    """Anything that cannot be opened or verified. The message is safe to show."""


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def unb64(text: str, *, size: int | None = None, what: str = "valor") -> bytes:
    try:
        raw = base64.b64decode(text.encode(), validate=True)
    except (ValueError, AttributeError) as exc:
        raise CryptoError(f"{what}: base64 inválido") from exc
    if size is not None and len(raw) != size:
        raise CryptoError(f"{what}: debe medir {size} bytes")
    return raw


def _raw_public(key) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_private(key) -> bytes:
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())


# --------------------------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------------------------
def new_box_keypair() -> tuple[bytes, bytes]:
    """(private, public) raw X25519."""
    key = X25519PrivateKey.generate()
    return _raw_private(key), _raw_public(key.public_key())


def new_sign_keypair() -> tuple[bytes, bytes]:
    """(private, public) raw Ed25519."""
    key = Ed25519PrivateKey.generate()
    return _raw_private(key), _raw_public(key.public_key())


def box_public_of(private: bytes) -> bytes:
    return _raw_public(X25519PrivateKey.from_private_bytes(private).public_key())


def check_box_public(text: str) -> bytes:
    raw = unb64(text, size=32, what="recipient_key")
    X25519PublicKey.from_public_bytes(raw)
    return raw


def check_sign_public(text: str) -> bytes:
    raw = unb64(text, size=32, what="sign_key")
    Ed25519PublicKey.from_public_bytes(raw)
    return raw


def derived_box_private(secret: str, label: str) -> bytes:
    """A deterministic X25519 private key: HKDF(secret, info=label). Used for `import_key`."""
    if not secret:
        raise CryptoError("falta el secreto del servidor")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=f"adventist.club/nodo-import-key/{label}".encode()).derive(secret.encode())


# --------------------------------------------------------------------------------------------
# Sealed envelope
# --------------------------------------------------------------------------------------------
def _aad(event_id: str) -> bytes:
    return f"{FORMAT}:{event_id}".encode()


def _envelope_key(shared: bytes, epk: bytes, recipient: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + recipient, info=_INFO).derive(shared)


def seal(payload: dict, recipient_public: bytes, event_id: str) -> dict:
    """{format, alg, epk, nonce, ciphertext}: gzip(JSON) sealed to `recipient_public`."""
    plain = gzip.compress(json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")).encode(),
                          compresslevel=6)
    eph = X25519PrivateKey.generate()
    epk = _raw_public(eph.public_key())
    shared = eph.exchange(X25519PublicKey.from_public_bytes(recipient_public))
    key = _envelope_key(shared, epk, recipient_public)
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, plain, _aad(event_id))
    return {"format": FORMAT, "alg": ALG, "epk": b64(epk), "nonce": b64(nonce), "ciphertext": b64(sealed)}


def open_sealed(envelope: dict, recipient_private: bytes, event_id: str, *, max_bytes: int | None = None) -> dict:
    if envelope.get("format") != FORMAT or envelope.get("alg") != ALG:
        raise CryptoError("formato de sobre desconocido")
    epk = unb64(envelope.get("epk", ""), size=32, what="epk")
    nonce = unb64(envelope.get("nonce", ""), size=12, what="nonce")
    sealed = unb64(envelope.get("ciphertext", ""), what="ciphertext")
    me = X25519PrivateKey.from_private_bytes(recipient_private)
    recipient = _raw_public(me.public_key())
    key = _envelope_key(me.exchange(X25519PublicKey.from_public_bytes(epk)), epk, recipient)
    try:
        plain = AESGCM(key).decrypt(nonce, sealed, _aad(event_id))
    except InvalidTag as exc:
        raise CryptoError("el sobre no se pudo abrir (clave equivocada o datos alterados)") from exc
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(plain)) as fh:
            data = fh.read(max_bytes + 1 if max_bytes else -1)
    except OSError as exc:
        raise CryptoError("el contenido del sobre está dañado") from exc
    if max_bytes and len(data) > max_bytes:
        raise CryptoError("el contenido del sobre es demasiado grande")
    return json.loads(data)


# --------------------------------------------------------------------------------------------
# Upload signature
# --------------------------------------------------------------------------------------------
def sign_message(event_id: str, node_id: str, timestamp: int | str, body: bytes) -> bytes:
    return f"{SIGN_CONTEXT}\n{event_id}\n{node_id}\n{timestamp}\n{hashlib.sha256(body).hexdigest()}".encode()


def sign(private: bytes, event_id: str, node_id: str, timestamp: int, body: bytes) -> str:
    key = Ed25519PrivateKey.from_private_bytes(private)
    return b64(key.sign(sign_message(event_id, node_id, timestamp, body)))


def verify(public_b64: str, signature_b64: str, event_id: str, node_id: str, timestamp: str, body: bytes) -> None:
    try:
        key = Ed25519PublicKey.from_public_bytes(unb64(public_b64, size=32, what="sign_key"))
        key.verify(unb64(signature_b64 or "", size=64, what="firma"),
                   sign_message(event_id, node_id, timestamp, body))
    except InvalidSignature as exc:
        raise CryptoError("la firma del nodo no es válida") from exc


# --------------------------------------------------------------------------------------------
# Secret box (values at rest)
# --------------------------------------------------------------------------------------------
_BOX_VERSION = "b1"


def _box_key(secret: str, purpose: str) -> bytes:
    if not secret:
        raise CryptoError("falta el secreto para cifrar")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=f"adventist.club/{purpose}/v1".encode()).derive(secret.encode())


def box(secret: str, purpose: str, value: bytes) -> str:
    nonce = os.urandom(12)
    return f"{_BOX_VERSION}.{b64(nonce + AESGCM(_box_key(secret, purpose)).encrypt(nonce, value, purpose.encode()))}"


def unbox(secret: str, purpose: str, text: str) -> bytes:
    try:
        version, payload = text.split(".", 1)
        if version != _BOX_VERSION:
            raise ValueError
        raw = base64.b64decode(payload)
        return AESGCM(_box_key(secret, purpose)).decrypt(raw[:12], raw[12:], purpose.encode())
    except (ValueError, InvalidTag) as exc:
        raise CryptoError("no se pudo descifrar (secreto distinto o dato alterado)") from exc
