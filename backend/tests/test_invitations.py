"""Bloque E, incremento E3 — Invitaciones al club y consentimiento del tutor.

Dos secretos (el token de la invitación y el del consentimiento) que sólo
viven como hash, un enlace multiuso que nunca activa a nadie por su cuenta, y
la regla que manda sobre todas: un menor no queda activo en un club sin que un
adulto lo autorice PARA ESE club.

Ningún test toca la red: los correos se capturan y se comprueba la llamada.
"""

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from app.security import sha256_hex
from app.services import email as email_service
from tests.conftest import DEFAULT_PASSWORD, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("invitations")

AUTH = "/api/v1/auth"
USERS = "/api/v1/users"
CLUBS = "/api/v1/clubs"
MEMBERSHIPS = "/api/v1/memberships"

MINOR_BIRTH_DATE = date(2014, 5, 4)
INVALID_INVITATION = "Invitación no válida o vencida"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mails(monkeypatch):
    """Every outgoing message of this block, captured in order."""
    sent = []

    async def invitation(to, club_name, role, link, inviter_name):
        sent.append({"kind": "invitation", "to": to, "club": club_name, "link": link})
        return True

    async def consent(to, child_name, club_name, link):
        sent.append({"kind": "consent", "to": to, "child": child_name, "link": link})
        return True

    monkeypatch.setattr(email_service, "send_club_invitation_email", invitation)
    monkeypatch.setattr(email_service, "send_consent_request_email", consent)
    return sent


async def _set(user: dict, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    async with SessionLocal() as db:
        await db.execute(
            text(f"UPDATE users SET {assignments} WHERE id = :id"),
            {**columns, "id": uuid.UUID(user["id"])},
        )
        await db.commit()


async def _join(user: dict, club: dict, role: str = "STUDENT") -> str:
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
    return str(membership_id)


async def _membership_of(user: dict) -> dict | None:
    return await fetch_one(
        "SELECT id::text AS id, club_id::text AS club, role, status, source, guardian_email,"
        " consent_at, end_reason FROM club_memberships WHERE user_id = :id"
        " ORDER BY created_at DESC LIMIT 1",
        id=user["id"],
    )


async def _verified(factory, label, role="STUDENT", **extra) -> dict:
    """An account with a confirmed e-mail: that is what joining a club needs."""
    user = await factory.user(label, role, **extra)
    await _set(user, verification_status="VERIFIED")
    return user


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("other-club", "club", association)
    director = await factory.user("director", "CLUB_DIRECTOR", club["id"])
    secretary = await factory.user("secretary", "CLUB_SECRETARY", club["id"])
    await _join(director, club, "CLUB_DIRECTOR")
    await _join(secretary, club, "CLUB_SECRETARY")
    other_director = await factory.user("other-director", "CLUB_DIRECTOR", other_club["id"])
    await _join(other_director, other_club, "CLUB_DIRECTOR")
    return {
        "association": association,
        "club": club,
        "other_club": other_club,
        "director": director,
        "secretary": secretary,
        "other_director": other_director,
    }


async def _invite(client, world, headers=None, **payload) -> dict:
    body = {"role": "STUDENT", **payload}
    response = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json=body,
        headers=headers or world["director"]["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


# ----------------------------------------------------------------------------
# Crear invitaciones
# ----------------------------------------------------------------------------
async def test_single_use_invitation_returns_its_token_once(client, world):
    created = await _invite(client, world)
    assert created["token"]
    assert created["url"].endswith(f"/join?t={created['token']}")
    assert created["whatsapp_url"].startswith("https://wa.me/?text=")
    assert created["invitation"]["max_uses"] == 1
    assert created["invitation"]["state"] == "ACTIVE"
    assert created["invitation"]["requires_approval"] is False

    stored = await fetch_one(
        "SELECT token_hash FROM club_invitations WHERE id = :id", id=created["invitation"]["id"]
    )
    # Only the hash is kept, and the listing never shows a token again.
    assert stored["token_hash"] == sha256_hex(created["token"])
    listed = await client.get(
        f"{CLUBS}/{world['club']['id']}/invitations", headers=world["director"]["headers"]
    )
    assert listed.status_code == 200, listed.text
    assert all("token" not in row for row in listed.json())


async def test_multi_use_invitations_are_students_only_and_need_approval(client, world):
    refused = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "INSTRUCTOR", "max_uses": 30},
        headers=world["director"]["headers"],
    )
    assert refused.status_code == 422

    created = await _invite(client, world, max_uses=30)
    assert created["invitation"]["requires_approval"] is True
    assert created["invitation"]["max_uses"] == 30


async def test_staff_invitations_are_always_nominal(client, world, factory, mails):
    anonymous = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "INSTRUCTOR"},
        headers=world["director"]["headers"],
    )
    assert anonymous.status_code == 422
    assert "correo" in anonymous.json()["detail"].lower()

    address = factory.email("invited-instructor")
    named = await _invite(client, world, role="INSTRUCTOR", email=address)
    assert named["invitation"]["email"] == address
    assert [mail["kind"] for mail in mails] == ["invitation"]
    assert mails[0]["to"] == address


async def test_the_secretary_only_invites_students(client, world):
    refused = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "INSTRUCTOR", "email": "someone@example.com"},
        headers=world["secretary"]["headers"],
    )
    assert refused.status_code == 403
    allowed = await _invite(client, world, headers=world["secretary"]["headers"])
    assert allowed["invitation"]["role"] == "STUDENT"


async def test_a_club_cannot_pile_up_live_invitations(client, world, factory):
    club = await factory.org("busy-club", "club", world["association"])
    director = await factory.user("busy-director", "CLUB_DIRECTOR", club["id"])
    await _join(director, club, "CLUB_DIRECTOR")
    for _ in range(20):
        response = await client.post(
            f"{CLUBS}/{club['id']}/invitations",
            json={"role": "STUDENT"},
            headers=director["headers"],
        )
        assert response.status_code == 201, response.text
    too_many = await client.post(
        f"{CLUBS}/{club['id']}/invitations", json={"role": "STUDENT"}, headers=director["headers"]
    )
    assert too_many.status_code == 409


# ----------------------------------------------------------------------------
# Vista previa: un solo 404 para todo lo que no sirve
# ----------------------------------------------------------------------------
async def test_preview_is_public_and_never_names_a_person(client, world):
    created = await _invite(client, world)
    preview = await client.post(
        f"{MEMBERSHIPS}/invitations/preview", json={"token": created["token"]}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["club"]["id"] == world["club"]["id"]
    assert body["role"] == "STUDENT" and body["requires_approval"] is False
    assert "created_by" not in body and "email" not in body
    assert world["director"]["email"] not in preview.text


async def test_expired_revoked_exhausted_and_unknown_look_identical(client, world, factory):
    unknown = await client.post(
        f"{MEMBERSHIPS}/invitations/preview", json={"token": "x" * 40}
    )
    assert unknown.status_code == 404 and unknown.json()["detail"] == INVALID_INVITATION

    revoked = await _invite(client, world)
    dropped = await client.delete(
        f"{CLUBS}/{world['club']['id']}/invitations/{revoked['invitation']['id']}",
        headers=world["director"]["headers"],
    )
    assert dropped.status_code == 204
    # Revoking twice is the same answer: the link is dead either way.
    assert (
        await client.delete(
            f"{CLUBS}/{world['club']['id']}/invitations/{revoked['invitation']['id']}",
            headers=world["director"]["headers"],
        )
    ).status_code == 204

    expired = await _invite(client, world)
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE club_invitations SET expires_at = now() - interval '1 day' WHERE id = :id"),
            {"id": uuid.UUID(expired["invitation"]["id"])},
        )
        await db.commit()

    exhausted = await _invite(client, world)
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE club_invitations SET uses = max_uses WHERE id = :id"),
            {"id": uuid.UUID(exhausted["invitation"]["id"])},
        )
        await db.commit()

    for dead in (revoked, expired, exhausted):
        response = await client.post(
            f"{MEMBERSHIPS}/invitations/preview", json={"token": dead["token"]}
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == INVALID_INVITATION


# ----------------------------------------------------------------------------
# Aceptar
# ----------------------------------------------------------------------------
async def test_an_adult_with_a_single_use_link_is_active_at_once(client, world, factory):
    created = await _invite(client, world)
    person = await _verified(factory, "adult-accept")

    accepted = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"]},
        headers=person["headers"],
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ACTIVE"

    row = await _membership_of(person)
    assert (row["status"], row["source"], row["club"]) == ("ACTIVE", "INVITATION", world["club"]["id"])
    user_row = await fetch_one(
        "SELECT organization_id::text AS org FROM users WHERE id = :id", id=person["id"]
    )
    assert user_row["org"] == world["club"]["id"]

    # Single use: the link is spent.
    second = await _verified(factory, "adult-second")
    replay = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"]},
        headers=second["headers"],
    )
    assert replay.status_code == 404


async def test_a_multi_use_link_leaves_everyone_waiting_for_the_director(client, world, factory):
    created = await _invite(client, world, max_uses=5)
    first = await _verified(factory, "queue-one")
    second = await _verified(factory, "queue-two")

    for person in (first, second):
        accepted = await client.post(
            f"{MEMBERSHIPS}/invitations/accept",
            json={"token": created["token"]},
            headers=person["headers"],
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "PENDING_APPROVAL"
        row = await fetch_one(
            "SELECT organization_id FROM users WHERE id = :id", id=person["id"]
        )
        # Nobody is in the club yet: a leaked link must not put strangers in.
        assert row["organization_id"] is None

    used = await fetch_one(
        "SELECT uses FROM club_invitations WHERE id = :id", id=created["invitation"]["id"]
    )
    assert used["uses"] == 2


async def test_a_nominal_invitation_only_opens_for_its_own_address(client, world, factory):
    guest = await _verified(factory, "named-guest")
    created = await _invite(client, world, role="INSTRUCTOR", email=guest["email"])

    intruder = await _verified(factory, "intruder")
    refused = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"]},
        headers=intruder["headers"],
    )
    assert refused.status_code == 403
    assert (await _membership_of(intruder)) is None

    accepted = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"]},
        headers=guest["headers"],
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "INSTRUCTOR"


async def test_changing_club_needs_the_person_to_confirm(client, world, factory):
    person = await _verified(factory, "mover")
    await _join(person, world["other_club"])
    created = await _invite(client, world)

    refused = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"]},
        headers=person["headers"],
    )
    assert refused.status_code == 409
    assert "traslado" in refused.json()["detail"].lower()

    moved = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"], "confirm_transfer": True},
        headers=person["headers"],
    )
    assert moved.status_code == 200, moved.text
    rows = await fetch_all(
        "SELECT club_id::text AS club, status, end_reason FROM club_memberships"
        " WHERE user_id = :id ORDER BY created_at",
        id=person["id"],
    )
    assert [(r["club"], r["status"], r["end_reason"]) for r in rows] == [
        (world["other_club"]["id"], "ENDED", "TRANSFERRED"),
        (world["club"]["id"], "ACTIVE", None),
    ]


async def test_administrative_accounts_do_not_join_clubs(client, world, factory):
    created = await _invite(client, world)
    admin = await _verified(factory, "admin-join", "ADMIN_ASSOCIATION", organization_id=world["association"]["id"])
    refused = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"]},
        headers=admin["headers"],
    )
    assert refused.status_code == 409


# ----------------------------------------------------------------------------
# Menores: nadie entra sin el permiso de un adulto
# ----------------------------------------------------------------------------
async def test_a_minor_waits_for_a_guardian_before_entering(client, world, factory, mails):
    created = await _invite(client, world)
    minor = await _verified(factory, "minor", is_minor=True)
    await _set(minor, birth_date=MINOR_BIRTH_DATE)
    guardian = await _verified(factory, "guardian", "PARENT_GUARDIAN")

    no_guardian = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"]},
        headers=minor["headers"],
    )
    assert no_guardian.status_code == 422

    accepted = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"], "guardian_email": guardian["email"]},
        headers=minor["headers"],
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "PENDING_CONSENT"
    row = await _membership_of(minor)
    assert row["guardian_email"] == guardian["email"] and row["consent_at"] is None
    user_row = await fetch_one(
        "SELECT organization_id FROM users WHERE id = :id", id=minor["id"]
    )
    assert user_row["organization_id"] is None

    assert [mail["kind"] for mail in mails] == ["consent"]
    consent_link = mails[0]["link"]
    token = consent_link.rsplit("=", 1)[1]

    # The minor cannot authorize themselves, and neither can another minor.
    self_signed = await client.post(
        f"{MEMBERSHIPS}/consents/decide",
        json={"token": token, "decision": "APPROVE"},
        headers=minor["headers"],
    )
    assert self_signed.status_code == 403

    # Public: a guardian arriving from the e-mail/WhatsApp link has no account yet.
    preview = await client.post(f"{MEMBERSHIPS}/consents/preview", json={"token": token})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["child"]["name"]
    assert body["club"]["id"] == world["club"]["id"]
    assert body["club"]["director_name"]
    # The guardian is told exactly what the club will see.
    assert set(body["club_will_see"]) >= {"name", "age", "progress", "evidence"}
    # Same result if they happen to be signed in when they open the link.
    signed_in = await client.post(
        f"{MEMBERSHIPS}/consents/preview", json={"token": token}, headers=guardian["headers"]
    )
    assert signed_in.status_code == 200 and signed_in.json() == body

    approved = await client.post(
        f"{MEMBERSHIPS}/consents/decide",
        json={"token": token, "decision": "APPROVE", "relationship": "PARENT"},
        headers=guardian["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "ACTIVE"

    row = await _membership_of(minor)
    assert row["status"] == "ACTIVE" and row["consent_at"] is not None
    user_row = await fetch_one(
        "SELECT organization_id::text AS org FROM users WHERE id = :id", id=minor["id"]
    )
    assert user_row["org"] == world["club"]["id"]

    # The guardianship now exists and is approved.
    guardianship = await fetch_one(
        "SELECT consent_status FROM guardianships WHERE guardian_id = :g AND child_id = :c",
        g=guardian["id"],
        c=minor["id"],
    )
    assert guardianship["consent_status"] == "APPROVED"

    # The consent token is single use.
    replay = await client.post(
        f"{MEMBERSHIPS}/consents/decide",
        json={"token": token, "decision": "APPROVE"},
        headers=guardian["headers"],
    )
    assert replay.status_code in (404, 409)


async def test_a_guardian_who_says_no_cancels_the_membership(client, world, factory, mails):
    created = await _invite(client, world)
    minor = await _verified(factory, "minor-no", is_minor=True)
    guardian = await _verified(factory, "guardian-no", "PARENT_GUARDIAN")
    accepted = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"], "guardian_email": guardian["email"]},
        headers=minor["headers"],
    )
    assert accepted.status_code == 200, accepted.text
    token = mails[-1]["link"].rsplit("=", 1)[1]

    refused = await client.post(
        f"{MEMBERSHIPS}/consents/decide",
        json={"token": token, "decision": "REJECT"},
        headers=guardian["headers"],
    )
    assert refused.status_code == 200, refused.text
    row = await _membership_of(minor)
    assert (row["status"], row["end_reason"]) == ("CANCELLED", "DECLINED")


async def test_any_adult_can_be_a_guardian(client, world, factory, mails):
    """D9: the director whose own child is a member does not need a second account."""
    created = await _invite(client, world)
    minor = await _verified(factory, "minor-d9", is_minor=True)
    parent = await _verified(factory, "parent-instructor", "INSTRUCTOR")

    await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"], "guardian_email": parent["email"]},
        headers=minor["headers"],
    )
    token = mails[-1]["link"].rsplit("=", 1)[1]
    approved = await client.post(
        f"{MEMBERSHIPS}/consents/decide",
        json={"token": token, "decision": "APPROVE"},
        headers=parent["headers"],
    )
    assert approved.status_code == 200, approved.text

    mine = await client.get(f"{USERS}/guardianships/my-children", headers=parent["headers"])
    assert mine.status_code == 200, mine.text
    children = mine.json()
    assert [row["child_id"] for row in children] == [minor["id"]]
    assert children[0]["club"]["id"] == world["club"]["id"]
    assert children[0]["membership_status"] == "ACTIVE"
    # The guardian's panel needs this to offer "revoke": .../{membership_id}/consent/revoke.
    active = await _membership_of(minor)
    assert children[0]["membership_id"] == active["id"]


async def test_a_guardian_can_withdraw_the_authorization(client, world, factory, mails):
    created = await _invite(client, world)
    minor = await _verified(factory, "minor-revoke", is_minor=True)
    guardian = await _verified(factory, "guardian-revoke", "PARENT_GUARDIAN")
    await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"], "guardian_email": guardian["email"]},
        headers=minor["headers"],
    )
    token = mails[-1]["link"].rsplit("=", 1)[1]
    await client.post(
        f"{MEMBERSHIPS}/consents/decide",
        json={"token": token, "decision": "APPROVE"},
        headers=guardian["headers"],
    )
    membership = await _membership_of(minor)

    stranger = await _verified(factory, "stranger-revoke", "PARENT_GUARDIAN")
    refused = await client.post(
        f"{MEMBERSHIPS}/{membership['id']}/consent/revoke", headers=stranger["headers"]
    )
    assert refused.status_code == 403

    revoked = await client.post(
        f"{MEMBERSHIPS}/{membership['id']}/consent/revoke", headers=guardian["headers"]
    )
    assert revoked.status_code == 200, revoked.text
    row = await _membership_of(minor)
    assert (row["status"], row["end_reason"]) == ("ENDED", "CONSENT_REVOKED")
    user_row = await fetch_one("SELECT organization_id FROM users WHERE id = :id", id=minor["id"])
    assert user_row["organization_id"] is None


async def test_the_minor_can_correct_the_guardian_address(client, world, factory, mails):
    created = await _invite(client, world)
    minor = await _verified(factory, "minor-resend", is_minor=True)
    typo = await _verified(factory, "guardian-typo", "PARENT_GUARDIAN")
    right = await _verified(factory, "guardian-right", "PARENT_GUARDIAN")
    await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"], "guardian_email": typo["email"]},
        headers=minor["headers"],
    )
    membership = await _membership_of(minor)

    resent = await client.post(
        f"{MEMBERSHIPS}/{membership['id']}/consent/resend",
        json={"guardian_email": right["email"]},
        headers=minor["headers"],
    )
    assert resent.status_code == 200, resent.text
    assert mails[-1]["to"] == right["email"]
    row = await _membership_of(minor)
    assert row["guardian_email"] == right["email"]

    # The old link dies with the correction.
    old_token = mails[0]["link"].rsplit("=", 1)[1]
    stale = await client.post(
        f"{MEMBERSHIPS}/consents/decide",
        json={"token": old_token, "decision": "APPROVE"},
        headers=typo["headers"],
    )
    assert stale.status_code == 404

    # Three a day, and no more: nobody uses a minor's club to send mail.
    for _ in range(2):
        again = await client.post(
            f"{MEMBERSHIPS}/{membership['id']}/consent/resend", json={}, headers=minor["headers"]
        )
        assert again.status_code == 200, again.text
    capped = await client.post(
        f"{MEMBERSHIPS}/{membership['id']}/consent/resend", json={}, headers=minor["headers"]
    )
    assert capped.status_code == 429

    logged = await fetch_all(
        "SELECT kind, count(*) AS n FROM notification_log WHERE entity_id = :id GROUP BY kind",
        id=membership["id"],
    )
    counted = {row["kind"]: row["n"] for row in logged}
    assert counted == {"CONSENT_REQUEST": 1, "CONSENT_RESEND": 3}


# ----------------------------------------------------------------------------
# Registro con invitación: todo o nada
# ----------------------------------------------------------------------------
async def test_registering_with_an_invitation_is_one_transaction(client, world, factory):
    created = await _invite(client, world)
    payload = {
        "email": factory.email("reg-invited"),
        "password": DEFAULT_PASSWORD,
        "name": factory.name("reg-invited"),
        "invitation_token": created["token"],
    }
    response = await client.post(f"{AUTH}/register", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organization_id"] == world["club"]["id"]
    assert body["membership"]["status"] == "ACTIVE"

    row = await _membership_of({"id": body["id"]})
    assert (row["status"], row["source"]) == ("ACTIVE", "INVITATION")


async def test_a_bad_invitation_token_leaves_no_account_behind(client, factory):
    payload = {
        "email": factory.email("reg-bad"),
        "password": DEFAULT_PASSWORD,
        "name": factory.name("reg-bad"),
        "invitation_token": "not-a-real-token",
    }
    response = await client.post(f"{AUTH}/register", json=payload)
    assert response.status_code == 400, response.text
    created = await fetch_one(
        "SELECT count(*) AS n FROM users WHERE email = :email", email=factory.email("reg-bad")
    )
    assert created["n"] == 0


async def test_registering_a_minor_with_an_invitation_asks_the_guardian(
    client, world, factory, mails
):
    created = await _invite(client, world)
    guardian = await _verified(factory, "reg-guardian", "PARENT_GUARDIAN")
    payload = {
        "email": factory.email("reg-minor"),
        "password": DEFAULT_PASSWORD,
        "name": factory.name("reg-minor"),
        "invitation_token": created["token"],
        "birth_date": MINOR_BIRTH_DATE.isoformat(),
        "guardian_email": guardian["email"],
    }
    response = await client.post(f"{AUTH}/register", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["membership"]["status"] == "PENDING_CONSENT"
    assert response.json()["organization_id"] is None
    assert mails[-1]["kind"] == "consent" and mails[-1]["to"] == guardian["email"]


async def test_an_invitation_and_a_club_are_mutually_exclusive(client, world, factory):
    created = await _invite(client, world)
    response = await client.post(
        f"{AUTH}/register",
        json={
            "email": factory.email("reg-both"),
            "password": DEFAULT_PASSWORD,
            "name": factory.name("reg-both"),
            "role": "CLUB_DIRECTOR",
            "invitation_token": created["token"],
            "club": {
                "name": factory.name("x-club"),
                "association_id": world["association"]["id"], "ministry": "pathfinders",
                "church_name": factory.name("x-iglesia"),
            },
        },
    )
    assert response.status_code == 400


# ----------------------------------------------------------------------------
# Auditoría
# ----------------------------------------------------------------------------
async def test_every_step_is_audited(client, world, factory, mails):
    created = await _invite(client, world)
    person = await _verified(factory, "audited")
    await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created["token"]},
        headers=person["headers"],
    )
    actions = await fetch_all(
        "SELECT action FROM audit_log WHERE user_id = :id ORDER BY created_at", id=person["id"]
    )
    assert "MEMBERSHIP_INVITE_ACCEPT" in [row["action"] for row in actions]

    created_audit = await fetch_one(
        "SELECT count(*) AS n FROM audit_log WHERE action = 'INVITATION_CREATE'"
        " AND entity_id = :id",
        id=created["invitation"]["id"],
    )
    assert created_audit["n"] == 1
