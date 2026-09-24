"""«Primer uso guiado» (migrations/018_onboarding.sql).

`GET /auth/me` carries `onboarding_completed_at` (null for a fresh account); `PATCH
/users/me/onboarding` sets it once, for finishing and for skipping alike, and a second call
keeps the first moment. The app decides who sees the guide; the API only remembers it.
"""

import uuid

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, DEFAULT_PASSWORD, fetch_all, fetch_one, module_factory, requires_db


def _column_exists() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name = 'users' AND column_name = 'onboarding_completed_at'"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(not _column_exists(), reason="apply migrations/018_onboarding.sql to the test database"),
]
factory = module_factory("onboarding")

AUTH = "/api/v1/auth"
ONBOARDING = "/api/v1/users/me/onboarding"


async def _audit(user: dict) -> list[str]:
    rows = await fetch_all(
        "SELECT action FROM audit_log WHERE user_id = :id AND action LIKE 'ONBOARDING_%'"
        " ORDER BY created_at",
        id=uuid.UUID(user["id"]),
    )
    return [row["action"] for row in rows]


async def test_a_new_account_has_not_seen_the_guide(client, factory):
    registered = await client.post(f"{AUTH}/register", json={
        "email": factory.email("fresh"), "password": DEFAULT_PASSWORD, "name": factory.name("fresh"),
    })
    assert registered.status_code == 201, registered.text
    login = await client.post(
        f"{AUTH}/login", json={"email": factory.email("fresh"), "password": DEFAULT_PASSWORD})
    token = login.json()["access_token"]

    me = await client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert "onboarding_completed_at" in me.json()
    assert me.json()["onboarding_completed_at"] is None
    # `/users/me` is the same shape.
    same = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert same.json()["onboarding_completed_at"] is None


async def test_finishing_marks_it_once_and_shows_in_me(client, factory):
    user = await factory.user("finisher")
    response = await client.patch(ONBOARDING, json={"outcome": "completed", "step": 4}, headers=user["headers"])
    assert response.status_code == 200, response.text
    first = response.json()["onboarding_completed_at"]
    assert first is not None

    me = await client.get(f"{AUTH}/me", headers=user["headers"])
    assert me.json()["onboarding_completed_at"] == first

    # A second call (another tab, a double click) keeps the first moment and audits nothing new.
    again = await client.patch(ONBOARDING, json={"outcome": "skipped"}, headers=user["headers"])
    assert again.status_code == 200
    assert again.json()["onboarding_completed_at"] == first
    assert await _audit(user) == ["ONBOARDING_COMPLETED"]
    row = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE user_id = :id AND action = 'ONBOARDING_COMPLETED'",
        id=uuid.UUID(user["id"]))
    assert row["metadata_json"] == {"step": 4}


async def test_skipping_marks_it_too(client, factory):
    user = await factory.user("skipper")
    response = await client.patch(ONBOARDING, json={"outcome": "skipped", "step": 1}, headers=user["headers"])
    assert response.status_code == 200
    assert response.json()["onboarding_completed_at"] is not None
    assert await _audit(user) == ["ONBOARDING_SKIPPED"]


async def test_an_empty_body_means_completed(client, factory):
    user = await factory.user("nobody")
    response = await client.patch(ONBOARDING, headers=user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["onboarding_completed_at"] is not None
    assert await _audit(user) == ["ONBOARDING_COMPLETED"]


async def test_it_only_touches_the_caller(client, factory):
    caller = await factory.user("caller")
    bystander = await factory.user("bystander")
    await client.patch(ONBOARDING, json={}, headers=caller["headers"])
    row = await fetch_one(
        "SELECT onboarding_completed_at FROM users WHERE id = :id", id=uuid.UUID(bystander["id"]))
    assert row["onboarding_completed_at"] is None


async def test_bad_input_and_no_session(client, factory):
    user = await factory.user("badinput")
    assert (await client.patch(ONBOARDING, json={})).status_code == 401
    bad = await client.patch(ONBOARDING, json={"outcome": "maybe"}, headers=user["headers"])
    assert bad.status_code == 422
    bad_step = await client.patch(ONBOARDING, json={"step": 0}, headers=user["headers"])
    assert bad_step.status_code == 422
    row = await fetch_one(
        "SELECT onboarding_completed_at FROM users WHERE id = :id", id=uuid.UUID(user["id"]))
    assert row["onboarding_completed_at"] is None
