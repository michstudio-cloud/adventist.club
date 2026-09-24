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


# ----------------------------------------------------------------------------
# SEC-02: la tutoría no sobrevive a los 18 años.
# ----------------------------------------------------------------------------
async def test_sec02_a_guardianship_stops_opening_the_portfolio_of_an_adult(client, factory):
    grown = await factory.user("sec02-grown", is_minor=True)
    guardian = await _verify(await factory.user("sec02-guardian", role="PARENT_GUARDIAN"))
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO guardianships (guardian_id, child_id, consent_status)"
                " VALUES (:g, :c, 'APPROVED')"
            ),
            {"g": uuid.UUID(guardian["id"]), "c": uuid.UUID(grown["id"])},
        )
        await db.commit()
    url = f"{PORTFOLIO}/users/{grown['id']}"
    assert (await client.get(url, headers=guardian["headers"])).status_code == 200

    # Cumple 18: fecha de nacimiento de hace 19 años y la bandera ya no dice menor.
    async with SessionLocal() as db:
        await db.execute(
            text(
                "UPDATE users SET is_minor = false,"
                " birth_date = (now() - interval '19 years')::date WHERE id = :id"
            ),
            {"id": uuid.UUID(grown["id"])},
        )
        await db.commit()
    assert (await client.get(url, headers=guardian["headers"])).status_code in (403, 404)


# ----------------------------------------------------------------------------
# SEC-03: el lote «prototipo» (sin sesión) no se verifica como un certificado oficial.
# ----------------------------------------------------------------------------
async def test_sec03_an_anonymous_prototype_certificate_is_not_official(client, factory):
    payload = {"recipient_names": [factory.name("Falso")], "honor_name": factory.name("Honor"),
               "club_name": factory.name("club"), "issued_date": "2026-09-22",
               "width_in": 11, "height_in": 8.5}
    created = await client.post("/api/v1/certificates/prototype-batch", json=payload)
    assert created.status_code == 201, created.text
    number = created.json()[0]["certificate_no"]

    verified = (await client.get(f"/api/v1/certificates/verify/{number}")).json()
    assert verified["official"] is False

    # El mismo registro, emitido por una persona del club (la vía del portafolio), sí lo es.
    issuer = await factory.user("sec03-issuer", role="CLUB_DIRECTOR")
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE certificates SET issued_by_id = :u WHERE certificate_no = :no"),
            {"u": uuid.UUID(issuer["id"]), "no": number},
        )
        await db.commit()
    verified = (await client.get(f"/api/v1/certificates/verify/{number}")).json()
    assert verified["official"] is True


async def test_sec03_the_open_endpoints_are_bounded(client, factory):
    too_many = {"recipient_names": ["Ana"] * 201, "honor_name": "Honor", "club_name": "Club",
                "issued_date": "2026-09-22", "width_in": 11, "height_in": 8.5}
    assert (await client.post("/api/v1/certificates/prototype-batch", json=too_many)).status_code == 422
    huge = {"recipient_names": ["Ana"], "honor_name": "Honor", "club_name": "Club",
            "issued_date": "2026-09-22", "width_in": 400, "height_in": 8.5}
    assert (await client.post("/api/v1/certificates/prototype-batch", json=huge)).status_code == 422

    pdf = {"images": ["data:image/png;base64,AAAA"] * 501, "page_width_in": 11,
           "page_height_in": 8.5, "item_width_in": 1, "item_height_in": 1}
    assert (await client.post("/api/v1/printing/pdf", json=pdf)).status_code == 422


# ----------------------------------------------------------------------------
# SEC-08: PATCH /users/{id} no acepta un avatar de cualquier sitio.
# ----------------------------------------------------------------------------
async def test_sec08_the_avatar_is_a_file_of_the_platform(client, factory):
    from app.config import settings

    adult = await factory.user("sec08-adult")
    url = f"{USERS}/{adult['id']}"
    tracker = await client.patch(
        url, json={"avatar_url": "https://evil.example/pixel.gif"}, headers=adult["headers"]
    )
    assert tracker.status_code == 422
    assert tracker.json()["detail"] == "invalid_avatar_url"
    own = f"{settings.R2_PUBLIC_URL.rstrip('/')}/avatars/{uuid.uuid4()}.png"
    saved = await client.patch(url, json={"avatar_url": own}, headers=adult["headers"])
    assert saved.status_code == 200, saved.text
    # Re-sending the value already stored (the profile form sends it back) stays accepted.
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE users SET avatar_url = 'https://legacy.example/a.png' WHERE id = :id"),
            {"id": uuid.UUID(adult["id"])},
        )
        await db.commit()
    same = await client.patch(
        url, json={"name": "Sec08", "avatar_url": "https://legacy.example/a.png"},
        headers=adult["headers"],
    )
    assert same.status_code == 200, same.text
