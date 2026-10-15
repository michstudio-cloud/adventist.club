"""Event node operator (coordination, from the node's Mac): unlock an account locked by wrong
passwords, list the locked ones, and see the connected devices (docs/RATE_LIMITS.md).

Authenticated by the header `X-Nodo-Operador`, compared in constant time with NODO_OPERADOR_TOKEN
(>= 32 characters). Without that token (production) every route here answers 404.
"""
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User
from app.rate_limit import device_locked_until, locked_accounts, normalize_email, unlock_account
from app.services import device_tracker
from app.services.audit import record_audit

OPERATOR_HEADER = "X-Nodo-Operador"
OPERATOR_ACTOR = "operador del nodo"


def require_operator(x_nodo_operador: str | None = Header(default=None)) -> None:
    token = settings.nodo_operador_token
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    if not x_nodo_operador:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta la cabecera X-Nodo-Operador.")
    if not hmac.compare_digest(x_nodo_operador.encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token de operador incorrecto.")


router = APIRouter(
    prefix="/api/v1/nodo/operador",
    tags=["nodo-operador"],
    dependencies=[Depends(require_operator)],
    include_in_schema=False,
)


class UnlockRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/cuentas/desbloquear")
async def unlock(payload: UnlockRequest, request: Request, db: AsyncSession = Depends(get_db)):
    email = normalize_email(payload.email)
    result = unlock_account(email)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    record_audit(
        db,
        action="AUTH_LOGIN_UNLOCK",
        entity_type="USER",
        entity_id=user.id if user else email,
        details=f"Inicio de sesión desbloqueado por el {OPERATOR_ACTOR}",
        metadata={"email": email, "actor": OPERATOR_ACTOR,
                  "contadores_borrados": result["contadores_borrados"],
                  "dispositivos": result["dispositivos"]},
        request=request,
    )
    await db.commit()
    return {"email": email, "desbloqueada": True, **{k: result[k] for k in ("contadores_borrados", "dispositivos")}}


@router.get("/cuentas/bloqueadas")
async def locked():
    return {"generado": _now(), "modo": settings.LOGIN_LOCKOUT_MODE, "cuentas": locked_accounts()}


@router.get("/dispositivos")
async def devices(minutos: int = Query(default=60, ge=1, le=720)):
    listed = device_tracker.snapshot(minutos, locked_until=device_locked_until)
    return {"generado": _now(), "minutos": minutos, "total": len(listed), "dispositivos": listed}
