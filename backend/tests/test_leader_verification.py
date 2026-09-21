"""Bloque E, incremento E7 — La carta de la iglesia para los cargos del club.

Decisión tomada: hay UNA sola tabla de cartas en la plataforma. El bloque B ya
creó `church_letters` con el doble escalón zona -> Asociación y el bucket
privado; E7 la reutiliza ampliando `role_requested` y añade lo suyo encima: la
gracia de 60 días del director (D4), la vigencia de 12 meses (D5) y la puerta
única `rbac.may_handle_minors`, detrás de un interruptor que nace APAGADO.

El bucket privado es siempre un doble: ningún test sale a la red.
"""

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from botocore.exceptions import ClientError
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import email as email_service
from app.services import private_storage
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

LETTERS = "/api/v1/church-letters"
CLUBS = "/api/v1/clubs"
MEMBERSHIPS = "/api/v1/memberships"
PORTFOLIO = "/api/v1/portfolio"
USERS = "/api/v1/users"

MINOR_BIRTH_DATE = date(2014, 5, 4)


def _table_exists(name: str) -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(await db.scalar(text(f"SELECT to_regclass('public.{name}') IS NOT NULL")))
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _table_exists("church_letters"),
        reason="apply migrations/009_church_letters.sql and 008f_leader_verification.sql",
    ),
]
needs_portfolio = pytest.mark.skipif(
    not _table_exists("honor_enrollments"),
    reason="apply migrations/007_portfolio.sql to the test database",
)
factory = module_factory("leaderverif")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
async def _set(user: dict, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    async with SessionLocal() as db:
        await db.execute(
            text(f"UPDATE users SET {assignments} WHERE id = :id"),
            {**columns, "id": uuid.UUID(user["id"])},
        )
        await db.commit()


async def _join(user: dict, club: dict, role: str) -> str:
    membership_id = uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO club_memberships (id, user_id, club_id, role, status, source,"
                " started_at) VALUES (:id, :user, :club, :role, 'ACTIVE', 'BACKFILL', now())"
            ),
            {
                "id": membership_id,
                "user": uuid.UUID(user["id"]),
                "club": uuid.UUID(club["id"]),
                "role": role,
            },
        )
        await db.execute(
            text("UPDATE users SET organization_id = :club, role = :role WHERE id = :user"),
            {"club": uuid.UUID(club["id"]), "role": role, "user": uuid.UUID(user["id"])},
        )
        await db.commit()
    user["membership_id"] = str(membership_id)
    return str(membership_id)


class FakePrivateR2:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.presigned: list[dict] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presigned.append({"operation": operation, "expires": ExpiresIn, **Params})
        return f"https://r2.test/{Params['Bucket']}/{Params['Key']}?op={operation}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject"
            )
        return self.objects[Key]

    def store_last_upload(self, size: int, content_type: str) -> str:
        key = self.presigned[-1]["Key"]
        self.objects[key] = {"ContentLength": size, "ContentType": content_type}
        return key


@pytest.fixture
def r2(monkeypatch):
    fake = FakePrivateR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET_NAME", "letters-test")
    monkeypatch.setattr(private_storage, "get_client", lambda: fake)
    return fake


@pytest.fixture
def enforced(monkeypatch):
    """Turn the deploy switch on, as the owner will once every association has
    a validator. Without it `may_handle_minors` is always true."""
    monkeypatch.setattr(
        settings, "LEADER_VERIFICATION_ENFORCED_FROM", date.today() - timedelta(days=1)
    )


@pytest.fixture
def mails(monkeypatch):
    sent = []

    async def submitted(to, reviewer_name, applicant_name, role, link):
        sent.append({"kind": "submitted", "to": to, "applicant": applicant_name, "role": role})
        return True

    async def decision(to, name, status, valid_until=None, note=None):
        sent.append({"kind": "decision", "to": to, "status": status, "valid_until": valid_until})
        return True

    monkeypatch.setattr(email_service, "send_letter_submitted_email", submitted)
    monkeypatch.setattr(email_service, "send_letter_decision_email", decision)
    return sent


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    zone = await factory.org("zona", "zone", association)
    church = await factory.org("iglesia", "church", zone)
    club = await factory.org("club", "club", church)
    other_zone = await factory.org("otra-zona", "zone", association)
    other_association = await factory.org("otra-assoc", "association")

    people = {
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "counselor": await factory.user("counselor", "COUNSELOR", club["id"]),
        "minor": await factory.user("minor", "STUDENT", club["id"], is_minor=True),
        "adult_member": await factory.user("adult-member", "STUDENT", club["id"]),
        "coord": await factory.user("coord", "COORDINATOR_ZONE", zone["id"]),
        "other_coord": await factory.user("other-coord", "COORDINATOR_ZONE", other_zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "outsider_admin": await factory.user(
            "outsider-admin", "ADMIN_ASSOCIATION", other_association["id"]
        ),
        "master": await factory.user("master", "MASTER_GC"),
    }
    for label, role in (
        ("director", "CLUB_DIRECTOR"),
        ("instructor", "INSTRUCTOR"),
        ("counselor", "COUNSELOR"),
        ("minor", "STUDENT"),
        ("adult_member", "STUDENT"),
    ):
        await _join(people[label], club, role)
    await _set(people["minor"], birth_date=MINOR_BIRTH_DATE)
    for label in ("director", "instructor", "counselor"):
        await _set(people[label], verification_status="VERIFIED", child_protection_completed=True)
    # The director's club was approved by a human a long time ago: their grace
    # window (D4) is over unless a test says otherwise.
    await _set(
        people["director"],
        club_approval="APPROVED",
        club_approval_at=date.today() - timedelta(days=400),
    )
    return {**people, "association": association, "zone": zone, "church": church, "club": club}


async def _present(client, user, r2, **extra) -> dict:
    body = {
        "church_name": "Iglesia Central",
        "content_type": "application/pdf",
        "size_bytes": 4096,
        **extra,
    }
    created = await client.post(LETTERS, json=body, headers=user["headers"])
    assert created.status_code == 201, created.text
    r2.store_last_upload(body["size_bytes"], body["content_type"])
    completed = await client.post(
        f"{LETTERS}/{created.json()['letter']['id']}/complete", headers=user["headers"]
    )
    assert completed.status_code == 200, completed.text
    return completed.json()


async def _authorize(client, world, letter_id, actor=None, **payload):
    """Zone validates, association authorizes: the two steps Bloque B shipped."""
    validated = await client.post(
        f"{LETTERS}/{letter_id}/review",
        json={"action": "VALIDATE"},
        headers=world["coord"]["headers"],
    )
    assert validated.status_code == 200, validated.text
    return await client.post(
        f"{LETTERS}/{letter_id}/review",
        json={"action": "AUTHORIZE", **payload},
        headers=(actor or world["assoc_admin"])["headers"],
    )


async def _verified_until(user: dict):
    row = await fetch_one("SELECT leader_verified_until FROM users WHERE id = :id", id=user["id"])
    return row["leader_verified_until"]


# ----------------------------------------------------------------------------
# The switch: OFF changes nothing
# ----------------------------------------------------------------------------
async def test_with_the_switch_off_nothing_changes(world):
    from app.models import User
    from app.rbac import may_handle_minors

    assert settings.LEADER_VERIFICATION_ENFORCED_FROM is None
    async with SessionLocal() as db:
        instructor = await db.get(User, uuid.UUID(world["instructor"]["id"]))
        assert may_handle_minors(instructor) is True  # nobody is blocked yet


async def test_the_switch_only_bites_from_its_own_day(world, monkeypatch):
    from app.models import User
    from app.rbac import may_handle_minors

    async with SessionLocal() as db:
        instructor = await db.get(User, uuid.UUID(world["instructor"]["id"]))
        monkeypatch.setattr(
            settings, "LEADER_VERIFICATION_ENFORCED_FROM", date.today() + timedelta(days=10)
        )
        assert may_handle_minors(instructor) is True
        monkeypatch.setattr(settings, "LEADER_VERIFICATION_ENFORCED_FROM", date.today())
        assert may_handle_minors(instructor) is False


async def test_a_malformed_switch_never_turns_enforcement_on():
    from app.config import Settings

    settings_with_junk = Settings(
        DATABASE_URL="postgresql://x/y", LEADER_VERIFICATION_ENFORCED_FROM="mañana"
    )
    assert settings_with_junk.LEADER_VERIFICATION_ENFORCED_FROM is None


# ----------------------------------------------------------------------------
# Presenting, validating and the copy on the account
# ----------------------------------------------------------------------------
async def test_a_director_presents_a_letter_and_the_association_authorizes_it(
    client, world, r2, mails, factory
):
    letter = await _present(client, world["director"], r2)
    assert letter["role_requested"] == "CLUB_DIRECTOR"  # the office they hold
    assert letter["status"] == "SUBMITTED"
    # Whoever can decide on it was told; the applicant is an adult, so naming
    # them is fine — no minor is ever named to a third party.
    assert {mail["to"] for mail in mails if mail["kind"] == "submitted"} >= {
        world["coord"]["email"],
        world["assoc_admin"]["email"],
    }
    assert all(
        mail["applicant"] == factory.name("director")
        for mail in mails
        if mail["kind"] == "submitted"
    )

    authorized = await _authorize(client, world, letter["id"])
    assert authorized.status_code == 200, authorized.text
    # Decision D5: 12 months when nobody says otherwise.
    assert authorized.json()["valid_until"] == (date.today() + timedelta(days=365)).isoformat()
    assert await _verified_until(world["director"]) == date.today() + timedelta(days=365)
    assert [m for m in mails if m["kind"] == "decision"][-1] == {
        "kind": "decision",
        "to": world["director"]["email"],
        "status": "AUTHORIZED",
        "valid_until": (date.today() + timedelta(days=365)).isoformat(),
    }

    checklist = await client.get(f"{LETTERS}/me", headers=world["director"]["headers"])
    assert checklist.json()["verified"] is True
    assert checklist.json()["expires_soon"] is False


async def test_a_letter_never_lasts_more_than_twenty_four_months(client, world, r2):
    letter = await _present(client, world["counselor"], r2)
    too_long = await _authorize(
        client,
        world,
        letter["id"],
        valid_until=(date.today() + timedelta(days=900)).isoformat(),
    )
    assert too_long.status_code == 422, too_long.text
    in_the_past = await client.post(
        f"{LETTERS}/{letter['id']}/review",
        json={"action": "AUTHORIZE", "valid_until": (date.today() - timedelta(days=1)).isoformat()},
        headers=world["assoc_admin"]["headers"],
    )
    assert in_the_past.status_code == 422, in_the_past.text

    ok = await client.post(
        f"{LETTERS}/{letter['id']}/review",
        json={"action": "AUTHORIZE", "valid_until": (date.today() + timedelta(days=200)).isoformat()},
        headers=world["assoc_admin"]["headers"],
    )
    assert ok.status_code == 200, ok.text
    assert await _verified_until(world["counselor"]) == date.today() + timedelta(days=200)


async def test_who_may_validate_a_letter(client, world, r2, factory):
    person = await factory.user("matrix-instructor", "INSTRUCTOR", world["club"]["id"])
    await _join(person, world["club"], "INSTRUCTOR")
    await _set(person, verification_status="VERIFIED", child_protection_completed=True)
    letter = await _present(client, person, r2)

    for label in ("other_coord", "outsider_admin"):
        denied = await client.post(
            f"{LETTERS}/{letter['id']}/review",
            json={"action": "VALIDATE"},
            headers=world[label]["headers"],
        )
        assert denied.status_code == 403, (label, denied.text)
    # The queue says the same thing: another zone never reads these letters.
    queue = await client.get(f"{LETTERS}/queue", headers=world["other_coord"]["headers"])
    assert letter["id"] not in [row["id"] for row in queue.json()]
    mine = await client.get(f"{LETTERS}/queue", headers=world["coord"]["headers"])
    assert letter["id"] in [row["id"] for row in mine.json()]

    # An administrative account has no church office to back: it does not
    # present letters at all, so nobody can end up validating their own.
    for label in ("coord", "assoc_admin"):
        refused = await client.post(
            LETTERS,
            json={
                "church_name": "Iglesia Central",
                "content_type": "application/pdf",
                "size_bytes": 10,
            },
            headers=world[label]["headers"],
        )
        assert refused.status_code == 403, (label, refused.text)

    assert (await _authorize(client, world, letter["id"])).status_code == 200


async def test_rejecting_and_revoking_take_the_verification_away(client, world, r2, factory, mails):
    person = await factory.user("revoked", "INSTRUCTOR", world["club"]["id"])
    await _join(person, world["club"], "INSTRUCTOR")
    await _set(person, verification_status="VERIFIED", child_protection_completed=True)

    letter = await _present(client, person, r2)
    assert (await _authorize(client, world, letter["id"])).status_code == 200
    assert await _verified_until(person) is not None

    revoked = await client.post(
        f"{LETTERS}/{letter['id']}/review",
        json={"action": "REVOKE", "note": "La iglesia retiró el respaldo"},
        headers=world["assoc_admin"]["headers"],
    )
    assert revoked.status_code == 200, revoked.text
    assert await _verified_until(person) is None
    assert [m for m in mails if m["kind"] == "decision"][-1]["status"] == "REVOKED"

    # ...and a rejection of the next one leaves nothing behind either.
    again = await _present(client, person, r2)
    rejected = await client.post(
        f"{LETTERS}/{again['id']}/review",
        json={"action": "REJECT", "note": "La carta no lleva firma"},
        headers=world["coord"]["headers"],
    )
    assert rejected.status_code == 200, rejected.text
    assert await _verified_until(person) is None


async def test_an_expired_letter_stops_verifying_the_same_day(world, factory):
    from app.models import User
    from app.rbac import is_verified_leader

    async with SessionLocal() as db:
        person = await db.get(User, uuid.UUID(world["instructor"]["id"]))
        person.child_protection_completed = True
        person.leader_verified_until = date.today()
        assert is_verified_leader(person) is True
        person.leader_verified_until = date.today() - timedelta(days=1)
        assert is_verified_leader(person) is False
        person.leader_verified_until = date.today() + timedelta(days=10)
        person.child_protection_completed = False
        assert is_verified_leader(person) is False  # the course is half of it
        await db.rollback()


# ----------------------------------------------------------------------------
# The renewal window (D5)
# ----------------------------------------------------------------------------
async def test_a_renewal_starts_sixty_days_before_and_keeps_the_verification(
    client, world, r2, factory
):
    person = await factory.user("renewer", "INSTRUCTOR", world["club"]["id"])
    await _join(person, world["club"], "INSTRUCTOR")
    await _set(person, verification_status="VERIFIED", child_protection_completed=True)
    letter = await _present(client, person, r2)
    assert (
        await _authorize(
            client, world, letter["id"], valid_until=(date.today() + timedelta(days=90)).isoformat()
        )
    ).status_code == 200

    too_early = await client.post(
        LETTERS,
        json={"church_name": "Iglesia Central", "content_type": "application/pdf", "size_bytes": 10},
        headers=person["headers"],
    )
    assert too_early.status_code == 409, too_early.text
    assert "renovarla" in too_early.json()["detail"]

    # Inside the window the renewal is allowed...
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET valid_until = :day WHERE id = :id"),
            {"day": date.today() + timedelta(days=30), "id": uuid.UUID(letter["id"])},
        )
        await db.commit()
    renewal = await _present(client, person, r2)
    assert renewal["status"] == "SUBMITTED"

    # ...the previous row is closed, and the verification already earned is NOT
    # lost while the new letter is being reviewed: the date lives on the account.
    previous = await fetch_one(
        "SELECT status, decision_note FROM church_letters WHERE id = :id", id=letter["id"]
    )
    assert previous["status"] == "REVOKED"
    assert "renovación" in previous["decision_note"]
    assert await _verified_until(person) == date.today() + timedelta(days=90)


# ----------------------------------------------------------------------------
# The gate: what an unverified leader may and may not do
# ----------------------------------------------------------------------------
@needs_portfolio
async def test_an_unverified_instructor_rules_on_adults_but_never_on_minors(
    client, world, factory, enforced
):
    from app.models import HonorEnrollment, User
    from app.rbac import can_review

    honor_id = await fetch_one(
        "SELECT id FROM honors WHERE status = 'PUBLISHED' LIMIT 1"
    )
    if honor_id is None:
        pytest.skip("no published honor in this database")

    enrollments = {}
    async with SessionLocal() as db:
        for label in ("minor", "adult_member"):
            enrollment_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO honor_enrollments (id, user_id, honor_id, mode, club_id, status)"
                    " VALUES (:id, :user, :honor, 'CLUB', :club, 'IN_PROGRESS')"
                ),
                {
                    "id": enrollment_id,
                    "user": uuid.UUID(world[label]["id"]),
                    "honor": honor_id["id"],
                    "club": uuid.UUID(world["club"]["id"]),
                },
            )
            enrollments[label] = enrollment_id
        await db.commit()

    async with SessionLocal() as db:
        instructor = await db.get(User, uuid.UUID(world["instructor"]["id"]))
        minor_enrollment = await db.get(HonorEnrollment, enrollments["minor"])
        adult_enrollment = await db.get(HonorEnrollment, enrollments["adult_member"])
        assert await can_review(db, instructor, adult_enrollment) is True
        assert await can_review(db, instructor, minor_enrollment) is False

        # With a letter in force the same instructor rules on the minor too.
        instructor.leader_verified_until = date.today() + timedelta(days=30)
        assert await can_review(db, instructor, minor_enrollment) is True
        await db.rollback()


async def test_an_unverified_instructor_reads_nothing_of_a_minor(client, world, enforced):
    denied = await client.get(
        f"{USERS}/{world['minor']['id']}", headers=world["instructor"]["headers"]
    )
    assert denied.status_code == 403, denied.text
    # An adult of the same club is still visible.
    assert (
        await client.get(
            f"{USERS}/{world['adult_member']['id']}", headers=world["instructor"]["headers"]
        )
    ).status_code == 200


async def test_the_roster_hides_minors_from_unverified_staff(client, world, enforced):
    roster = await client.get(
        f"{CLUBS}/{world['club']['id']}/members", headers=world["instructor"]["headers"]
    )
    assert roster.status_code == 200, roster.text
    ids = [row["user_id"] for row in roster.json()]
    assert world["minor"]["id"] not in ids
    assert world["adult_member"]["id"] in ids

    # Whoever manages the club keeps seeing everybody: that is how a club is run
    # and how an unverified instructor gets noticed at all.
    managed = await client.get(
        f"{CLUBS}/{world['club']['id']}/members", headers=world["assoc_admin"]["headers"]
    )
    assert world["minor"]["id"] in [row["user_id"] for row in managed.json()]


async def test_an_unverified_adult_cannot_take_charge_of_a_unit(client, world, enforced):
    await _set(world["counselor"], leader_verified_until=None)
    unit = await client.post(
        f"{CLUBS}/{world['club']['id']}/units",
        json={"name": "Unidad Verificada"},
        headers=world["assoc_admin"]["headers"],
    )
    assert unit.status_code == 201, unit.text
    denied = await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit.json()['id']}/counselor",
        json={"membership_id": world["counselor"]["membership_id"]},
        headers=world["assoc_admin"]["headers"],
    )
    assert denied.status_code == 409, denied.text
    assert "verificar" in denied.json()["detail"].lower()

    await _set(world["counselor"], leader_verified_until=date.today() + timedelta(days=10))
    ok = await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit.json()['id']}/counselor",
        json={"membership_id": world["counselor"]["membership_id"]},
        headers=world["assoc_admin"]["headers"],
    )
    assert ok.status_code == 200, ok.text
    await _set(world["counselor"], leader_verified_until=None)


# ----------------------------------------------------------------------------
# The director's 60-day grace (D4)
# ----------------------------------------------------------------------------
async def test_the_director_grace_covers_day_fifty_nine_and_not_day_sixty_one(
    world, monkeypatch, factory
):
    from app.models import User
    from app.rbac import director_in_grace, may_handle_minors

    monkeypatch.setattr(
        settings, "LEADER_VERIFICATION_ENFORCED_FROM", date.today() - timedelta(days=400)
    )
    async with SessionLocal() as db:
        director = await db.get(User, uuid.UUID(world["director"]["id"]))
        director.leader_verified_until = None
        director.club_approval_at = None
        for days, expected in ((59, True), (61, False)):
            director.club_approval_at = None
            director.created_at = None
            director.club_approval_at = (
                date.today() - timedelta(days=days)
            )
            assert director_in_grace(director) is expected, days
            assert may_handle_minors(director) is expected, days

        # A director whose club was never approved has no grace at all.
        director.club_approval = "PENDING"
        director.club_approval_at = date.today()
        assert director_in_grace(director) is False
        await db.rollback()


async def test_the_grace_is_counted_from_the_day_the_switch_was_turned_on(world, monkeypatch):
    """A club approved two years ago must not be born already out of grace the
    day enforcement starts: the window opens with the switch (D4)."""
    from app.models import User
    from app.rbac import director_in_grace

    monkeypatch.setattr(settings, "LEADER_VERIFICATION_ENFORCED_FROM", date.today())
    async with SessionLocal() as db:
        director = await db.get(User, uuid.UUID(world["director"]["id"]))
        director.club_approval = "APPROVED"
        director.club_approval_at = date.today() - timedelta(days=900)
        assert director_in_grace(director) is True
        await db.rollback()


# ----------------------------------------------------------------------------
# The letter is worth nothing outside the club it was validated for (rule 7)
# ----------------------------------------------------------------------------
async def test_leaving_the_club_leaves_the_verification_behind(client, world, factory):
    person = await factory.user("leaver", "INSTRUCTOR", world["club"]["id"])
    await _join(person, world["club"], "INSTRUCTOR")
    await _set(
        person,
        verification_status="VERIFIED",
        child_protection_completed=True,
        leader_verified_until=date.today() + timedelta(days=100),
    )
    left = await client.delete(f"{MEMBERSHIPS}/me", headers=person["headers"])
    assert left.status_code == 200, left.text
    assert await _verified_until(person) is None


async def test_moving_to_another_club_leaves_the_verification_behind(client, world, factory):
    other_club = await factory.org("club-destino", "club", world["church"])
    person = await factory.user("mover", "INSTRUCTOR", world["club"]["id"])
    await _join(person, world["club"], "INSTRUCTOR")
    await _set(
        person,
        verification_status="VERIFIED",
        child_protection_completed=True,
        leader_verified_until=date.today() + timedelta(days=100),
    )
    director = await factory.user("destino-director", "CLUB_DIRECTOR", other_club["id"])
    await _join(director, other_club, "CLUB_DIRECTOR")

    invitation = await client.post(
        f"{CLUBS}/{other_club['id']}/invitations",
        json={"role": "INSTRUCTOR", "email": person["email"]},
        headers=director["headers"],
    )
    assert invitation.status_code == 201, invitation.text
    moved = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": invitation.json()["token"], "confirm_transfer": True},
        headers=person["headers"],
    )
    assert moved.status_code == 200, moved.text
    assert await _verified_until(person) is None


# ----------------------------------------------------------------------------
# Audit
# ----------------------------------------------------------------------------
async def test_every_decision_leaves_an_audit_row(client, world, r2, factory):
    person = await factory.user("audited", "INSTRUCTOR", world["club"]["id"])
    await _join(person, world["club"], "INSTRUCTOR")
    await _set(person, verification_status="VERIFIED", child_protection_completed=True)
    letter = await _present(client, person, r2)
    assert (await _authorize(client, world, letter["id"])).status_code == 200

    rows = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'CHURCH_LETTER' AND entity_id = :id"
        " ORDER BY created_at",
        id=letter["id"],
    )
    assert [row["action"] for row in rows] == [
        "LETTER_SUBMIT",
        "LETTER_VALIDATE",
        "LETTER_AUTHORIZE",
    ]
