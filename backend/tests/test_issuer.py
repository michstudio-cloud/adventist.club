"""Issuer organisation resolution for certificates."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.config import settings
from app.main import resolve_issuer_organization
from app.models import Organization
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_issuer_resolution(client, monkeypatch):
    from app.db import SessionLocal

    code = f"T-ISSUER-{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        try:
            monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", code)
            with pytest.raises(HTTPException) as err:      # a real code must exist: never invented
                await resolve_issuer_organization(db)
            assert err.value.status_code == 503

            db.add(Organization(id=uuid.uuid4(), type="association", name="Asociación de prueba",
                                code=code, status="active"))
            await db.flush()
            assert (await resolve_issuer_organization(db)).code == code

            monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", "PROTOTYPE")
            proto = await resolve_issuer_organization(db)     # placeholder is auto-created
            assert proto.code == "PROTOTYPE"
            created_proto = not (await db.execute(
                select(Organization).where(Organization.code == "PROTOTYPE", Organization.id != proto.id))).first()
        finally:
            await db.rollback()                              # nothing persists
