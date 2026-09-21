"""The catalogue answers in the reader's language; Spanish is the source and the fallback."""
import uuid

from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import module_factory, requires_db

pytestmark = requires_db
factory = module_factory("hontr")
HONORS = "/api/v1/honors"


async def _published_honor(factory, label: str, translations: dict[str, str]) -> dict:
    honor_id, name = uuid.uuid4(), factory.name(label)
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO honors (id, ministry_id, name, slug, active, status, wiki_title, authority, skill_level,"
            " year_introduced) VALUES (:id, :m, :name, :slug, true, 'PUBLISHED', 'Knot Tying', 'GC', 2, 1928)"),
            {"id": honor_id, "m": ministry, "name": name, "slug": f"{factory.prefix}-{label}"})
        for locale, translated in translations.items():
            await db.execute(text(
                "INSERT INTO honor_translations (honor_id, locale, name, source, source_url, license)"
                " VALUES (:id, :locale, :name, 'pathfinder-wiki', 'https://wiki.pathfindersonline.org/w/AY_Honors/Knot_Tying',"
                " 'CC BY-SA 3.0')"), {"id": honor_id, "locale": locale, "name": translated})
        await db.commit()
    return {"id": str(honor_id), "name": name}


async def _find(client, honor, **params):
    response = await client.get(HONORS, params={"q": params.pop("q", honor["name"]), **params},
                                headers=params.pop("headers", None))
    assert response.status_code == 200, response.text
    return next((row for row in response.json() if row["id"] == honor["id"]), None)


async def test_names_follow_the_requested_locale(client, factory):
    english = factory.name("Knot Tying")
    honor = await _published_honor(factory, "nudos", {"en": english, "pt-BR": factory.name("Nós e amarras")})

    spanish = await _find(client, honor)
    assert spanish["name"] == honor["name"] and spanish["name_locale"] == "es"
    assert (spanish["authority"], spanish["skill_level"], spanish["year_introduced"]) == ("GC", 2, 1928)

    row = await _find(client, honor, locale="en")
    assert row["name"] == english and row["name_locale"] == "en" and row["original_name"] == honor["name"]
    assert (await _find(client, honor, locale="en-US"))["name"] == english            # region falls back to language
    assert (await _find(client, honor, locale="pt"))["name_locale"] == "pt-BR"        # language finds its only region
    assert (await _find(client, honor, locale="fr"))["name_locale"] == "es"           # nothing in French: source text

    by_translation = await _find(client, honor, q=english, locale="en")                # search works in that language
    assert by_translation is not None
    header = await client.get(HONORS, params={"q": honor["name"]}, headers={"Accept-Language": "en-GB,en;q=0.9,es;q=0.5"})
    assert next(r for r in header.json() if r["id"] == honor["id"])["name"] == english

    detail = await client.get(f"{HONORS}/{honor['id']}", params={"locale": "en"})
    assert detail.status_code == 200 and detail.json()["name"] == english
    assert (await client.get(HONORS, params={"locale": "not a locale"})).status_code == 422


async def test_category_names_are_translated(client, factory):
    async with SessionLocal() as db:
        category = (await db.execute(text(
            "SELECT c.id, c.slug, c.name FROM honor_categories c JOIN ministries m ON m.id=c.ministry_id"
            " WHERE m.slug='pathfinders' ORDER BY c.slug LIMIT 1"))).mappings().one()
        await db.execute(text(
            "INSERT INTO honor_category_translations (category_id, locale, name) VALUES (:id, 'xx', :name)"
            " ON CONFLICT (category_id, locale) DO UPDATE SET name = EXCLUDED.name"),
            {"id": category["id"], "name": factory.name("translated")})
        await db.commit()
    try:
        translated = await client.get(f"{HONORS}/categories", params={"locale": "xx"})
        assert next(c for c in translated.json() if c["slug"] == category["slug"])["name"] == factory.name("translated")
        source = await client.get(f"{HONORS}/categories")
        assert next(c for c in source.json() if c["slug"] == category["slug"])["name"] == category["name"]
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM honor_category_translations WHERE locale='xx'"))
            await db.commit()
