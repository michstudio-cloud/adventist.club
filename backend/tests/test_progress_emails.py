"""Bloque E, incremento E9 — Correos de avance del portafolio.

Los tres que el bloque A dejó pendientes y ni uno más: requisito INCOMPLETO con
observación, inscripción LISTA para certificar y CERTIFICADO emitido. Un
`COMPLETE` suelto no envía nada, hay un tope de uno por inscripción cada 12 h, y
`users.notify_progress` los apaga sin tocar los de seguridad, invitación,
consentimiento ni decisión de membresía.

El envío se comprueba SIEMPRE sobre la llamada, nunca sobre la red.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import email as email_service
from tests.conftest import DB_AVAILABLE, fetch_all, module_factory, requires_db
from tests.test_portfolio import (  # the harness of block A, reused as is
    ENROLLMENTS,
    FakePrivateR2,
    _complete_all,
    _enroll,
    _honor,
    _review,
    _submit,
)

PORTFOLIO = "/api/v1/portfolio"
USERS = "/api/v1/users"


def _tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(
                    await db.scalar(
                        text(
                            "SELECT to_regclass('public.honor_enrollments') IS NOT NULL"
                            " AND to_regclass('public.notification_log') IS NOT NULL"
                        )
                    )
                )
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _tables_exist(),
        reason="apply migrations/007_portfolio.sql, 008c and 008g to the test database",
    ),
]
factory = module_factory("progressmail")


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    people = {
        "minor": await factory.user("minor", "STUDENT", club["id"], is_minor=True),
        "adult": await factory.user("adult", "STUDENT", club["id"]),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "guardian": await factory.user("guardian", "PARENT_GUARDIAN"),
        "quiet_guardian": await factory.user("quiet-guardian", "PARENT_GUARDIAN"),
    }
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE users SET verification_status = 'VERIFIED' WHERE id = :id"),
            {"id": uuid.UUID(people["instructor"]["id"])},
        )
        for label in ("guardian", "quiet_guardian"):
            await db.execute(
                text(
                    "INSERT INTO guardianships (guardian_id, child_id, consent_status)"
                    " VALUES (:g, :c, 'APPROVED')"
                ),
                {"g": uuid.UUID(people[label]["id"]), "c": uuid.UUID(people["minor"]["id"])},
            )
        await db.execute(
            text("UPDATE users SET notify_progress = false WHERE id = :id"),
            {"id": uuid.UUID(people["quiet_guardian"]["id"])},
        )
        await db.commit()
    return {**people, "association": association, "club": club}


@pytest.fixture
def mails(monkeypatch):
    sent = []

    async def progress(to, name, kind, honor_name, note, link):
        sent.append({"to": to, "kind": kind, "honor": honor_name, "note": note})
        return True

    monkeypatch.setattr(email_service, "send_progress_email", progress)
    return sent


@pytest.fixture
def issuer(factory, monkeypatch):
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", "PROTOTYPE")


@pytest.fixture
def r2(monkeypatch):
    from app.services import private_storage

    fake = FakePrivateR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET_NAME", "evidence-test")
    monkeypatch.setattr(private_storage, "get_client", lambda: fake)
    return fake


async def _logged(enrollment_id) -> list[str]:
    rows = await fetch_all(
        "SELECT kind FROM notification_log WHERE entity_id = :id ORDER BY sent_at",
        id=str(enrollment_id),
    )
    return [row["kind"] for row in rows]


async def _age_the_log(enrollment_id, hours: int) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE notification_log SET sent_at = now() - make_interval(hours => :h)"
                 " WHERE entity_id = :id"),
            {"h": hours, "id": str(enrollment_id)},
        )
        await db.commit()


# ----------------------------------------------------------------------------
# The three moments, and only those three
# ----------------------------------------------------------------------------
async def test_a_plain_complete_sends_nothing(client, world, factory, mails):
    honor = await _honor(factory, "silent", theoretical=(True, True))
    enrollment = await _enroll(client, world["adult"], honor)
    assert (await _submit(client, world["adult"], enrollment["id"], 1)).status_code == 200
    done = await _review(client, world["instructor"], enrollment["id"], 1, verdict="COMPLETE")
    assert done.status_code == 200, done.text

    # One requirement of two is done: the portfolio is simply moving along.
    assert mails == []
    assert await _logged(enrollment["id"]) == []


async def test_an_incomplete_verdict_carries_its_observation(client, world, factory, mails):
    honor = await _honor(factory, "observed", theoretical=(True,))
    enrollment = await _enroll(client, world["adult"], honor)
    assert (await _submit(client, world["adult"], enrollment["id"], 1)).status_code == 200
    note = "Falta la fecha de la salida"
    sent = await _review(
        client, world["instructor"], enrollment["id"], 1, verdict="INCOMPLETE", note=note
    )
    assert sent.status_code == 200, sent.text

    assert mails == [
        {
            "to": world["adult"]["email"],
            "kind": "PROGRESS_INCOMPLETE",
            "honor": honor["name"],
            "note": note,
        }
    ]
    assert await _logged(enrollment["id"]) == ["PROGRESS_INCOMPLETE"]


async def test_finishing_the_last_requirement_says_it_is_ready(client, world, factory, mails):
    honor = await _honor(factory, "ready", theoretical=(True,))
    enrollment = await _enroll(client, world["adult"], honor)
    await _complete_all(client, world["adult"], world["instructor"], enrollment)

    assert [mail["kind"] for mail in mails] == ["PROGRESS_READY"]
    assert mails[0]["to"] == world["adult"]["email"]
    # «Lista para certificar» is about the member's own work: no guardians here.
    assert await _logged(enrollment["id"]) == ["PROGRESS_READY"]


async def test_the_certificate_also_reaches_the_guardians(
    client, world, factory, mails, issuer, r2
):
    honor = await _honor(factory, "certified", theoretical=(True,))
    enrollment = await _enroll(client, world["minor"], honor)
    await _complete_all(client, world["minor"], world["instructor"], enrollment)
    mails.clear()

    issued = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/certificate",
        json={"issued_date": "2026-09-22"},
        headers=world["director"]["headers"],
    )
    assert issued.status_code == 201, issued.text

    addresses = {mail["to"] for mail in mails}
    assert world["minor"]["email"] in addresses
    assert world["guardian"]["email"] in addresses
    # A guardian who switched the progress e-mails off is not written to.
    assert world["quiet_guardian"]["email"] not in addresses
    assert all(mail["kind"] == "PROGRESS_CERTIFIED" for mail in mails)


# ----------------------------------------------------------------------------
# The cap and the preference
# ----------------------------------------------------------------------------
async def test_at_most_one_progress_notice_per_enrollment_every_twelve_hours(
    client, world, factory, mails
):
    honor = await _honor(factory, "capped", theoretical=(True, True))
    enrollment = await _enroll(client, world["adult"], honor)
    for position in (1, 2):
        assert (await _submit(client, world["adult"], enrollment["id"], position)).status_code == 200

    first = await _review(
        client, world["instructor"], enrollment["id"], 1, verdict="INCOMPLETE", note="Uno"
    )
    assert first.status_code == 200, first.text
    second = await _review(
        client, world["instructor"], enrollment["id"], 2, verdict="INCOMPLETE", note="Dos"
    )
    assert second.status_code == 200, second.text

    assert [mail["note"] for mail in mails] == ["Uno"]  # the second one is held back
    assert await _logged(enrollment["id"]) == ["PROGRESS_INCOMPLETE"]

    # Thirteen hours later the window is open again.
    await _age_the_log(enrollment["id"], 13)
    assert (
        await _submit(client, world["adult"], enrollment["id"], 2)
    ).status_code == 200
    third = await _review(
        client, world["instructor"], enrollment["id"], 2, verdict="INCOMPLETE", note="Tres"
    )
    assert third.status_code == 200, third.text
    assert [mail["note"] for mail in mails] == ["Uno", "Tres"]


async def test_a_certificate_is_never_held_back_by_the_cap(
    client, world, factory, mails, issuer, r2
):
    """It happens once per enrollment and it is the message that closes the
    story: it never falls into the 12-hour window of the other two."""
    honor = await _honor(factory, "capped-cert", theoretical=(True,))
    enrollment = await _enroll(client, world["adult"], honor)
    await _complete_all(client, world["adult"], world["instructor"], enrollment)
    assert [mail["kind"] for mail in mails] == ["PROGRESS_READY"]

    issued = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/certificate",
        json={"issued_date": "2026-09-22"},
        headers=world["director"]["headers"],
    )
    assert issued.status_code == 201, issued.text
    assert [mail["kind"] for mail in mails] == ["PROGRESS_READY", "PROGRESS_CERTIFIED"]


async def test_the_member_can_switch_the_progress_emails_off(client, world, factory, mails):
    quiet = await factory.user("quiet", "STUDENT", world["club"]["id"])
    turned_off = await client.patch(
        f"{USERS}/{quiet['id']}", json={"notify_progress": False}, headers=quiet["headers"]
    )
    assert turned_off.status_code == 200, turned_off.text

    honor = await _honor(factory, "muted", theoretical=(True,))
    enrollment = await _enroll(client, quiet, honor)
    assert (await _submit(client, quiet, enrollment["id"], 1)).status_code == 200
    assert (
        await _review(
            client, world["instructor"], enrollment["id"], 1, verdict="INCOMPLETE", note="Nada"
        )
    ).status_code == 200

    assert mails == []
    assert await _logged(enrollment["id"]) == []


async def test_a_mail_outage_never_breaks_a_verdict(client, world, factory, monkeypatch):
    """Resend refusing (or raising) must not undo a verdict: the notice is
    marked as not delivered and that is all."""

    async def explode(*args, **kwargs):
        raise RuntimeError("Resend is down")

    monkeypatch.setattr(email_service, "send_progress_email", explode)
    honor = await _honor(factory, "outage", theoretical=(True,))
    enrollment = await _enroll(client, world["adult"], honor)
    assert (await _submit(client, world["adult"], enrollment["id"], 1)).status_code == 200
    verdict = await _review(
        client, world["instructor"], enrollment["id"], 1, verdict="INCOMPLETE", note="Revisa"
    )
    assert verdict.status_code == 200, verdict.text

    rows = await fetch_all(
        "SELECT ok FROM notification_log WHERE entity_id = :id", id=str(enrollment["id"])
    )
    assert [row["ok"] for row in rows] == [False]
    # ...and the verdict itself is in the database.
    progress = await fetch_all(
        "SELECT status FROM requirement_progress WHERE enrollment_id = :id",
        id=enrollment["id"],
    )
    assert [row["status"] for row in progress] == ["INCOMPLETE"]


async def test_no_progress_email_ever_carries_another_persons_data(world):
    """The body is the honor, an observation the member already read and a link
    to their own portfolio. Nothing else (spec §7)."""
    html = email_service.progress_email_html(
        "Ana", "PROGRESS_INCOMPLETE", "Nudos y amarres", "Falta la foto", "https://x/portafolio/1"
    )
    assert "Nudos y amarres" in html and "Falta la foto" in html
    assert html.count("<img") == 1  # only the header logo, above the content
    for other in (world["minor"]["email"], world["guardian"]["email"]):
        assert other not in html
