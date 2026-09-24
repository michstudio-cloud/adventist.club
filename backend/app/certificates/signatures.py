"""Handwritten signatures as images on a certificate (docs/CERTIFICADOS_V4.md, «Firmas»).

A template offers `<image id="signature_director">` / `<image id="signature_instructor">` just
above each signature line. The caller of `POST /certificates/render` sends the picture as a
data URL (the assistant without an account: the signature lives only in that request) or as the
https URL of a signature saved to an account (`POST /users/me/signature`, public media bucket,
`signatures/<uuid>.png`). Both go through `normalize_signature` before the engine sees them:

- raster only: PNG, JPEG or WebP. SVG is refused — it is markup, and a signature has no reason
  to carry any;
- at most `MAX_SIGNATURE_BYTES` (400 KB) as received, and a sane pixel size (a small file that
  decodes to a huge canvas is refused, not rendered);
- the picture that reaches resvg is at most `MAX_OUTPUT_PX` on its long side (re-encoded PNG,
  transparency kept): the slot is ~2.4 in wide, so even at 600 dpi nothing visible is lost and
  the render time does not depend on what the person uploaded.
"""
from __future__ import annotations

import base64
import io
import re

from PIL import Image

SIGNATURE_FIELDS = ("signature_director", "signature_instructor")
MAX_SIGNATURE_BYTES = 400 * 1024
MAX_INPUT_PX = 4000                      # per side, as received
MAX_INPUT_PIXELS = 8_000_000             # width x height, as received
MAX_OUTPUT_PX = 1600                     # long side of what the engine draws
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
MIME_TYPES = frozenset(FORMATS.values())
DATA_URL_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)$", re.I)


class SignatureError(ValueError):
    """The picture cannot be a signature; the message is safe to show to the person."""


def decode_data_url(value: str) -> tuple[str, bytes]:
    match = DATA_URL_RE.match(value.strip())
    if not match:
        raise SignatureError("La firma debe ser una imagen PNG, JPEG o WebP.")
    mime = match.group(1).lower()
    if mime not in MIME_TYPES:
        raise SignatureError("La firma debe ser una imagen PNG, JPEG o WebP (SVG no se acepta).")
    encoded = re.sub(r"\s+", "", match.group(2))
    if len(encoded) * 3 // 4 > MAX_SIGNATURE_BYTES + 3:
        raise SignatureError(too_large_message())
    try:
        return mime, base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise SignatureError("La firma no es una imagen válida.") from exc


def too_large_message() -> str:
    return f"La firma supera el tamaño máximo de {MAX_SIGNATURE_BYTES // 1024} KB."


def normalize_signature_bytes(data: bytes, declared_mime: str | None = None) -> bytes:
    """Validate a signature picture and return it as a PNG the engine can draw quickly."""
    if not data:
        raise SignatureError("La firma está vacía.")
    if len(data) > MAX_SIGNATURE_BYTES:
        raise SignatureError(too_large_message())
    if declared_mime is not None and declared_mime not in MIME_TYPES:
        raise SignatureError("La firma debe ser una imagen PNG, JPEG o WebP (SVG no se acepta).")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            width, height = probe.size
        if fmt not in FORMATS:
            raise SignatureError("La firma debe ser una imagen PNG, JPEG o WebP.")
        if declared_mime is not None and FORMATS[fmt] != declared_mime:
            raise SignatureError("El contenido de la firma no corresponde a su tipo.")
        if width < 1 or height < 1 or width > MAX_INPUT_PX or height > MAX_INPUT_PX or width * height > MAX_INPUT_PIXELS:
            raise SignatureError(f"La firma mide {width} × {height} px; el máximo es {MAX_INPUT_PX} px por lado.")
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            picture = image.convert("RGBA")
    except SignatureError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise SignatureError("La firma no es una imagen válida.") from exc
    if max(picture.size) > MAX_OUTPUT_PX:
        picture.thumbnail((MAX_OUTPUT_PX, MAX_OUTPUT_PX), Image.LANCZOS)
    out = io.BytesIO()
    picture.save(out, "PNG", optimize=False, compress_level=6)
    return out.getvalue()


def normalize_signature(value: str) -> str:
    """Data URL in, data URL (PNG) out. Raises SignatureError with a message for the person."""
    mime, data = decode_data_url(value)
    png = normalize_signature_bytes(data, mime)
    return f"data:image/png;base64,{base64.b64encode(png).decode()}"
