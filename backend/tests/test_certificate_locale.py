"""The language a certificate is issued in (migrations/016_certificate_locale.sql).

Issuing stores `locale` next to the template; a locale the template has no translation for
is refused (422 `locale_not_supported`); `POST /certificates/render` with a folio prints in
the stored language unless the caller asks for another; rows from before 016 are Spanish.
"""

import re
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.certificates.render import load_template
from app.config import settings
from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_one, module_factory, requires_db

ENROLLMENTS = "/api/v1/portfolio/enrollments"
RENDER = "/api/v1/certificates/render"


def _locale_column_exists() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name = 'certificates' AND column_name = 'locale'"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(not _locale_column_exists(),
                       reason="apply migrations/016_certificate_locale.sql to the test database"),
]
factory = module_factory("certlocale")


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    issuer = await factory.org("issuer", "association")
    code = f"{factory.prefix}-ISS"
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET code = :code WHERE id = :id"),
                         {"code": code, "id": uuid.UUID(issuer["id"])})
        await db.commit()
    return {
        "member": await factory.user("member", "STUDENT", club["id"]),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "issuer_code": code,
    }


@pytest.fixture
def issuer(world, monkeypatch):
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


async def _ready_enrollment(client, factory, world, label: str) -> str:
    """A published one-requirement honor, enrolled, submitted and approved: READY."""
    honor_id = uuid.uuid4()
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO honors (id, ministry_id, name, slug, active, status)"
            " VALUES (:id, :m, :name, :slug, true, 'PUBLISHED')"),
            {"id": honor_id, "m": ministry, "name": factory.name(label), "slug": f"{factory.prefix}-{label}"})
        await db.execute(text(
            "INSERT INTO honor_requirements (honor_id, position, description, is_theoretical, locale)"
            " VALUES (:h, 1, 'Requisito', true, 'es')"), {"h": honor_id})
        await db.commit()
    member, director = world["member"], world["director"]
    enrolled = await client.post(ENROLLMENTS, json={"honor_id": str(honor_id)}, headers=member["headers"])
    assert enrolled.status_code == 201, enrolled.text
    eid = enrolled.json()["id"]
    sent = await client.put(f"{ENROLLMENTS}/{eid}/requirements/1",
                            json={"status": "SUBMITTED", "member_note": "Hecho"}, headers=member["headers"])
    assert sent.status_code == 200, sent.text
    approved = await client.post(f"{ENROLLMENTS}/{eid}/requirements/1/review",
                                 json={"verdict": "COMPLETE"}, headers=director["headers"])
    assert approved.status_code == 200, approved.text
    return eid


def _texts(svg: str) -> list[str]:
    return [line.replace("&amp;", "&") for line in re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg)]


async def test_issuing_stores_the_locale_and_exposes_it(client, factory, world, issuer):
    eid = await _ready_enrollment(client, factory, world, "frances")
    issued = await client.post(f"{ENROLLMENTS}/{eid}/certificate", headers=world["director"]["headers"], json={
        "template": "especialidad-modular-azul", "locale": "fr", "issued_date": "2026-09-22"})
    assert issued.status_code == 201, issued.text
    body = issued.json()
    assert body["locale"] == "fr" and body["template_slug"] == "especialidad-modular-azul"
    row = await fetch_one("SELECT locale FROM certificates WHERE certificate_no = :no", no=body["certificate_no"])
    assert row["locale"] == "fr"

    verified = (await client.get(f"/api/v1/certificates/verify/{body['certificate_no']}")).json()
    assert verified["valid"] is True                                   # the locale is outside the hash
    assert verified["locale"] == "fr" and verified["template_slug"] == "especialidad-modular-azul"
    mine = (await client.get("/api/v1/portfolio/me", headers=world["member"]["headers"])).json()
    listed = next(c for c in mine["certificates"] if c["certificate_no"] == body["certificate_no"])
    assert listed["locale"] == "fr" and listed["template_slug"] == "especialidad-modular-azul"


async def test_a_locale_the_template_does_not_speak_is_refused(client, factory, world, issuer):
    eid = await _ready_enrollment(client, factory, world, "sin-frances")
    url, headers = f"{ENROLLMENTS}/{eid}/certificate", world["director"]["headers"]
    assert "fr" not in load_template("especialidad-basica").locales
    for template, locale in (("especialidad-basica", "fr"), ("especialidad-modular-azul", "de")):
        refused = await client.post(url, headers=headers, json={
            "template": template, "locale": locale, "issued_date": "2026-09-22"})
        assert refused.status_code == 422 and refused.json()["detail"] == "locale_not_supported", refused.text
    assert (await client.post(url, headers=headers, json={
        "locale": "FR!", "issued_date": "2026-09-22"})).status_code == 422
    enrollment = await fetch_one("SELECT status FROM honor_enrollments WHERE id = :id", id=uuid.UUID(eid))
    assert enrollment["status"] == "READY"                             # nothing was issued

    default = await client.post(url, headers=headers, json={"template": "especialidad-basica", "issued_date": "2026-09-22"})
    assert default.status_code == 201 and default.json()["locale"] == "es"


async def test_render_prints_in_the_stored_locale_unless_asked_otherwise(client, factory, world, issuer, monkeypatch):
    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)
    eid = await _ready_enrollment(client, factory, world, "render")
    issued = await client.post(f"{ENROLLMENTS}/{eid}/certificate", headers=world["director"]["headers"], json={
        "template": "especialidad-modular-azul", "locale": "fr", "issued_date": "2026-09-22"})
    assert issued.status_code == 201, issued.text
    folio = issued.json()["certificate_no"]
    french = load_template("especialidad-modular-azul").strings["fr"]["church_name"]

    stored = await client.post(RENDER, json={"template": "especialidad-modular-azul", "format": "svg",
                                             "certificate_no": folio})
    assert stored.status_code == 200, stored.text
    assert "22 septembre 2026" in _texts(stored.text) and french in _texts(stored.text)

    english = await client.post(RENDER, json={"template": "especialidad-modular-azul", "format": "svg",
                                              "certificate_no": folio, "locale": "en"})
    assert english.status_code == 200, english.text
    assert "September 22, 2026" in _texts(english.text) and french not in _texts(english.text)

    no_folio = await client.post(RENDER, json={"template": "especialidad-basica", "format": "svg"})
    assert no_folio.status_code == 200                                 # without a folio: Spanish as before


async def test_rows_from_before_the_migration_are_spanish(client, factory, world, issuer):
    column = await fetch_one(
        "SELECT column_default, is_nullable, character_maximum_length FROM information_schema.columns"
        " WHERE table_name = 'certificates' AND column_name = 'locale'")
    assert column["is_nullable"] == "NO" and column["column_default"].startswith("'es'")
    assert column["character_maximum_length"] == 8

    batch = await client.post("/api/v1/certificates/prototype-batch", json={
        "recipient_names": [factory.name("Ana")], "honor_name": factory.name("Viejo"),
        "club_name": factory.name("club-viejo"), "issued_date": "2026-09-22", "width_in": 11, "height_in": 8.5})
    assert batch.status_code == 201, batch.text
    folio = batch.json()[0]["certificate_no"]
    async with SessionLocal() as db:        # what a row written before 016 looks like after it
        await db.execute(text("UPDATE certificates SET locale = DEFAULT WHERE certificate_no = :no"), {"no": folio})
        await db.commit()
    verified = (await client.get(f"/api/v1/certificates/verify/{folio}")).json()
    assert verified["valid"] is True and verified["locale"] == "es"
    # The «Prototipo WxHin» templates the assistant wrote before it sent its slug have none.
    from app.services.certificates import get_or_create_template, template_slug

    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug = 'pathfinders'"))).scalar_one()
        legacy = await get_or_create_template(db, ministry, "Prototipo 11x8.5in", 11, 8.5)
        await db.commit()
        assert await template_slug(db, legacy.id) is None


async def _prototype(client, factory, **extra):
    return await client.post("/api/v1/certificates/prototype-batch", json={
        "recipient_names": [factory.name("Ana")], "honor_name": factory.name("Asistente"),
        "club_name": factory.name("club-asistente"), "issued_date": "2026-09-24",
        "width_in": 11, "height_in": 8.5, **extra})


async def test_the_assistant_batch_keeps_the_template_it_rendered(client, factory, world, issuer):
    """`template` (a slug of the engine) is kept like the portfolio keeps it, so /verify offers
    PNG/PDF for these folios; without it, the default template; an unknown one is a 422."""
    from app.services.portfolio import DEFAULT_CERTIFICATE_TEMPLATE

    chosen = await _prototype(client, factory, template="especialidad-modular-azul")
    assert chosen.status_code == 201, chosen.text
    folio = chosen.json()[0]["certificate_no"]
    verified = (await client.get(f"/api/v1/certificates/verify/{folio}")).json()
    assert verified["valid"] is True and verified["template_slug"] == "especialidad-modular-azul"
    stored = await fetch_one(
        "SELECT t.name, t.supports_svg, t.width, t.height FROM certificates c"
        " JOIN certificate_templates t ON t.id = c.template_id WHERE c.certificate_no = :no", no=folio)
    template = load_template("especialidad-modular-azul")
    assert stored["name"] == "especialidad-modular-azul" and stored["supports_svg"] is True
    assert stored["width"] == round(template.width_pt / 72, 4) and stored["height"] == round(template.height_pt / 72, 4)
    # ...and the folio renders again on its template (what /verify's download does).
    again = await client.post(RENDER, json={"template": verified["template_slug"], "format": "svg",
                                            "certificate_no": folio})
    assert again.status_code == 200, again.text

    default = await _prototype(client, factory)
    assert default.status_code == 201, default.text
    verified = (await client.get(f"/api/v1/certificates/verify/{default.json()[0]['certificate_no']}")).json()
    assert verified["template_slug"] == DEFAULT_CERTIFICATE_TEMPLATE

    before = await fetch_one("SELECT count(*) AS n FROM certificates")
    unknown = await _prototype(client, factory, template="no-existe-esta")
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["detail"]["code"] == "template_not_found"
    assert (await fetch_one("SELECT count(*) AS n FROM certificates"))["n"] == before["n"]
    assert (await _prototype(client, factory, template="../etc")).status_code == 422
