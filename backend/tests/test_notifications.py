"""«Avisos» — the in-app inbox (017_notifications.sql) and the e-mails it rides with.

* A requirement SENT to review tells the club's reviewers: in the inbox always (one unread
  notice per club whose count is the queue), by e-mail at most once per club every 12 h.
* The member hears about their own progress: a COMPLETE (inbox only, E9 keeps its rule of
  «a plain COMPLETE sends no e-mail»), READY, hours approved, positive points — e-mail capped
  at one per entity every 12 h and switched off by `notify_progress`; the inbox never is.
* `/notifications` only ever shows one's own, with `X-Total-Count`, and a notice of
  somebody else is a 404.

E-mail is always checked on the call, never on the network.
"""
import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from app.services import email as email_service
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db
from tests.test_portfolio import _enroll, _honor, _review, _submit

NOTIFICATIONS = "/api/v1/notifications"
LOGS = "/api/v1/activity/logs"


def _tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(
                    await db.scalar(
                        text(
                            "SELECT to_regclass('public.notifications') IS NOT NULL"
                            " AND to_regclass('public.activity_logs') IS NOT NULL"
                            " AND to_regclass('public.xp_awards') IS NOT NULL"
                        )
                    )
                )
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(not _tables_exist(), reason="apply migrations 013, 014 and 017 to the test database"),
]
factory = module_factory("avisos")


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("club-b", "club", association)
    people = {
        "member": await factory.user("member", "STUDENT", club["id"]),
        "mate": await factory.user("mate", "STUDENT", club["id"]),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        # Not verified: an instructor without a confirmed account reviews nothing.
        "unverified": await factory.user("unverified", "INSTRUCTOR", club["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", other_club["id"]),
    }
    await _exec("UPDATE users SET verification_status = 'VERIFIED' WHERE id = :id",
                id=uuid.UUID(people["instructor"]["id"]))
    return {**people, "club": club, "other_club": other_club}


@pytest_asyncio.fixture
async def fresh(factory, world):
    """A member with an empty inbox and no e-mail history."""
    return await factory.user(f"fresh-{uuid.uuid4().hex[:6]}", "STUDENT", world["club"]["id"])


@pytest.fixture
def mails(monkeypatch):
    sent = []

    def capture(kind):
        async def send(to, *args, **kwargs):
            sent.append({"kind": kind, "to": to, "args": args, "kwargs": kwargs})
            return True

        return send

    monkeypatch.setattr(email_service, "send_pending_reviews_email", capture("PENDING_REVIEWS"))
    monkeypatch.setattr(email_service, "send_progress_email", capture("PROGRESS"))
    monkeypatch.setattr(email_service, "send_hours_approved_email", capture("HOURS_APPROVED"))
    monkeypatch.setattr(email_service, "send_hours_rejected_email", capture("HOURS_REJECTED"))
    monkeypatch.setattr(email_service, "send_xp_awarded_email", capture("XP_AWARDED"))
    return sent


async def _inbox(client, user, **params) -> list[dict]:
    response = await client.get(NOTIFICATIONS, params=params, headers=user["headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def _club_review_mails_logged(club) -> int:
    rows = await fetch_all(
        "SELECT id FROM notification_log WHERE kind = 'PENDING_REVIEWS' AND entity_id = :id",
        id=club["id"],
    )
    return len(rows)


async def _age_log(kind: str, entity_id: str, hours: int) -> None:
    await _exec(
        "UPDATE notification_log SET sent_at = now() - make_interval(hours => :h)"
        " WHERE kind = :k AND entity_id = :id",
        h=hours, k=kind, id=entity_id,
    )


# ----------------------------------------------------------------------------
# Requirements sent to review -> the club's reviewers
# ----------------------------------------------------------------------------
async def test_a_submission_reaches_the_reviewers_once_per_club_every_twelve_hours(
    client, world, factory, mails
):
    honor = await _honor(factory, "queue", theoretical=(True, True))
    first = await _enroll(client, world["member"], honor)
    second = await _enroll(client, world["mate"], honor)

    assert (await _submit(client, world["member"], first["id"], 1)).status_code == 200
    reviews = [mail for mail in mails if mail["kind"] == "PENDING_REVIEWS"]
    # The director and the verified instructor; never the unverified one, never the member.
    assert {mail["to"] for mail in reviews} == {world["director"]["email"], world["instructor"]["email"]}
    director_mail = next(m for m in reviews if m["to"] == world["director"]["email"])
    # (reviewer name, club, count, link): a count and a link, never whose work it is.
    assert director_mail["args"][2] == 1
    assert director_mail["args"][3].endswith("/admin/portafolio")
    assert world["member"]["email"] not in str(director_mail)

    notice = (await _inbox(client, world["director"], unread="true"))[0]
    assert notice["kind"] == "PENDING_REVIEWS" and notice["count"] == 1
    assert notice["data"]["pending"] == 1 and notice["link"] == "/panel"
    assert await _inbox(client, world["unverified"]) == []

    # A second submission inside the window: no second e-mail, the SAME notice now says 2.
    mails.clear()
    assert (await _submit(client, world["mate"], second["id"], 1)).status_code == 200
    assert [m for m in mails if m["kind"] == "PENDING_REVIEWS"] == []
    unread = await _inbox(client, world["director"], unread="true")
    assert len(unread) == 1 and unread[0]["id"] == notice["id"] and unread[0]["count"] == 2
    assert await _club_review_mails_logged(world["club"]) == 2   # one per reviewer, once

    # Thirteen hours later the window is open again.
    await _age_log("PENDING_REVIEWS", world["club"]["id"], 13)
    assert (await _submit(client, world["member"], first["id"], 2)).status_code == 200
    again = [m for m in mails if m["kind"] == "PENDING_REVIEWS"]
    assert len(again) == 2 and all(m["args"][2] == 3 for m in again)

    # The director of another club hears nothing about this one.
    assert await _inbox(client, world["director_b"]) == []


async def test_a_draft_or_a_withdrawal_tells_nobody(client, world, factory, mails):
    honor = await _honor(factory, "draft", theoretical=(True,))
    enrollment = await _enroll(client, world["mate"], honor)
    before = await _inbox(client, world["instructor"])
    draft = await client.put(
        f"/api/v1/portfolio/enrollments/{enrollment['id']}/requirements/1",
        json={"member_note": "Borrador"}, headers=world["mate"]["headers"],
    )
    assert draft.status_code == 200, draft.text
    assert [m for m in mails if m["kind"] == "PENDING_REVIEWS"] == []
    assert await _inbox(client, world["instructor"]) == before


# ----------------------------------------------------------------------------
# The member's own progress
# ----------------------------------------------------------------------------
async def test_approved_requirements_count_in_the_inbox_and_send_no_email(
    client, world, factory, fresh, mails
):
    honor = await _honor(factory, "approved", theoretical=(True, True, True))
    enrollment = await _enroll(client, fresh, honor)
    for position in (1, 2):
        assert (await _submit(client, fresh, enrollment["id"], position)).status_code == 200
        done = await _review(client, world["instructor"], enrollment["id"], position)
        assert done.status_code == 200, done.text

    inbox = await _inbox(client, fresh)
    assert [row["kind"] for row in inbox] == ["REQUIREMENT_APPROVED"]
    assert inbox[0]["count"] == 2 and inbox[0]["data"]["award"] == honor["name"]
    assert inbox[0]["link"] == f"/portfolio/enrollments/{enrollment['id']}"
    assert inbox[0]["title"] == "2 requisitos aprobados"
    # E9: a plain COMPLETE sends no e-mail.
    assert [m for m in mails if m["kind"] == "PROGRESS"] == []

    # The last one makes it READY: E9's notice, in the inbox AND by e-mail.
    assert (await _submit(client, fresh, enrollment["id"], 3)).status_code == 200
    assert (await _review(client, world["instructor"], enrollment["id"], 3)).status_code == 200
    kinds = [row["kind"] for row in await _inbox(client, fresh)]
    assert kinds == ["PROGRESS_READY", "REQUIREMENT_APPROVED"]
    progress = [m for m in mails if m["kind"] == "PROGRESS"]
    assert [m["args"][1] for m in progress] == ["PROGRESS_READY"] and progress[0]["to"] == fresh["email"]


async def test_the_inbox_ignores_the_email_preference(client, world, factory, mails):
    quiet = await factory.user(f"quiet-{uuid.uuid4().hex[:6]}", "STUDENT", world["club"]["id"])
    await _exec("UPDATE users SET notify_progress = false WHERE id = :id", id=uuid.UUID(quiet["id"]))
    honor = await _honor(factory, "quiet", theoretical=(True,))
    enrollment = await _enroll(client, quiet, honor)
    assert (await _submit(client, quiet, enrollment["id"], 1)).status_code == 200
    verdict = await _review(client, world["instructor"], enrollment["id"], 1,
                            verdict="INCOMPLETE", note="Falta la fecha")
    assert verdict.status_code == 200, verdict.text

    assert [m for m in mails if m["kind"] == "PROGRESS"] == []
    inbox = await _inbox(client, quiet)
    assert [row["kind"] for row in inbox] == ["PROGRESS_INCOMPLETE"]
    assert inbox[0]["data"]["note"] == "Falta la fecha"


async def test_approved_hours_reach_the_member_capped_by_email_summed_in_the_inbox(
    client, world, fresh, mails
):
    today = date.today().isoformat()
    ids = []
    for quantity in (2, 1.5):
        created = await client.post(LOGS, json={
            "category": "SERVICE", "performed_on": today, "quantity": quantity,
            "description": "Limpieza del parque"}, headers=fresh["headers"])
        assert created.status_code == 201, created.text
        ids.append(created.json()[0]["id"])
    assert await _inbox(client, fresh) == []          # sending hours tells the member nothing

    for log_id in ids:
        decided = await client.post(f"{LOGS}/{log_id}/decision", json={"status": "APPROVED"},
                                    headers=world["director"]["headers"])
        assert decided.status_code == 200, decided.text

    hours_mails = [m for m in mails if m["kind"] == "HOURS_APPROVED"]
    assert len(hours_mails) == 1 and hours_mails[0]["to"] == fresh["email"]
    assert hours_mails[0]["args"][1] == 2.0                 # (name, service, attendance, link)
    inbox = await _inbox(client, fresh)
    assert [row["kind"] for row in inbox] == ["HOURS_APPROVED"]
    assert inbox[0]["count"] == 2 and inbox[0]["data"]["service"] == 3.5
    assert inbox[0]["link"] == "/portfolio/horas"
    # Where and what the member did never travels in a notice.
    assert "parque" not in str(inbox[0]) and "parque" not in str(hours_mails[0])

    # A rejection is not an approval: the approved notice keeps its count (the rejection
    # has its own notice, see the next test).
    rejected_log = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": today, "quantity": 1, "description": "Otra"},
        headers=fresh["headers"])
    await client.post(f"{LOGS}/{rejected_log.json()[0]['id']}/decision",
                      json={"status": "REJECTED", "note": "Sin constancia"},
                      headers=world["director"]["headers"])
    approved = [row for row in await _inbox(client, fresh) if row["kind"] == "HOURS_APPROVED"]
    assert len(approved) == 1 and approved[0]["count"] == 2 and approved[0]["data"]["service"] == 3.5


async def test_rejected_hours_tell_the_member_with_the_reason_capped_by_email(
    client, world, fresh, mails
):
    today = date.today().isoformat()

    async def send(category, quantity, description):
        created = await client.post(LOGS, json={
            "category": category, "performed_on": today, "quantity": quantity,
            "description": description}, headers=fresh["headers"])
        assert created.status_code == 201, created.text
        return created.json()[0]["id"]

    async def decide(log_id, status_, note=None):
        body = {"status": status_} if note is None else {"status": status_, "note": note}
        decided = await client.post(f"{LOGS}/{log_id}/decision", json=body,
                                    headers=world["director"]["headers"])
        assert decided.status_code == 200, decided.text

    first = await send("SERVICE", 2.5, "Visita al asilo del barrio")
    await decide(first, "REJECTED", "Falta la constancia de la iglesia")
    inbox = await _inbox(client, fresh)
    assert [row["kind"] for row in inbox] == ["HOURS_REJECTED"]
    notice = inbox[0]
    assert notice["data"] == {"service": 2.5, "attendance": 0.0, "note": "Falta la constancia de la iglesia"}
    assert notice["link"] == "/portfolio/horas" and notice["title"] == "Horas no aprobadas"
    assert notice["body"] == "No se aprobaron 2,5 h de servicio. «Falta la constancia de la iglesia»"
    rejected = [m for m in mails if m["kind"] == "HOURS_REJECTED"]
    # (name, service, attendance, note, link) — to the member only, never where it was.
    assert len(rejected) == 1 and rejected[0]["to"] == fresh["email"]
    assert rejected[0]["args"][1:4] == (2.5, 0.0, "Falta la constancia de la iglesia")
    assert rejected[0]["args"][4].endswith("/portfolio/horas")
    assert "asilo" not in str(notice) and "asilo" not in str(rejected[0])

    # Deciding «rejected» again on the same log says nothing new.
    await decide(first, "REJECTED", "Falta la constancia de la iglesia")
    assert len(await _inbox(client, fresh)) == 1

    # A second rejection inside the window: its own notice with its own reason, and no
    # second e-mail — the cap of HOURS_APPROVED, per member.
    second = await send("ATTENDANCE", 1, "Reunión del sábado")
    await decide(second, "REJECTED", "No estabas en la lista")
    inbox = await _inbox(client, fresh)
    assert [row["kind"] for row in inbox] == ["HOURS_REJECTED", "HOURS_REJECTED"]
    assert inbox[0]["data"] == {"service": 0.0, "attendance": 1.0, "note": "No estabas en la lista"}
    assert inbox[0]["body"] == "No se aprobaron 1 asistencia. «No estabas en la lista»"
    assert inbox[1]["data"]["note"] == "Falta la constancia de la iglesia"
    assert len([m for m in mails if m["kind"] == "HOURS_REJECTED"]) == 1

    # Thirteen hours later the e-mail may go again.
    await _age_log("HOURS_REJECTED", fresh["id"], 13)
    third = await send("SERVICE", 1, "Otra")
    await decide(third, "REJECTED", "Duplicado")
    again = [m for m in mails if m["kind"] == "HOURS_REJECTED"]
    assert len(again) == 2 and again[1]["args"][3] == "Duplicado"


async def test_rejected_hours_respect_the_email_preference(client, world, factory, mails):
    quiet = await factory.user(f"quiet-h-{uuid.uuid4().hex[:6]}", "STUDENT", world["club"]["id"])
    await _exec("UPDATE users SET notify_progress = false WHERE id = :id", id=uuid.UUID(quiet["id"]))
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "Algo"}, headers=quiet["headers"])
    log_id = created.json()[0]["id"]
    decided = await client.post(f"{LOGS}/{log_id}/decision",
                                json={"status": "REJECTED", "note": "Sin constancia"},
                                headers=world["director"]["headers"])
    assert decided.status_code == 200, decided.text
    assert [m for m in mails if m["kind"] == "HOURS_REJECTED"] == []
    assert [row["kind"] for row in await _inbox(client, quiet)] == ["HOURS_REJECTED"]


async def test_an_outing_recorded_by_the_director_notifies_each_member(client, world, factory, mails):
    a = await factory.user(f"out-a-{uuid.uuid4().hex[:6]}", "STUDENT", world["club"]["id"])
    b = await factory.user(f"out-b-{uuid.uuid4().hex[:6]}", "STUDENT", world["club"]["id"])
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 3,
        "description": "Salida del club", "user_ids": [a["id"], b["id"]]},
        headers=world["director"]["headers"])
    assert created.status_code == 201, created.text
    assert {m["to"] for m in mails if m["kind"] == "HOURS_APPROVED"} == {a["email"], b["email"]}
    for person in (a, b):
        assert [row["data"]["service"] for row in await _inbox(client, person)] == [3.0]


async def test_positive_points_notify_and_negative_ones_do_not(client, world, fresh, mails):
    membership_id = uuid.uuid4()
    await _exec(
        "INSERT INTO club_memberships (id, user_id, club_id, role, status, source, started_at)"
        " VALUES (:id, :u, :c, 'STUDENT', 'ACTIVE', 'ADMIN', now())",
        id=membership_id, u=uuid.UUID(fresh["id"]), c=uuid.UUID(world["club"]["id"]),
    )
    await _exec(
        "INSERT INTO club_memberships (id, user_id, club_id, role, status, source, started_at)"
        " SELECT gen_random_uuid(), :u, :c, 'CLUB_DIRECTOR', 'ACTIVE', 'ADMIN', now()"
        " WHERE NOT EXISTS (SELECT 1 FROM club_memberships WHERE user_id = :u AND status = 'ACTIVE')",
        u=uuid.UUID(world["director"]["id"]), c=uuid.UUID(world["club"]["id"]),
    )
    url = f"/api/v1/clubs/{world['club']['id']}/members/{membership_id}/xp"
    for points in (10, 5):
        given = await client.post(url, json={"category": "conducta", "points": points,
                                             "note": "Muy respetuoso"},
                                  headers=world["director"]["headers"])
        assert given.status_code == 201, given.text
    taken = await client.post(url, json={"category": "uniforme", "points": -5},
                              headers=world["director"]["headers"])
    assert taken.status_code == 201, taken.text

    xp_mails = [m for m in mails if m["kind"] == "XP_AWARDED"]
    assert len(xp_mails) == 1 and xp_mails[0]["args"][1] == 10     # capped: one in 12 h
    inbox = await _inbox(client, fresh)
    assert [row["kind"] for row in inbox] == ["XP_AWARDED"]
    assert inbox[0]["data"]["points"] == 15 and inbox[0]["count"] == 2
    # The note is a judgement about a person: it is not copied anywhere.
    assert "respetuoso" not in str(inbox[0]) and "respetuoso" not in str(xp_mails[0])


# ----------------------------------------------------------------------------
# The endpoints
# ----------------------------------------------------------------------------
async def test_the_inbox_endpoints(client, world, factory, fresh):
    # Three notices of three different kinds, written the way the services do.
    from app.services import notifications

    async with SessionLocal() as db:
        for kind, entity in (("HOURS_APPROVED", "a"), ("XP_AWARDED", "b"), ("REQUIREMENT_APPROVED", "c")):
            await notifications.stage_inbox(
                db, user_id=uuid.UUID(fresh["id"]), kind=kind, data={"points": 1, "service": 1},
                link="/profile", entity_type="USER", entity_id=entity,
            )
        # A link that is not a path of the app is dropped, never stored.
        await notifications.stage_inbox(
            db, user_id=uuid.UUID(fresh["id"]), kind="XP_AWARDED", data={"points": 2},
            link="https://evil.example/", entity_type="USER", entity_id="d",
        )
        await db.commit()

    count = await client.get(f"{NOTIFICATIONS}/unread-count", headers=fresh["headers"])
    assert count.status_code == 200 and count.json() == {"count": 4}

    page = await client.get(NOTIFICATIONS, params={"limit": 2}, headers=fresh["headers"])
    assert page.status_code == 200 and page.headers["x-total-count"] == "4"
    assert len(page.json()) == 2
    rest = await client.get(NOTIFICATIONS, params={"limit": 2, "offset": 2}, headers=fresh["headers"])
    ids = [row["id"] for row in page.json()] + [row["id"] for row in rest.json()]
    assert len(set(ids)) == 4
    assert all(row["link"] in ("/profile", None) for row in page.json() + rest.json())

    # Somebody else's notice is a 404, whatever the id.
    stranger = await client.post(f"{NOTIFICATIONS}/{ids[0]}/read", headers=world["mate"]["headers"])
    assert stranger.status_code == 404
    missing = await client.post(f"{NOTIFICATIONS}/{uuid.uuid4()}/read", headers=fresh["headers"])
    assert missing.status_code == 404

    read = await client.post(f"{NOTIFICATIONS}/{ids[0]}/read", headers=fresh["headers"])
    assert read.status_code == 200 and read.json()["read_at"] is not None
    first_read = read.json()["read_at"]
    again = await client.post(f"{NOTIFICATIONS}/{ids[0]}/read", headers=fresh["headers"])
    assert again.json()["read_at"] == first_read                 # idempotent
    unread = await client.get(NOTIFICATIONS, params={"unread": "true"}, headers=fresh["headers"])
    assert unread.headers["x-total-count"] == "3"

    everything = await client.post(f"{NOTIFICATIONS}/read-all", headers=fresh["headers"])
    assert everything.status_code == 200 and everything.json() == {"updated": 3}
    count = await client.get(f"{NOTIFICATIONS}/unread-count", headers=fresh["headers"])
    assert count.json() == {"count": 0}

    # Without a session there is no inbox.
    assert (await client.get(NOTIFICATIONS)).status_code == 401


async def test_a_read_notice_is_not_reused(client, world, fresh):
    """Once read, the next event of the same kind opens a NEW notice with count 1."""
    from app.services import notifications

    async def stage():
        async with SessionLocal() as db:
            await notifications.stage_inbox(
                db, user_id=uuid.UUID(fresh["id"]), kind="XP_AWARDED", data={"points": 5},
                add=("points",), entity_type="USER", entity_id=fresh["id"],
            )
            await db.commit()

    await stage()
    await stage()
    rows = await _inbox(client, fresh)
    assert len(rows) == 1 and rows[0]["count"] == 2 and rows[0]["data"]["points"] == 10
    await client.post(f"{NOTIFICATIONS}/read-all", headers=fresh["headers"])
    await stage()
    rows = await _inbox(client, fresh)
    assert [(row["count"], row["data"]["points"], row["read_at"] is None) for row in rows] == [
        (1, 5, True), (2, 10, False)]


async def test_the_notification_rows_go_with_the_account(factory):
    person = await factory.user(f"gone-{uuid.uuid4().hex[:6]}", "STUDENT")
    from app.services import notifications

    async with SessionLocal() as db:
        await notifications.stage_inbox(db, user_id=uuid.UUID(person["id"]), kind="XP_AWARDED",
                                        data={"points": 1})
        await db.commit()
    await _exec("DELETE FROM users WHERE id = :id", id=uuid.UUID(person["id"]))
    assert await fetch_one("SELECT id FROM notifications WHERE user_id = :id",
                           id=uuid.UUID(person["id"])) is None


# ----------------------------------------------------------------------------
# The church letter about to expire (migrations/notify_expiring_letters.py)
# ----------------------------------------------------------------------------
def _expiring_script():
    import importlib.util
    import pathlib
    import sys

    path = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "notify_expiring_letters.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("notify_expiring_letters", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_an_expiring_letter_leaves_the_notice_in_the_inbox_with_the_cap_of_its_email(
    client, world, factory
):
    from datetime import timedelta

    from tests.conftest import TEST_DATABASE_URL

    script = _expiring_script()
    leader = await factory.user(f"leader-{uuid.uuid4().hex[:6]}", "INSTRUCTOR", world["club"]["id"])
    letter_id = uuid.uuid4()
    until = date.today() + timedelta(days=12)
    await _exec(
        "INSERT INTO church_letters (id, user_id, organization_id, church_name, storage_key,"
        " content_type, size_bytes, status, valid_until) VALUES (:id, :u, :o, 'Iglesia', :k,"
        " 'application/pdf', 1024, 'AUTHORIZED', :until)",
        id=letter_id, u=uuid.UUID(leader["id"]), o=uuid.UUID(world["club"]["id"]),
        k=f"letters/{leader['id']}/{letter_id}.pdf", until=until,
    )
    await _exec("UPDATE users SET leader_verified_until = :until WHERE id = :id",
                until=until, id=uuid.UUID(leader["id"]))
    sent = []

    def send(email, name, valid_until, days_left):
        sent.append((email, valid_until, days_left))
        return True

    # A dry run writes nothing: no e-mail, no log, no notice.
    dry = script.run(TEST_DATABASE_URL, days=30, commit=False, send=send)
    assert leader["email"] in [person["email"] for person in dry["people"]]
    assert sent == [] and await _inbox(client, leader) == []

    script.run(TEST_DATABASE_URL, days=30, commit=True, send=send)
    assert [row for row in sent if row[0] == leader["email"]] == [(leader["email"], until, 12)]
    inbox = await _inbox(client, leader)
    assert [row["kind"] for row in inbox] == ["LEADER_LETTER_EXPIRING"]
    assert inbox[0]["data"] == {"valid_until": until.isoformat(), "days": 12}
    assert inbox[0]["link"] == "/panel"
    assert inbox[0]["title"] == "Tu carta de la iglesia vence pronto"
    assert until.strftime("%d/%m/%Y") in inbox[0]["body"]
    row = await fetch_one(
        "SELECT entity_type, entity_id FROM notifications WHERE user_id = :u", u=uuid.UUID(leader["id"]))
    assert row == {"entity_type": "CHURCH_LETTER", "entity_id": str(letter_id)}

    # The same cap as its e-mail: running it again inside the window adds nothing.
    sent.clear()
    script.run(TEST_DATABASE_URL, days=30, commit=True, send=send)
    assert [row for row in sent if row[0] == leader["email"]] == []
    assert len(await _inbox(client, leader)) == 1

    # Once the window is over the warning comes back — and, while still unread, it updates
    # the same notice instead of piling up another.
    await _exec(
        "UPDATE notification_log SET sent_at = now() - interval '31 days'"
        " WHERE kind = 'LEADER_LETTER_EXPIRING' AND entity_id = :id", id=str(letter_id))
    script.run(TEST_DATABASE_URL, days=30, commit=True, send=send)
    inbox = await _inbox(client, leader)
    assert len(inbox) == 1 and inbox[0]["count"] == 2

