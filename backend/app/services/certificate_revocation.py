"""Bloque D · I7 — The endpoint side of annulling a certificate (spec §5.5).

`certificates.revoke_certificate` writes the change; this is the thin layer around it:
find the certificate, ask `rbac.can_revoke`, queue the e-mail and commit once, so the
audit row, the `revoked` event, the WITHDRAWN enrollment and the notification log all
land together or not at all.
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Certificate, User
from app.rbac import can_revoke
from app.schemas.portfolio import CertificateOut, CertificateRevokeIn
from app.services import notifications, portfolio
from app.services.certificates import revoke_certificate


async def revoke(
    db: AsyncSession,
    actor: User,
    certificate_id: uuid.UUID,
    payload: CertificateRevokeIn,
    request: Request | None,
    background=None,
) -> CertificateOut:
    certificate = await db.get(Certificate, certificate_id)
    if certificate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificado no encontrado")
    if not await can_revoke(db, actor, certificate):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo la Asociación que cubre este certificado puede anularlo",
        )
    await revoke_certificate(db, certificate, actor, payload.reason, request)
    await notifications.queue_certificate_revoked(
        db, background, certificate=certificate, reason=payload.reason
    )
    await db.commit()
    return (await portfolio._certificates_out(db, [certificate]))[0]
