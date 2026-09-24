"""Regresiones de la revisión de seguridad de 2026-09 (docs/SEGURIDAD_REVISION_2026-09.md).

Cada prueba lleva el identificador del hallazgo que cierra (SEC-xx).
"""

import uuid

from sqlalchemy import text

from app.db import SessionLocal

from tests.conftest import fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("secrev")

USERS = "/api/v1/users"
PORTFOLIO = "/api/v1/portfolio"


async def _verify(user: dict) -> dict:
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE users SET verification_status = 'VERIFIED' WHERE id = :id"),
            {"id": uuid.UUID(user["id"])},
        )
        await db.commit()
    return user


# ----------------------------------------------------------------------------
# SEC-01: nadie se nombra a sí mismo tutor aprobado de un menor.
# ----------------------------------------------------------------------------
async def test_sec01_a_self_declared_guardian_cannot_approve_their_own_link(client, factory):
    child = await factory.user("sec01-child", is_minor=True)
    stranger = await _verify(await factory.user("sec01-stranger", role="PARENT_GUARDIAN"))

    created = await client.post(
        f"{USERS}/guardianships", json={"child_id": child["id"]}, headers=stranger["headers"]
    )
    assert created.status_code == 201, created.text
    link = created.json()
    assert link["consent_status"] == "PENDING"

    approved = await client.post(
        f"{USERS}/guardianships/{link['id']}/approve", headers=stranger["headers"]
    )
    assert approved.status_code == 403
    row = await fetch_one(
        "SELECT consent_status FROM guardianships WHERE id = :id", id=uuid.UUID(link["id"])
    )
    assert row["consent_status"] == "PENDING"

    # Sin tutoría aprobada no hay portafolio ni fotos del menor.
    portfolio = await client.get(f"{PORTFOLIO}/users/{child['id']}", headers=stranger["headers"])
    assert portfolio.status_code in (403, 404)

    # Retirarse sigue siendo posible.
    withdrawn = await client.post(
        f"{USERS}/guardianships/{link['id']}/reject", headers=stranger["headers"]
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["consent_status"] == "REJECTED"


async def test_sec01_an_unverified_adult_cannot_declare_a_guardianship(client, factory):
    child = await factory.user("sec01-child2", is_minor=True)
    unverified = await factory.user("sec01-unverified", role="PARENT_GUARDIAN")
    refused = await client.post(
        f"{USERS}/guardianships", json={"child_id": child["id"]}, headers=unverified["headers"]
    )
    assert refused.status_code == 403
