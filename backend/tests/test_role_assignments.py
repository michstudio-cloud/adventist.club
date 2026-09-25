"""Bloque I §1: several scoped roles per person and organization invitations.

Spec: docs/superpowers/specs/2026-09-24-eventos.md §1.1 and §1.2.

What is proved here: `has_role` / `roles_in` inherit down the ltree `path` and never
up; the rank + scope matrix of who invites whom; the accept flow (e-mail must match,
minors refused, single use, expiry, resend kills the old token); `users.role` is
recomputed as the principal and a club account stays attached to its club unless an
administrative role outranks it; `POST /org-nodes/clubs/admin` invites a director
without an account; the 026 backfill leaves exactly one principal per user.
"""
import pathlib
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from app.models import Organization, User
from app.rbac import has_role, outranks_in, roles_in
from app.services import memberships as membership_service
from app.services import role_assignments
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("roles")

ORG = "/api/v1/org-nodes"
PREVIEW = "/api/v1/org-invitations/preview"
ACCEPT = "/api/v1/org-invitations/accept"
MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "026_role_assignments.sql"


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    division = await factory.org("div", "division")
    union = await factory.org("uni", "union", division)
    assoc = await factory.org("assoc", "association", union)
    zone = await factory.org("zona", "zone", assoc)
    church = await factory.org("iglesia", "church", zone)
    club = await factory.org("club", "club", church)
    other_assoc = await factory.org("otra-assoc", "association", union)
    other_zone = await factory.org("otra-zona", "zone", other_assoc)
    other_club = await factory.org("otro-club", "club", other_zone)
    return {
        "union": union,
        "assoc": assoc,
        "zone": zone,
        "church": church,
        "club": club,
        "other_assoc": other_assoc,
        "other_zone": other_zone,
        "other_club": other_club,
        "master": await factory.user("master", "MASTER_GC"),
        "union_admin": await factory.user("union-admin", "ADMIN_UNION", union["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", assoc["id"]),
        "coord": await factory.user("coord", "COORDINATOR_ZONE", zone["id"]),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
    }


async def _load(user_id: str) -> User:
    async with SessionLocal() as db:
        return await db.get(User, uuid.UUID(user_id))


async def _membership(user: dict, club: dict, role: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO club_memberships (id, user_id, club_id, role, status, source,"
                " started_at, created_at, updated_at) VALUES (gen_random_uuid(), :u, :c, :r,"
                " 'ACTIVE', 'ADMIN', now(), now(), now())"
            ),
            {"u": uuid.UUID(user["id"]), "c": uuid.UUID(club["id"]), "r": role},
        )
        await db.commit()


async def _invite(client, actor: dict, node: dict, role: str, email: str, **extra):
    return await client.post(
        f"{ORG}/{node['id']}/invitations",
        json={"role": role, "email": email, **extra},
        headers=actor["headers"],
    )


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in body.split(";") if statement.strip()]


async def _apply_migration() -> None:
    async with SessionLocal() as db:
        for statement in _statements(MIGRATION.read_text()):
            await db.execute(text(statement))
        await db.commit()


async def _principal(user_id: str) -> tuple[str, str | None]:
    row = await fetch_one(
        "SELECT role, organization_id::text AS org FROM users WHERE id = :id", id=uuid.UUID(user_id)
    )
    return row["role"], row["org"]


# ----------------------------------------------------------------------------
# rbac: has_role / roles_in / outranks_in
# ----------------------------------------------------------------------------
async def test_a_role_reaches_down_the_tree_and_never_up(world):
    admin = await _load(world["assoc_admin"]["id"])
    async with SessionLocal() as db:
        for node in ("assoc", "zone", "church", "club"):
            assert await has_role(db, admin, "ADMIN_ASSOCIATION", uuid.UUID(world[node]["id"])), node
        assert not await has_role(db, admin, "ADMIN_ASSOCIATION", uuid.UUID(world["union"]["id"]))
        assert not await has_role(db, admin, "ADMIN_ASSOCIATION", uuid.UUID(world["other_club"]["id"]))
        assert await has_role(db, admin, "ADMIN_ASSOCIATION")
        assert not await has_role(db, admin, "COORDINATOR_ZONE")
        assert await roles_in(db, admin, uuid.UUID(world["club"]["id"])) == ["ADMIN_ASSOCIATION"]
        assert await roles_in(db, admin, uuid.UUID(world["union"]["id"])) == []

        master = await _load(world["master"]["id"])
        assert await has_role(db, master, "MASTER_GC", uuid.UUID(world["other_club"]["id"]))
        # MASTER_GC is global but is still its own role; rank questions go to outranks_in.
        assert not await has_role(db, master, "ADMIN_ASSOCIATION", uuid.UUID(world["club"]["id"]))
        assert await outranks_in(db, master, "ADMIN_UNION", uuid.UUID(world["union"]["id"]))


async def test_a_second_role_counts_where_it_was_given(factory, world):
    member = await factory.user("alumno-instructor", "STUDENT", world["club"]["id"])
    await _membership(member, world["club"], "STUDENT")
    async with SessionLocal() as db:
        person = await db.get(User, uuid.UUID(member["id"]))
        zone = await db.get(Organization, uuid.UUID(world["zone"]["id"]))
        await role_assignments.grant(db, user=person, role="INSTRUCTOR", organization=zone, actor=None)
        await db.commit()

    person = await _load(member["id"])
    async with SessionLocal() as db:
        assert await has_role(db, person, "INSTRUCTOR", uuid.UUID(world["church"]["id"]))
        assert not await has_role(db, person, "INSTRUCTOR", uuid.UUID(world["other_zone"]["id"]))
        assert await has_role(db, person, "STUDENT", uuid.UUID(world["club"]["id"]))
        assert await roles_in(db, person, uuid.UUID(world["club"]["id"])) == ["INSTRUCTOR", "STUDENT"]
    # A club account stays attached to its club: INSTRUCTOR is not administrative.
    assert await _principal(member["id"]) == ("STUDENT", world["club"]["id"])


# ----------------------------------------------------------------------------
# Who invites whom
# ----------------------------------------------------------------------------
async def test_the_rank_and_scope_matrix(client, factory, world):
    email = factory.email("matriz")
    cases = [
        # (actor, node, role, expected)
        ("master", "assoc", "ADMIN_ASSOCIATION", 201),
        ("union_admin", "other_assoc", "ADMIN_ASSOCIATION", 201),
        ("assoc_admin", "assoc", "ADMIN_ASSOCIATION", 403),  # an equal: never
        ("assoc_admin", "zone", "COORDINATOR_ZONE", 201),
        ("assoc_admin", "other_zone", "COORDINATOR_ZONE", 403),  # outside the scope
        ("assoc_admin", "assoc", "INSTRUCTOR", 201),
        ("assoc_admin", "club", "CLUB_DIRECTOR", 201),
        ("coord", "club", "CLUB_DIRECTOR", 201),
        ("coord", "zone", "INSTRUCTOR", 201),
        ("coord", "zone", "COORDINATOR_ZONE", 403),
        ("coord", "assoc", "INSTRUCTOR", 403),  # above the coordinator's node
        ("coord", "other_club", "CLUB_DIRECTOR", 403),
        ("director", "club", "CLUB_DIRECTOR", 403),
        ("director", "zone", "INSTRUCTOR", 403),
    ]
    for number, (actor, node, role, expected) in enumerate(cases):
        # One address per case: a second live invitation for the same person is a 409.
        address = email if number == 0 else factory.email(f"matriz-{number}")
        response = await _invite(client, world[actor], world[node], role, address)
        assert response.status_code == expected, (actor, node, role, response.text)

    # The role must fit the node, and the address is required.
    wrong = await _invite(client, world["master"], world["club"], "COORDINATOR_ZONE", email)
    assert wrong.status_code == 422, wrong.text
    missing = await client.post(
        f"{ORG}/{world['club']['id']}/invitations",
        json={"role": "CLUB_DIRECTOR"},
        headers=world["master"]["headers"],
    )
    assert missing.status_code == 422
    # Two live invitations for the same person and role: the second is refused.
    again = await _invite(client, world["master"], world["assoc"], "ADMIN_ASSOCIATION", email)
    assert again.status_code == 409, again.text

    # The team screens follow the same rule.
    listed = await client.get(f"{ORG}/{world['zone']['id']}/invitations", headers=world["coord"]["headers"])
    assert listed.status_code == 200 and listed.json()
    assert all("token" not in row for row in listed.json())
    refused = await client.get(f"{ORG}/{world['club']['id']}/invitations", headers=world["director"]["headers"])
    assert refused.status_code == 403

    audit = await fetch_all(
        "SELECT metadata_json FROM audit_log WHERE action = 'ORG_INVITATION_CREATE'"
        " AND user_id = :u",
        u=uuid.UUID(world["coord"]["id"]),
    )
    assert audit and all(email not in str(row["metadata_json"]) for row in audit)


# ----------------------------------------------------------------------------
# Accepting
# ----------------------------------------------------------------------------
async def test_accepting_makes_the_role_principal_and_is_single_use(client, factory, world):
    email = factory.email("ruben")
    created = await _invite(client, world["master"], world["other_assoc"], "ADMIN_ASSOCIATION", email)
    assert created.status_code == 201, created.text
    body = created.json()
    token = body["token"]
    assert body["url"].endswith(f"/invitacion?t={token}")
    assert body["whatsapp_url"].startswith("https://wa.me/?text=")
    log = await fetch_one(
        "SELECT kind, email FROM notification_log WHERE entity_id = :id", id=body["invitation"]["id"]
    )
    assert log["kind"] == "ORG_INVITATION" and log["email"] == email

    preview = await client.post(PREVIEW, json={"token": token})
    assert preview.status_code == 200, preview.text
    assert preview.json()["role"] == "ADMIN_ASSOCIATION"
    assert preview.json()["organization"]["type"] == "ASSOCIATION"
    assert email not in preview.text and preview.json()["email_hint"].endswith("@example.com")

    # Somebody else signed in: 403, and the invitation is still usable.
    intruder = await factory.user("intruso", "STUDENT")
    wrong = await client.post(ACCEPT, json={"token": token}, headers=intruder["headers"])
    assert wrong.status_code == 403, wrong.text
    assert wrong.json()["detail"] == "Esta invitación es para otra cuenta de correo."

    person = await factory.user("ruben", "STUDENT")  # registers with the invited address
    ok = await client.post(ACCEPT, json={"token": token}, headers=person["headers"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["principal_role"] == "ADMIN_ASSOCIATION"
    assert await _principal(person["id"]) == ("ADMIN_ASSOCIATION", world["other_assoc"]["id"])

    me = await client.get("/api/v1/auth/me", headers=person["headers"])
    assert me.json()["role"] == "ADMIN_ASSOCIATION"
    assert me.json()["roles"][0] == {
        "role": "ADMIN_ASSOCIATION",
        "principal": True,
        "organization": {
            "id": world["other_assoc"]["id"],
            "name": factory.name("otra-assoc"),
            "type": "ASSOCIATION",
        },
    }
    # 024's selector context and 026's roles travel together in /me (merge of main).
    assert "ministries_available" in me.json() and "active_ministry" in me.json()
    mine = (await client.get("/api/v1/users/me", headers=person["headers"])).json()
    assert mine["roles"] and "clubs_available" in mine

    rows = await fetch_all(
        "SELECT role, source, is_primary, status, invitation_id::text AS inv FROM role_assignments"
        " WHERE user_id = :u AND status = 'ACTIVE'",
        u=uuid.UUID(person["id"]),
    )
    assert [dict(r) for r in rows if r["role"] == "ADMIN_ASSOCIATION"] == [{
        "role": "ADMIN_ASSOCIATION", "source": "INVITATION", "is_primary": True,
        "status": "ACTIVE", "inv": body["invitation"]["id"],
    }]
    assert sum(1 for r in rows if r["is_primary"]) == 1

    # Single use.
    twice = await client.post(ACCEPT, json={"token": token}, headers=person["headers"])
    assert twice.status_code == 404
    assert (await client.post(PREVIEW, json={"token": token})).status_code == 404
    accepted = await fetch_one(
        "SELECT action FROM audit_log WHERE entity_id = :id AND action = 'ORG_INVITATION_ACCEPT'",
        id=body["invitation"]["id"],
    )
    assert accepted is not None

    # The team of the association shows them; the new admin may now invite below.
    team = await client.get(
        f"{ORG}/{world['other_assoc']['id']}/role-assignments", headers=world["master"]["headers"]
    )
    assert [row["user"]["id"] for row in team.json()] == [person["id"]]
    below = await _invite(client, person, world["other_zone"], "COORDINATOR_ZONE", factory.email("z"))
    assert below.status_code == 201, below.text


async def test_minors_expiry_revoke_and_resend(client, factory, world):
    minor_email = factory.email("menor")
    minor = await factory.user("menor", "STUDENT", is_minor=True)
    token = (await _invite(client, world["assoc_admin"], world["zone"], "INSTRUCTOR", minor_email)).json()["token"]
    refused = await client.post(ACCEPT, json={"token": token}, headers=minor["headers"])
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Un menor de edad no puede aceptar un rol de adulto."

    # Expired: the single 404, like a token that never existed.
    late_email = factory.email("tarde")
    late = await factory.user("tarde", "STUDENT")
    created = (await _invite(client, world["assoc_admin"], world["zone"], "INSTRUCTOR", late_email)).json()
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE org_invitations SET expires_at = now() - interval '1 minute',"
                 " created_at = now() - interval '2 days' WHERE id = :id"),
            {"id": uuid.UUID(created["invitation"]["id"])},
        )
        await db.commit()
    expired = await client.post(ACCEPT, json={"token": created["token"]}, headers=late["headers"])
    assert expired.status_code == 404
    listed = await client.get(f"{ORG}/{world['zone']['id']}/invitations", headers=world["assoc_admin"]["headers"])
    states = {row["id"]: row["state"] for row in listed.json()}
    assert states[created["invitation"]["id"]] == "EXPIRED"

    # Resend: a new token; the old one is revoked.
    resent = await client.post(
        f"{ORG}/{world['zone']['id']}/invitations/{created['invitation']['id']}/resend",
        headers=world["assoc_admin"]["headers"],
    )
    assert resent.status_code == 201, resent.text
    new = resent.json()
    assert new["token"] != created["token"] and new["invitation"]["id"] != created["invitation"]["id"]
    old = await fetch_one(
        "SELECT revoked_at FROM org_invitations WHERE id = :id", id=uuid.UUID(created["invitation"]["id"])
    )
    assert old["revoked_at"] is not None
    assert (await client.post(ACCEPT, json={"token": created["token"]}, headers=late["headers"])).status_code == 404

    # A coordinator of another zone cannot touch it.
    other = await factory.user("coord-otra", "COORDINATOR_ZONE", world["other_zone"]["id"])
    foreign = await client.delete(
        f"{ORG}/{world['zone']['id']}/invitations/{new['invitation']['id']}", headers=other["headers"]
    )
    assert foreign.status_code == 403

    # Revoke: the new token dies too; revoking again answers the same.
    for _ in range(2):
        gone = await client.delete(
            f"{ORG}/{world['zone']['id']}/invitations/{new['invitation']['id']}",
            headers=world["assoc_admin"]["headers"],
        )
        assert gone.status_code == 204
    assert (await client.post(ACCEPT, json={"token": new["token"]}, headers=late["headers"])).status_code == 404


async def test_a_director_who_becomes_coordinator_keeps_the_club_and_gets_it_back(
    client, factory, world
):
    club = await factory.org("club-coord", "club", world["church"])
    director = await factory.user("dir-coord", "CLUB_DIRECTOR", club["id"])
    await _membership(director, club, "CLUB_DIRECTOR")

    token = (await _invite(client, world["assoc_admin"], world["zone"], "COORDINATOR_ZONE", director["email"])).json()["token"]
    ok = await client.post(ACCEPT, json={"token": token}, headers=director["headers"])
    assert ok.status_code == 200, ok.text
    # An administrative role outranks the club: the principal moves to the zone...
    assert await _principal(director["id"]) == ("COORDINATOR_ZONE", world["zone"]["id"])
    roles = [(r["role"], r["organization"]["id"]) for r in ok.json()["roles"]]
    assert roles == [("COORDINATOR_ZONE", world["zone"]["id"]), ("CLUB_DIRECTOR", club["id"])]
    # ...and the membership is untouched.
    membership = await fetch_one(
        "SELECT status, role FROM club_memberships WHERE user_id = :u", u=uuid.UUID(director["id"])
    )
    assert dict(membership) == {"status": "ACTIVE", "role": "CLUB_DIRECTOR"}
    person = await _load(director["id"])
    async with SessionLocal() as db:
        assert await has_role(db, person, "CLUB_DIRECTOR", uuid.UUID(club["id"]))

    # Retiring the coordination gives the club back as the principal.
    row = await fetch_one(
        "SELECT id::text AS id FROM role_assignments WHERE user_id = :u AND role = 'COORDINATOR_ZONE'"
        " AND status = 'ACTIVE'",
        u=uuid.UUID(director["id"]),
    )
    self_retire = await client.delete(
        f"{ORG}/{world['zone']['id']}/role-assignments/{row['id']}", headers=director["headers"]
    )
    assert self_retire.status_code == 403
    retired = await client.delete(
        f"{ORG}/{world['zone']['id']}/role-assignments/{row['id']}",
        headers=world["assoc_admin"]["headers"],
    )
    assert retired.status_code == 204, retired.text
    assert await _principal(director["id"]) == ("CLUB_DIRECTOR", club["id"])
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'ROLE_END'", id=row["id"]
    )
    assert audit["metadata_json"]["principal_after"] == "CLUB_DIRECTOR"

    # A club role is not retired from the team: it ends with the membership.
    club_row = await fetch_one(
        "SELECT id::text AS id FROM role_assignments WHERE user_id = :u AND role = 'CLUB_DIRECTOR'"
        " AND status = 'ACTIVE'",
        u=uuid.UUID(director["id"]),
    )
    refused = await client.delete(
        f"{ORG}/{club['id']}/role-assignments/{club_row['id']}", headers=world["assoc_admin"]["headers"]
    )
    assert refused.status_code == 409


async def test_leaving_the_club_leaves_an_administrative_principal_alone(client, factory, world):
    club = await factory.org("club-sale", "club", world["church"])
    person = await factory.user("sale", "CLUB_DIRECTOR", club["id"])
    await _membership(person, club, "CLUB_DIRECTOR")
    token = (await _invite(client, world["master"], world["assoc"], "ADMIN_ASSOCIATION", person["email"])).json()["token"]
    assert (await client.post(ACCEPT, json={"token": token}, headers=person["headers"])).status_code == 200
    assert await _principal(person["id"]) == ("ADMIN_ASSOCIATION", world["assoc"]["id"])

    async with SessionLocal() as db:
        member = await db.get(User, uuid.UUID(person["id"]))
        membership = await membership_service.active_membership(db, member.id)
        await membership_service.end(
            db, membership, end_reason=membership_service.REMOVED, actor=None, member=member
        )
        await db.commit()
    assert await _principal(person["id"]) == ("ADMIN_ASSOCIATION", world["assoc"]["id"])
    member = await _load(person["id"])
    async with SessionLocal() as db:
        assert not await has_role(db, member, "CLUB_DIRECTOR", uuid.UUID(club["id"]))
        assert await has_role(db, member, "ADMIN_ASSOCIATION", uuid.UUID(club["id"]))


async def test_accepting_club_director_appoints_the_director(client, factory, world):
    club = await factory.org("club-nuevo-dir", "club", world["church"])
    # A member of another club: accepting moves them (a transfer) and they lead this one.
    old_club = await factory.org("club-anterior", "club", world["church"])
    person = await factory.user("nuevo-dir", "STUDENT", old_club["id"])
    await _membership(person, old_club, "STUDENT")
    token = (await _invite(client, world["coord"], club, "CLUB_DIRECTOR", person["email"])).json()["token"]
    ok = await client.post(ACCEPT, json={"token": token}, headers=person["headers"])
    assert ok.status_code == 200, ok.text
    assert await _principal(person["id"]) == ("CLUB_DIRECTOR", club["id"])
    rows = await fetch_all(
        "SELECT club_id::text AS club, role, status FROM club_memberships WHERE user_id = :u"
        " ORDER BY created_at",
        u=uuid.UUID(person["id"]),
    )
    assert [(r["club"], r["role"], r["status"]) for r in rows] == [
        (old_club["id"], "STUDENT", "ENDED"),
        (club["id"], "CLUB_DIRECTOR", "ACTIVE"),
    ]
    user = await fetch_one("SELECT club_approval FROM users WHERE id = :id", id=uuid.UUID(person["id"]))
    assert user["club_approval"] == "APPROVED"
    active = await fetch_all(
        "SELECT role, organization_id::text AS org, is_primary FROM role_assignments"
        " WHERE user_id = :u AND status = 'ACTIVE'",
        u=uuid.UUID(person["id"]),
    )
    assert [dict(r) for r in active] == [{"role": "CLUB_DIRECTOR", "org": club["id"], "is_primary": True}]

    # Somebody who already leads a club cannot take a second one.
    busy_token = (await _invite(client, world["coord"], world["club"], "CLUB_DIRECTOR", person["email"])).json()["token"]
    busy = await client.post(ACCEPT, json={"token": busy_token}, headers=person["headers"])
    assert busy.status_code == 409 and busy.json()["detail"] == "director_has_club"


async def test_an_administrator_who_accepts_a_club_keeps_the_administration(client, factory, world):
    club = await factory.org("club-del-admin", "club", world["church"])
    admin = await factory.user("admin-dir", "COORDINATOR_ZONE", world["zone"]["id"])
    token = (await _invite(client, world["assoc_admin"], club, "CLUB_DIRECTOR", admin["email"])).json()["token"]
    ok = await client.post(ACCEPT, json={"token": token}, headers=admin["headers"])
    assert ok.status_code == 200, ok.text
    assert await _principal(admin["id"]) == ("COORDINATOR_ZONE", world["zone"]["id"])
    membership = await fetch_one(
        "SELECT club_id::text AS club, role, status FROM club_memberships WHERE user_id = :u",
        u=uuid.UUID(admin["id"]),
    )
    assert dict(membership) == {"club": club["id"], "role": "CLUB_DIRECTOR", "status": "ACTIVE"}
    primaries = await fetch_all(
        "SELECT role FROM role_assignments WHERE user_id = :u AND status = 'ACTIVE' AND is_primary",
        u=uuid.UUID(admin["id"]),
    )
    assert [r["role"] for r in primaries] == ["COORDINATOR_ZONE"]


# ----------------------------------------------------------------------------
# POST /org-nodes/clubs/admin with a director who has no account yet
# ----------------------------------------------------------------------------
async def test_admin_club_with_an_unknown_director_invites_them(client, factory, world):
    email = factory.email("dir-sin-cuenta")
    response = await client.post(
        f"{ORG}/clubs/admin",
        json={
            "association_id": world["assoc"]["id"],
            "ministry": "adventurers",
            "name": factory.name("club-invitado"),
            "director_email": email,
        },
        headers=world["assoc_admin"]["headers"],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE" and body["director"] is None
    invitation = body["director_invitation"]
    assert invitation["email"] == email and invitation["expires_at"]
    token = invitation["url"].split("t=", 1)[1]

    row = await fetch_one(
        "SELECT organization_id::text AS org, role FROM org_invitations WHERE id = :id",
        id=uuid.UUID(invitation["id"]),
    )
    assert dict(row) == {"org": body["id"], "role": "CLUB_DIRECTOR"}
    assert await fetch_one(
        "SELECT id FROM notification_log WHERE entity_id = :id", id=invitation["id"]
    ) is not None

    person = await factory.user("dir-sin-cuenta", "STUDENT")  # registers, then accepts
    ok = await client.post(ACCEPT, json={"token": token}, headers=person["headers"])
    assert ok.status_code == 200, ok.text
    assert await _principal(person["id"]) == ("CLUB_DIRECTOR", body["id"])
    roster = await client.get(f"/api/v1/clubs/{body['id']}/members", headers=person["headers"])
    assert roster.status_code == 200, roster.text


# ----------------------------------------------------------------------------
# 026 backfill
# ----------------------------------------------------------------------------
async def test_the_backfill_gives_every_account_one_principal_and_is_idempotent(factory, world):
    fresh = await factory.user("backfill", "INSTRUCTOR", world["club"]["id"])
    loose = await factory.user("backfill-suelto", "PARENT_GUARDIAN")
    for _ in range(2):
        await _apply_migration()
    for person, role, org in ((fresh, "INSTRUCTOR", world["club"]["id"]), (loose, "PARENT_GUARDIAN", None)):
        rows = await fetch_all(
            "SELECT role, organization_id::text AS org, status, is_primary, source"
            " FROM role_assignments WHERE user_id = :u",
            u=uuid.UUID(person["id"]),
        )
        assert [dict(r) for r in rows] == [
            {"role": role, "org": org, "status": "ACTIVE", "is_primary": True, "source": "BACKFILL"}
        ]
    orphans = await fetch_one(
        "SELECT count(*) AS n FROM users u WHERE NOT EXISTS"
        " (SELECT 1 FROM role_assignments ra WHERE ra.user_id = u.id)"
    )
    assert orphans["n"] == 0
    doubles = await fetch_one(
        "SELECT count(*) AS n FROM (SELECT user_id FROM role_assignments WHERE status = 'ACTIVE'"
        " AND is_primary GROUP BY user_id HAVING count(*) > 1) x"
    )
    assert doubles["n"] == 0


async def test_a_legacy_change_replaces_the_stale_principal(factory, world):
    """`PATCH /users/{id}` and the membership service still write the two columns;
    the next write here catches the mirror up instead of bringing the old role back."""
    person = await factory.user("legado", "COORDINATOR_ZONE", world["zone"]["id"])
    await _apply_migration()
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE users SET role = 'STUDENT', organization_id = NULL WHERE id = :id"),
            {"id": uuid.UUID(person["id"])},
        )
        await db.commit()
    member = await _load(person["id"])
    async with SessionLocal() as db:
        # Read-side: the demotion counts at once, the stale mirror is ignored.
        assert not await has_role(db, member, "COORDINATOR_ZONE", uuid.UUID(world["zone"]["id"]))
        member = await db.get(User, member.id)
        assoc = await db.get(Organization, uuid.UUID(world["assoc"]["id"]))
        await role_assignments.grant(db, user=member, role="INSTRUCTOR", organization=assoc, actor=None)
        await db.commit()
    assert await _principal(person["id"]) == ("INSTRUCTOR", world["assoc"]["id"])
    rows = await fetch_all(
        "SELECT role, status, end_reason FROM role_assignments WHERE user_id = :u ORDER BY granted_at",
        u=uuid.UUID(person["id"]),
    )
    assert ("COORDINATOR_ZONE", "ENDED", "REPLACED") in [(r["role"], r["status"], r["end_reason"]) for r in rows]
