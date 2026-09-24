"""021 — The handwritten signatures stay on the issued certificate (docs/CERTIFICADOS_V4.md «Firmas»).

Until 020 a signature was printed when the certificate was generated and then lost: downloading
it again from somewhere else (the portfolio, `/verify/<folio>`, `POST /render` with the folio)
came out unsigned. The owner's rule: «para guardar el archivo pidamos que se registren» — only an
issuance by someone with an account keeps it.

**What the issuer may send** for `signature_director` / `signature_instructor`:

- a data URL (drawn or uploaded at that moment): PNG / JPEG / WebP, <= 400 KB, re-encoded
  (`app/certificates/signatures.py`, the same rules as `POST /render`);
- an https URL **only when it is the issuer's own saved signature** (`users.signature_url`; the
  only saved signature a client ever sees is its own). Any other URL — another host, another
  person's saved signature, any other object of the bucket — is a 422. Nobody can put someone
  else's saved signature on a certificate.

Who issues is on the record (`issued_by_id`, audit row with the slots signed), so a drawn
signature has an accountable author exactly like the typed names next to it.

**What is kept** is never the account's URL (its owner may replace or delete it any day) but an
immutable copy, uploaded through the same `storage.upload_bytes` as everything else, to
`certificates/signatures/<certificate id>/<uuid>.png`. A batch (the assistant with a session)
uploads each signature once, in the folder of its first certificate, and every certificate of the
batch points to it: the copies are never deleted, so sharing one is the same as copying it.

**Never deleted**: revoking a certificate (a historical document) or deleting the signer's
account (the copy belongs to the certificate) leaves them where they are.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

import anyio
from fastapi import HTTPException, status

from app.certificates.signatures import (
    SIGNATURE_FIELDS,
    SignatureError,
    normalize_signature_bytes,
    png_data_url,
    signature_png_from_data_url,
)
from app.models import Certificate, User
from app.services import storage

logger = logging.getLogger(__name__)

SAVED_FOLDER = "signatures/"
COPY_PREFIX = f"{storage.CERTIFICATE_SIGNATURES_PREFIX}/"
# A normalized PNG (<= 1600 px) re-encoded from a small JPEG can exceed the 400 KB of a person's
# upload; what the API stored itself is read with this cap.
STORED_MAX_BYTES = 4 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def column(slot: str) -> str:
    """`signature_director` -> `signature_director_url` (the `certificates` column)."""
    return f"{slot}_url"


def stored_urls(certificate: Certificate) -> dict[str, str]:
    return {slot: url for slot in SIGNATURE_FIELDS if (url := getattr(certificate, column(slot)))}


def is_unaccounted(certificate: Certificate) -> bool:
    """The open prototype tool without a session: nobody of the platform stands behind it (the
    same test as `official` in `GET /verify`)."""
    return certificate.issued_by_id is None and certificate.user_id is None and certificate.enrollment_id is None


def _unprocessable(detail: str, slot: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{detail} ({slot})")


async def _saved_png(user: User) -> bytes:
    """The account's saved signature, read from the bucket. Raises StorageError / SignatureError."""
    key = storage.key_from_public_url(user.signature_url)
    if not key or not key.startswith(SAVED_FOLDER):
        raise SignatureError("La cuenta no tiene firma guardada.")
    data, _type = await storage.download_bytes(key, STORED_MAX_BYTES)
    return await anyio.to_thread.run_sync(
        lambda: normalize_signature_bytes(data, "image/png", max_bytes=STORED_MAX_BYTES)
    )


async def prepare(values: Mapping[str, str | None], issuer: User) -> dict[str, bytes]:
    """Validate what the issuer sent, before anything is issued. `{slot: normalized PNG}`.

    422 with the reason for anything that cannot be a signature or is not theirs to use;
    503 / 502 when their saved signature cannot be read from the bucket right now."""
    prepared: dict[str, bytes] = {}
    for slot in SIGNATURE_FIELDS:
        value = (values.get(slot) or "").strip()
        if not value:
            continue
        if value.startswith("data:"):
            try:
                prepared[slot] = await anyio.to_thread.run_sync(signature_png_from_data_url, value)
            except SignatureError as exc:
                raise _unprocessable(str(exc), slot) from exc
            continue
        if not issuer.signature_url or value != issuer.signature_url:
            raise _unprocessable("La firma debe ser una imagen o tu propia firma guardada.", slot)
        try:
            prepared[slot] = await _saved_png(issuer)
        except SignatureError as exc:
            raise _unprocessable(str(exc), slot) from exc
        except storage.StorageNotConfigured as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Almacenamiento no configurado") from exc
        except storage.StorageError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No se pudo leer tu firma guardada.") from exc
    return prepared


async def attach(certificates: Sequence[Certificate], signatures: Mapping[str, bytes], *, strict: bool = True) -> list[str]:
    """Upload each signature once to the first certificate's folder and point every certificate
    of `certificates` to it. Returns the slots stored.

    `strict`: the person asked for this signature, so a bucket that cannot take it is an error
    and nothing is issued (503 / 502; the caller has not committed). Not strict (automatic
    issuance): the certificate is issued without it and the failure is logged."""
    if not certificates or not signatures:
        return []
    folder = storage.certificate_signature_folder(certificates[0].id)
    stored: list[str] = []
    for slot in SIGNATURE_FIELDS:
        png = signatures.get(slot)
        if not png:
            continue
        try:
            url, _key = await storage.upload_bytes(png, "image/png", folder)
        except storage.StorageNotConfigured as exc:
            if not strict:
                logger.warning("signature %s not kept for %s: storage not configured", slot, certificates[0].id)
                continue
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Almacenamiento no configurado") from exc
        except storage.StorageError as exc:
            if not strict:
                logger.warning("signature %s not kept for %s: %s", slot, certificates[0].id, exc)
                continue
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No se pudo guardar la firma del certificado.") from exc
        for certificate in certificates:
            setattr(certificate, column(slot), url)
        stored.append(slot)
    return stored


async def saved_signature(user: User | None) -> bytes | None:
    """Best effort, for an issuance nobody clicked (the automatic course certificate): the
    signer's saved signature, or None when there is none or it cannot be read."""
    if user is None or not user.signature_url:
        return None
    try:
        return await _saved_png(user)
    except (SignatureError, storage.StorageNotConfigured, storage.StorageError) as exc:
        logger.warning("saved signature of %s not usable: %s", user.id, exc)
        return None


async def render_images(certificate: Certificate, slots: Sequence[str]) -> dict[str, str]:
    """`{slot: PNG data URL}` of the copies this certificate keeps, for `POST /render`.

    Only copies under `certificates/signatures/` are ever read (they are ours and already
    normalized). Raises StorageError / StorageNotConfigured when one cannot be read."""
    images: dict[str, str] = {}
    for slot, url in stored_urls(certificate).items():
        if slot not in slots:
            continue
        key = storage.key_from_public_url(url)
        if not key or not key.startswith(COPY_PREFIX):
            raise storage.StorageError(f"firma fuera de {COPY_PREFIX}")
        data, _type = await storage.download_bytes(key, STORED_MAX_BYTES)
        if not data.startswith(PNG_MAGIC):
            raise storage.StorageError("la firma guardada no es un PNG")
        images[slot] = png_data_url(data)
    return images
