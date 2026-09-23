"""Especialidades sin requisitos: the admin console lists and counts the honors whose
requirements are missing (no rows) or only exist in a foreign language (no `es` rows), so the
owner can review them and upload the requirements.
"""

import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import module_factory, requires_db

pytestmark = requires_db
factory = module_factory("hgaps")

HONORS = "/api/v1/honors"
STATS = f"{HONORS}/stats/overview"
NEW_FIELDS = {"requirements_count", "requirements_locale"}
# A token of its own for `q`: the prefix alone could trigram-match other modules' honors.
TOKEN = "gap" + uuid.uuid4().hex[:10]


@pytest_asyncio.fixture(scope="module")
async def staff(factory):
    division = await factory.org("div", "division")
    association = await factory.org("assoc", "association", division)
    zone = await factory.org("zone", "zone", association)
    return {
        "master": await factory.user("master", "MASTER_GC"),
        "instructor": await factory.user("instructor", "INSTRUCTOR", zone["id"]),
        "student": await factory.user("student", "STUDENT", zone["id"]),
    }


async def _create(client, staff, factory, label: str, requirements: int = 2) -> str:
    body = {
        "name": factory.name(f"{TOKEN} {label}"),
        "code": f"{factory.prefix}-{label}"[:40],
        "category": "recreation",
        "requirements": [{"description": f"Requisito {n}"} for n in range(1, requirements + 1)],
    }
    response = await client.post(HONORS, json=body, headers=staff["master"]["headers"])
    assert response.status_code == 201, response.text
    honor_id = response.json()["id"]
    published = await client.post(f"{HONORS}/{honor_id}/publish", headers=staff["master"]["headers"])
    assert published.status_code == 200, published.text
    return honor_id


async def _sql(statement: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(statement), params)
        await db.commit()


@pytest_asyncio.fixture(scope="module")
async def honors(client, staff, factory):
    """complete: 3 `es` rows (+1 `en` that must not count); english: 2 rows only in `en`;
    missing: no rows at all."""
    complete = await _create(client, staff, factory, "complete", requirements=3)
    await _sql(
        "INSERT INTO honor_requirements (id, honor_id, position, description, locale)"
        " VALUES (:id, :honor, 1, 'Requirement 1', 'en')",
        id=uuid.uuid4(), honor=uuid.UUID(complete),
    )
    english = await _create(client, staff, factory, "english", requirements=2)
    await _sql("UPDATE honor_requirements SET locale = 'en' WHERE honor_id = :id", id=uuid.UUID(english))
    missing = await _create(client, staff, factory, "missing", requirements=1)
    await _sql("DELETE FROM honor_requirements WHERE honor_id = :id", id=uuid.UUID(missing))
    return {"complete": complete, "english": english, "missing": missing}


async def _list(client, staff, who="master", **params):
    headers = staff[who]["headers"] if who else None
    return await client.get(HONORS, params={"q": TOKEN, **params}, headers=headers)


async def test_staff_list_carries_requirement_counts_in_the_source_locale(client, staff, honors):
    response = await _list(client, staff, status="ALL")
    assert response.status_code == 200, response.text
    by_id = {row["id"]: row for row in response.json()}
    assert by_id[honors["complete"]]["requirements_count"] == 3
    assert by_id[honors["complete"]]["requirements_locale"] == "es"
    assert by_id[honors["english"]]["requirements_count"] == 2
    assert by_id[honors["english"]]["requirements_locale"] == "en"
    assert by_id[honors["missing"]]["requirements_count"] == 0
    assert by_id[honors["missing"]]["requirements_locale"] is None

    # Also without `status` (the published catalogue as a staff member sees it).
    plain = {row["id"]: row for row in (await _list(client, staff)).json()}
    assert plain[honors["english"]]["requirements_locale"] == "en"


async def test_content_filter_and_total_header(client, staff, honors):
    expected = {
        "missing_requirements": {honors["missing"]},
        "foreign_only": {honors["english"]},
        "complete": {honors["complete"]},
    }
    for content, ids in expected.items():
        response = await _list(client, staff, status="ALL", content=content)
        assert response.status_code == 200, response.text
        assert {row["id"] for row in response.json()} == ids, content
        assert response.headers["X-Total-Count"] == str(len(ids)), content

    # Combinable with `status` and `category`.
    published = await _list(client, staff, status="PUBLISHED", content="missing_requirements")
    assert {row["id"] for row in published.json()} == {honors["missing"]}
    other_category = await _list(client, staff, status="ALL", content="complete", category="nature")
    assert other_category.json() == [] and other_category.headers["X-Total-Count"] == "0"
    same_category = await _list(client, staff, content="foreign_only", category="recreation")
    assert {row["id"] for row in same_category.json()} == {honors["english"]}

    # Pagination keeps the filtered total.
    paged = await _list(client, staff, status="ALL", content="complete", limit=1, offset=1)
    assert paged.json() == [] and paged.headers["X-Total-Count"] == "1"

    invalid = await _list(client, staff, status="ALL", content="nope")
    assert invalid.status_code == 422


async def test_instructor_is_staff_for_the_counts(client, staff, honors):
    # The instructor did not author these honors: only the public catalogue is visible, but the
    # counts (a staff tool) still come along.
    response = await _list(client, staff, who="instructor", content="missing_requirements")
    assert response.status_code == 200
    assert {row["id"] for row in response.json()} == {honors["missing"]}
    assert response.json()[0]["requirements_count"] == 0


async def test_public_list_is_unchanged(client, staff, honors):
    for who in (None, "student"):
        response = await _list(client, staff, who=who, content="missing_requirements")
        assert response.status_code == 200, response.text
        rows = response.json()
        # `content` is ignored: the three published honors are all there, without the new fields.
        assert {row["id"] for row in rows} == set(honors.values()), who
        assert response.headers["X-Total-Count"] == "3"
        for row in rows:
            assert not NEW_FIELDS & set(row), row


async def test_stats_count_missing_and_foreign_only(client, staff, honors, factory):
    body = (await client.get(STATS, headers=staff["master"]["headers"])).json()
    assert body["missing_requirements"] >= 1
    assert body["foreign_only"] >= 1

    # Exact deltas: one more honor of each kind moves each counter by one.
    extra_missing = await _create(client, staff, factory, "missing2", requirements=1)
    await _sql("DELETE FROM honor_requirements WHERE honor_id = :id", id=uuid.UUID(extra_missing))
    extra_english = await _create(client, staff, factory, "english2", requirements=1)
    await _sql("UPDATE honor_requirements SET locale = 'en' WHERE honor_id = :id", id=uuid.UUID(extra_english))
    after = (await client.get(STATS, headers=staff["master"]["headers"])).json()
    assert after["missing_requirements"] == body["missing_requirements"] + 1
    assert after["foreign_only"] == body["foreign_only"] + 1
    assert after["total_honors"] == body["total_honors"] + 2

