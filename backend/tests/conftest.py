"""
Integration tests against a real PostgreSQL with migrations applied.

The environment is prepared BEFORE the app is imported, because the app reads
its settings and builds its engine at import time. If the database cannot be
reached, every test that needs it is skipped.
"""

import asyncio
import os
import uuid

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://test@127.0.0.1:55432/etl")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET"] = "test-only-secret-" + "x" * 32
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENV"] = "test"
for _name in (
    "ENVIRONMENT",
    "SENTRY_DSN",
    "RESEND_API_KEY",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_PRIVATE_BUCKET_NAME",
):
    os.environ.pop(_name, None)

import bcrypt  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import (  # noqa: E402
    SessionLocal,
    connect_args_for,
    engine,
    normalize_database_url,
)
from app.main import app  # noqa: E402
from app.security import create_access_token  # noqa: E402

# Every row created by this run carries this prefix, so cleanup is surgical.
RUN = "t" + uuid.uuid4().hex[:8]
DEFAULT_PASSWORD = "Sup3rSecret"
_FAST_HASH = bcrypt.hashpw(DEFAULT_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


def _database_is_local() -> bool:
    """These tests write rows. They only ever run against a database on this machine."""
    return connect_args_for(normalize_database_url(TEST_DATABASE_URL)) == {}


def _database_reachable() -> bool:
    if not _database_is_local():
        return False

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                await db.execute(text("SELECT 1 FROM users LIMIT 1"))
            return True
        except Exception:
            return False
        finally:
            # The probe runs on a throwaway loop; drop its connections.
            await engine.dispose()

    return asyncio.run(probe())


DB_AVAILABLE = _database_reachable()
requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="test database is unreachable or not local (set TEST_DATABASE_URL to a local Postgres)",
)


@pytest_asyncio.fixture(scope="session")
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await engine.dispose()


class Factory:
    """Creates rows directly in the database, all tagged with a module prefix."""

    def __init__(self, module: str):
        self.prefix = f"{RUN}-{module}"

    def email(self, label: str) -> str:
        return f"{self.prefix}-{label}@example.com"

    def name(self, label: str) -> str:
        return f"{self.prefix} {label}"

    async def user(
        self,
        label: str,
        role: str = "STUDENT",
        organization_id: str | None = None,
        *,
        is_minor: bool = False,
        password_hash: str | None = None,
        status: str = "ACTIVE",
    ) -> dict:
        user_id = uuid.uuid4()
        async with SessionLocal() as db:
            await db.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, name, role, organization_id,"
                    " is_minor, status) VALUES (:id, :email, :hash, :name, :role, :org,"
                    " :minor, :status)"
                ),
                {
                    "id": user_id,
                    "email": self.email(label),
                    "hash": password_hash or _FAST_HASH,
                    "name": self.name(label),
                    "role": role,
                    "org": uuid.UUID(organization_id) if organization_id else None,
                    "minor": is_minor,
                    "status": status,
                },
            )
            await db.commit()
        return {
            "id": str(user_id),
            "email": self.email(label),
            "headers": auth_headers(user_id),
        }

    async def org(self, label: str, org_type: str, parent: dict | None = None) -> dict:
        org_id = uuid.uuid4()
        path = f"{parent['path']}.{org_id.hex}" if parent else org_id.hex
        async with SessionLocal() as db:
            await db.execute(
                text(
                    "INSERT INTO organizations (id, parent_id, type, name, status, path)"
                    " VALUES (:id, :parent, :type, :name, 'active', text2ltree(:path))"
                ),
                {
                    "id": org_id,
                    "parent": uuid.UUID(parent["id"]) if parent else None,
                    "type": org_type,
                    "name": self.name(label),
                    "path": path,
                },
            )
            await db.commit()
        return {"id": str(org_id), "path": path}

    async def cleanup(self) -> None:
        like = f"{self.prefix}%"
        async with SessionLocal() as db:
            params = {"like": like}
            await db.execute(
                text(
                    "DELETE FROM audit_log WHERE user_email LIKE :like"
                    " OR user_id IN (SELECT id FROM users WHERE email LIKE :like)"
                    " OR entity_id IN (SELECT id::text FROM organizations WHERE name LIKE :like)"
                ),
                params,
            )
            honors = (
                "SELECT id FROM honors WHERE code LIKE :like OR name LIKE :like"
                " OR created_by_id IN (SELECT id FROM users WHERE email LIKE :like)"
            )
            users = "SELECT id FROM users WHERE email LIKE :like"
            has_portfolio = await db.scalar(text("SELECT to_regclass('public.honor_enrollments')"))
            if has_portfolio:
                # Certificates and enrollments point at honors without cascade: they go first.
                # Progress rows and evidences cascade from the enrollment.
                certificates = (
                    "SELECT id FROM certificates WHERE recipient_name LIKE :like"
                    f" OR user_id IN ({users}) OR honor_id IN ({honors})"
                )
                await db.execute(
                    text(f"DELETE FROM certificate_events WHERE certificate_id IN ({certificates})"),
                    params,
                )
                await db.execute(text(f"DELETE FROM certificates WHERE id IN ({certificates})"), params)
                await db.execute(
                    text(
                        "DELETE FROM clubs WHERE name LIKE :like OR organization_id IN"
                        " (SELECT id FROM organizations WHERE name LIKE :like)"
                    ),
                    params,
                )
                await db.execute(
                    text(
                        f"DELETE FROM honor_enrollments WHERE user_id IN ({users})"
                        f" OR honor_id IN ({honors})"
                    ),
                    params,
                )
            # Bloque B: church letters hang from the user and cascade, but they are deleted
            # explicitly so a module that only creates letters cleans up after itself too.
            if await db.scalar(text("SELECT to_regclass('public.church_letters')")):
                await db.execute(text(f"DELETE FROM church_letters WHERE user_id IN ({users})"), params)
            if await db.scalar(text("SELECT to_regclass('public.courses')")):
                # Courses point at honors and users without cascade, so they go first; their
                # lessons, plan and review rows cascade from the course. Newer versions point
                # at older ones, so the delete runs until nothing is left.
                courses = (
                    f"SELECT id FROM courses WHERE instructor_id IN ({users})"
                    f" OR honor_id IN ({honors})"
                )
                for _ in range(5):
                    deleted = await db.execute(
                        text(
                            f"DELETE FROM courses WHERE id IN ({courses})"
                            f" AND id NOT IN (SELECT previous_version_id FROM courses"
                            "  WHERE previous_version_id IS NOT NULL)"
                        ),
                        params,
                    )
                    if deleted.rowcount == 0:
                        break
            # Bloque F: activity logs cascade from the user, but a log can also point at a
            # club of this run whose member is not: delete by club too.
            if await db.scalar(text("SELECT to_regclass('public.activity_logs')")):
                await db.execute(
                    text(
                        f"DELETE FROM activity_logs WHERE user_id IN ({users})"
                        " OR club_id IN (SELECT id FROM organizations WHERE name LIKE :like)"
                    ),
                    params,
                )
            # Bloque F: programs are pointed at by enrollments and certificates, both
            # already deleted above; their sections, requirements and texts cascade. A
            # program is also pointed at by its next version and by the requirements that
            # ask for it, so the delete runs until nothing is left.
            if await db.scalar(text("SELECT to_regclass('public.programs')")):
                programs = "SELECT id FROM programs WHERE name LIKE :like OR slug LIKE :like"
                for _ in range(5):
                    deleted = await db.execute(
                        text(
                            f"DELETE FROM programs WHERE id IN ({programs})"
                            " AND id NOT IN (SELECT previous_version_id FROM programs"
                            "  WHERE previous_version_id IS NOT NULL)"
                            " AND id NOT IN (SELECT target_program_id FROM program_requirements"
                            "  WHERE target_program_id IS NOT NULL)"
                        ),
                        params,
                    )
                    if deleted.rowcount == 0:
                        break
            await db.execute(text(f"DELETE FROM honors WHERE id IN ({honors})"), params)
            # Bloque F: the categories a program requirement may point at.
            await db.execute(
                text("DELETE FROM honor_categories WHERE name LIKE :like OR slug LIKE :like"),
                params,
            )
            # Memberships cascade from the user, but a membership can also point
            # at an organization of this run whose member is not: delete by club
            # too, before the organizations go.
            if await db.scalar(text("SELECT to_regclass('public.club_memberships')")):
                clubs = "SELECT id FROM organizations WHERE name LIKE :like"
                await db.execute(
                    text(
                        f"DELETE FROM club_memberships WHERE user_id IN ({users})"
                        f" OR club_id IN ({clubs})"
                    ),
                    params,
                )
            if await db.scalar(text("SELECT to_regclass('public.club_invitations')")):
                await db.execute(
                    text(
                        "DELETE FROM notification_log WHERE email LIKE :like"
                        f" OR user_id IN ({users})"
                    ),
                    params,
                )
                await db.execute(
                    text(
                        f"DELETE FROM club_invitations WHERE created_by_id IN ({users})"
                        " OR club_id IN (SELECT id FROM organizations WHERE name LIKE :like)"
                    ),
                    params,
                )
            # Units (E5) are pointed at by memberships and invitations, which are
            # already gone by now, and they point at organizations and users.
            if await db.scalar(text("SELECT to_regclass('public.club_units')")):
                await db.execute(
                    text(
                        "DELETE FROM club_units WHERE club_id IN"
                        " (SELECT id FROM organizations WHERE name LIKE :like)"
                        f" OR counselor_id IN ({users})"
                    ),
                    params,
                )
            await db.execute(text("DELETE FROM users WHERE email LIKE :like"), params)
            await db.execute(text("DELETE FROM organizations WHERE name LIKE :like"), params)
            await db.commit()


def auth_headers(user_id) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def fetch_all(sql: str, **params) -> list:
    async with SessionLocal() as db:
        return (await db.execute(text(sql), params)).mappings().all()


async def fetch_one(sql: str, **params):
    rows = await fetch_all(sql, **params)
    return rows[0] if rows else None


def module_factory(module: str):
    """Module-scoped factory fixture that removes everything it created."""

    @pytest_asyncio.fixture(scope="module")
    async def factory():
        instance = Factory(module)
        yield instance
        await instance.cleanup()

    return factory
