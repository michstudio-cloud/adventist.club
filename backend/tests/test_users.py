"""Users: profile, subtree scoping, whitelist PATCH, soft delete, guardianships."""

import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal

from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("users")

USERS = "/api/v1/users"


@pytest_asyncio.fixture(scope="module")
async def tree(factory):
    """
    division
      ├─ union_a ── assoc_a : admin_a (ADMIN_UNION @ union_a), student_a, instructor_a
      └─ union_b            : admin_b (ADMIN_UNION @ union_b), student_b
    """
    division = await factory.org("div", "division")
    union_a = await factory.org("uni-a", "union", division)
    union_b = await factory.org("uni-b", "union", division)
    assoc_a = await factory.org("assoc-a", "association", union_a)
    return {
        "division": division,
        "union_a": union_a,
        "union_b": union_b,
        "assoc_a": assoc_a,
        "master": await factory.user("master", role="MASTER_GC"),
        "admin_a": await factory.user("admin-a", role="ADMIN_UNION", organization_id=union_a["id"]),
        "admin_b": await factory.user("admin-b", role="ADMIN_UNION", organization_id=union_b["id"]),
        "student_a": await factory.user("student-a", organization_id=assoc_a["id"]),
        "instructor_a": await factory.user(
            "instructor-a", role="INSTRUCTOR", organization_id=assoc_a["id"]
        ),
        "student_b": await factory.user("student-b", organization_id=union_b["id"]),
        "loner": await factory.user("loner"),
    }


def _ids(response):
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()}


async def test_me(client, tree):
    assert (await client.get(f"{USERS}/me")).status_code == 401
    me = await client.get(f"{USERS}/me", headers=tree["student_a"]["headers"])
    assert me.status_code == 200
    body = me.json()
    assert body["id"] == tree["student_a"]["id"]
    assert body["organization_id"] == body["org_node_id"] == tree["assoc_a"]["id"]
    assert "password_hash" not in body and "mfa_secret" not in body


async def test_list_is_scoped_to_own_subtree(client, tree, factory):
    ours = {tree[k]["id"] for k in tree if "id" in tree[k] and "path" not in tree[k]}

    admin_a = _ids(
        await client.get(USERS, headers=tree["admin_a"]["headers"], params={"limit": 500})
    )
    assert {tree["admin_a"]["id"], tree["student_a"]["id"], tree["instructor_a"]["id"]} <= admin_a
    assert tree["student_b"]["id"] not in admin_a
    assert tree["admin_b"]["id"] not in admin_a
    assert tree["master"]["id"] not in admin_a

    # Members never get the directory: e-mails and birth dates (of minors too) are for staff only.
    student = _ids(await client.get(USERS, headers=tree["student_a"]["headers"]))
    assert student & ours == {tree["student_a"]["id"]}
    asking = await client.get(
        USERS, headers=tree["student_a"]["headers"], params={"organization_id": tree["assoc_a"]["id"]}
    )
    assert _ids(asking) & ours == {tree["student_a"]["id"]}
    # An instructor who hangs off a field (a virtual instructor) is not club staff: only themself.
    instructor = _ids(await client.get(USERS, headers=tree["instructor_a"]["headers"]))
    assert instructor & ours == {tree["instructor_a"]["id"]}
    # Club staff see their own club's members, and nothing beyond the club.
    club = await factory.org("club-a", "club", tree["assoc_a"])
    club_instructor = await factory.user("club-instr", role="INSTRUCTOR", organization_id=club["id"])
    club_member = await factory.user("club-member", organization_id=club["id"])
    async with SessionLocal() as db:   # staff must be in good standing: a verified account
        await db.execute(text("UPDATE users SET verification_status = 'VERIFIED' WHERE id = :id"),
                         {"id": uuid.UUID(club_instructor["id"])})
        await db.commit()
    seen = _ids(await client.get(USERS, headers=club_instructor["headers"]))
    assert seen & (ours | {club_member["id"], club_instructor["id"]}) == {club_member["id"], club_instructor["id"]}
    wider = await client.get(USERS, headers=club_instructor["headers"], params={"organization_id": tree["assoc_a"]["id"]})
    assert wider.status_code == 403

    # Asking for someone else's subtree is refused, a narrower one is fine.
    foreign = await client.get(
        USERS, headers=tree["admin_a"]["headers"], params={"organization_id": tree["union_b"]["id"]}
    )
    assert foreign.status_code == 403
    upward = await client.get(
        USERS, headers=tree["admin_a"]["headers"], params={"org_node_id": tree["division"]["id"]}
    )
    assert upward.status_code == 403
    narrower = _ids(
        await client.get(
            USERS,
            headers=tree["admin_a"]["headers"],
            params={"organization_id": tree["assoc_a"]["id"], "role": "STUDENT"},
        )
    )
    assert narrower & ours == {tree["student_a"]["id"]}

    # Without an organization you only see yourself.
    assert _ids(await client.get(USERS, headers=tree["loner"]["headers"])) == {tree["loner"]["id"]}

    # MASTER_GC is global.
    everyone = _ids(
        await client.get(USERS, headers=tree["master"]["headers"], params={"limit": 500})
    )
    assert ours <= everyone
    division = _ids(
        await client.get(
            USERS,
            headers=tree["master"]["headers"],
            params={"organization_id": tree["division"]["id"], "limit": 500},
        )
    )
    assert division & ours == ours - {tree["master"]["id"], tree["loner"]["id"]}


async def test_get_user_visibility(client, tree):
    target = f"{USERS}/{tree['student_a']['id']}"
    assert (await client.get(target, headers=tree["student_a"]["headers"])).status_code == 200
    assert (await client.get(target, headers=tree["admin_a"]["headers"])).status_code == 200
    assert (await client.get(target, headers=tree["instructor_a"]["headers"])).status_code == 200
    assert (await client.get(target, headers=tree["master"]["headers"])).status_code == 200
    assert (await client.get(target, headers=tree["admin_b"]["headers"])).status_code == 403
    assert (await client.get(target, headers=tree["student_b"]["headers"])).status_code == 403
    missing = await client.get(f"{USERS}/{uuid.uuid4()}", headers=tree["master"]["headers"])
    assert missing.status_code == 404


async def test_self_cannot_escalate(client, tree, factory):
    student = tree["student_a"]
    url = f"{USERS}/{student['id']}"
    for body in (
        {"role": "MASTER_GC"},
        {"role": "INSTRUCTOR"},
        {"status": "SUSPENDED"},
        {"organization_id": tree["union_b"]["id"]},
        {"org_node_id": tree["union_b"]["id"]},
        {"verification_status": "VERIFIED"},
        {"name": "ok", "role": "ADMIN_UNION"},
    ):
        response = await client.patch(url, json=body, headers=student["headers"])
        assert response.status_code == 403, body
    # Fields outside the whitelist are rejected outright.
    for body in ({"password_hash": "x"}, {"mfa_enabled": False}, {"email": "a@b.co"}):
        response = await client.patch(url, json=body, headers=student["headers"])
        assert response.status_code == 422, body

    row = await fetch_one(
        "SELECT role, status, organization_id::text AS org, verification_status, name"
        " FROM users WHERE id = :id",
        id=uuid.UUID(student["id"]),
    )
    assert row["role"] == "STUDENT" and row["status"] == "ACTIVE"
    assert row["org"] == tree["assoc_a"]["id"] and row["verification_status"] == "PENDING"
    assert row["name"] == factory.name("student-a")

    # An admin cannot promote itself either.
    admin = tree["admin_a"]
    own_role = await client.patch(
        f"{USERS}/{admin['id']}", json={"role": "MASTER_GC"}, headers=admin["headers"]
    )
    assert own_role.status_code == 403


async def test_self_update_and_clear_avatar(client, tree, factory):
    student = tree["student_a"]
    url = f"{USERS}/{student['id']}"
    updated = await client.patch(
        url,
        json={"name": factory.name("student-a"), "avatar_url": "https://media.example/a.png"},
        headers=student["headers"],
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["avatar_url"] == "https://media.example/a.png"

    # Omitting avatar_url leaves it alone ...
    untouched = await client.patch(
        url, json={"name": factory.name("student-a")}, headers=student["headers"]
    )
    assert untouched.json()["avatar_url"] == "https://media.example/a.png"
    # ... an explicit null clears it.
    cleared = await client.patch(url, json={"avatar_url": None}, headers=student["headers"])
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["avatar_url"] is None
    row = await fetch_one(
        "SELECT avatar_url FROM users WHERE id = :id", id=uuid.UUID(student["id"])
    )
    assert row["avatar_url"] is None

    assert (
        await client.patch(url, json={"name": None}, headers=student["headers"])
    ).status_code == 400
    assert (await client.patch(url, json={}, headers=student["headers"])).status_code == 400
    # Someone else's profile is off limits.
    other = await client.patch(
        f"{USERS}/{tree['instructor_a']['id']}", json={"name": "x"}, headers=student["headers"]
    )
    assert other.status_code == 403


async def test_admin_updates_within_scope_only(client, tree, factory):
    admin = tree["admin_a"]
    pupil = await factory.user("pupil", organization_id=tree["assoc_a"]["id"])
    url = f"{USERS}/{pupil['id']}"

    moved = await client.patch(
        url, json={"organization_id": tree["union_a"]["id"]}, headers=admin["headers"]
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["organization_id"] == tree["union_a"]["id"]

    out_of_scope = await client.patch(
        url, json={"organization_id": tree["union_b"]["id"]}, headers=admin["headers"]
    )
    assert out_of_scope.status_code == 403
    peer_role = await client.patch(url, json={"role": "ADMIN_UNION"}, headers=admin["headers"])
    assert peer_role.status_code == 403
    above = await client.patch(url, json={"role": "MASTER_GC"}, headers=admin["headers"])
    assert above.status_code == 403
    needs_cert = await client.patch(url, json={"role": "INSTRUCTOR"}, headers=admin["headers"])
    assert needs_cert.status_code == 400

    cert = await client.post(
        f"{url}/child-protection-cert", params={"completed": "true"}, headers=admin["headers"]
    )
    assert cert.status_code == 200, cert.text
    assert cert.json()["child_protection_cert"]["completed"] is True
    assert cert.json()["child_protection_cert"]["completed_at"] is not None
    promoted = await client.patch(url, json={"role": "INSTRUCTOR"}, headers=admin["headers"])
    assert promoted.status_code == 200 and promoted.json()["role"] == "INSTRUCTOR"

    # The other union's admin has no say over this user.
    foreign = tree["admin_b"]
    assert (
        await client.patch(url, json={"status": "SUSPENDED"}, headers=foreign["headers"])
    ).status_code == 403
    assert (
        await client.post(f"{url}/verify", params={"approved": "true"}, headers=foreign["headers"])
    ).status_code == 403
    assert (await client.delete(url, headers=foreign["headers"])).status_code == 403

    verified = await client.post(
        f"{url}/verify", params={"approved": "true"}, headers=admin["headers"]
    )
    assert verified.status_code == 200 and verified.json()["verification_status"] == "VERIFIED"
    by_student = await client.post(
        f"{url}/verify", params={"approved": "true"}, headers=tree["student_a"]["headers"]
    )
    assert by_student.status_code == 403

    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'USER' AND entity_id = :id"
        " ORDER BY created_at",
        id=pupil["id"],
    )
    assert [row["action"] for row in audit] == ["UPDATE", "UPDATE", "UPDATE", "APPROVE"]


async def test_soft_delete(client, tree, factory):
    victim = await factory.user("victim", organization_id=tree["assoc_a"]["id"])
    url = f"{USERS}/{victim['id']}"
    assert (await client.delete(url, headers=victim["headers"])).status_code == 403
    assert (await client.delete(url, headers=tree["student_a"]["headers"])).status_code == 403
    assert (await client.delete(url, headers=tree["admin_a"]["headers"])).status_code == 204

    row = await fetch_one("SELECT status FROM users WHERE id = :id", id=uuid.UUID(victim["id"]))
    assert row["status"] == "INACTIVE"  # the row is kept
    # Its still-valid token is dead from now on.
    assert (await client.get(f"{USERS}/me", headers=victim["headers"])).status_code == 403


async def test_guardianship_rules(client, tree, factory):
    org = tree["assoc_a"]["id"]
    guardian = await factory.user("guardian", role="PARENT_GUARDIAN", organization_id=org)
    other_guardian = await factory.user("guardian2", role="PARENT_GUARDIAN", organization_id=org)
    child = await factory.user("child", organization_id=org, is_minor=True)
    adult = await factory.user("adult", organization_id=org)
    url = f"{USERS}/guardianships"
    # SEC-01: only an adult whose e-mail is verified may declare a guardianship.
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE users SET verification_status = 'VERIFIED' WHERE id = ANY(:ids)"),
            {"ids": [uuid.UUID(u["id"]) for u in (guardian, other_guardian, tree["student_a"])]},
        )
        await db.commit()

    # D9 (bloque E): any ADULT may be a guardian, not only PARENT_GUARDIAN —
    # the director whose own child is a member needs no second account. A minor
    # never can.
    a_minor = await factory.user("minor-guardian", is_minor=True, organization_id=org)
    refused = await client.post(url, json={"child_id": child["id"]}, headers=a_minor["headers"])
    assert refused.status_code == 403
    another_adult = await client.post(
        url, json={"child_id": child["id"]}, headers=tree["student_a"]["headers"]
    )
    assert another_adult.status_code == 201, another_adult.text
    not_minor = await client.post(url, json={"child_id": adult["id"]}, headers=guardian["headers"])
    assert not_minor.status_code == 400
    itself = await client.post(url, json={"child_id": guardian["id"]}, headers=guardian["headers"])
    assert itself.status_code == 400
    missing = await client.post(
        url, json={"child_id": str(uuid.uuid4())}, headers=guardian["headers"]
    )
    assert missing.status_code == 404
    bad_relationship = await client.post(
        url, json={"child_id": child["id"], "relationship": "COUSIN"}, headers=guardian["headers"]
    )
    assert bad_relationship.status_code == 422

    created = await client.post(
        url,
        json={"child_id": child["id"], "relationship": "LEGAL_GUARDIAN"},
        headers=guardian["headers"],
    )
    assert created.status_code == 201, created.text
    link = created.json()
    assert link["consent_status"] == "PENDING" and link["consent_granted_at"] is None
    assert link["relationship"] == "LEGAL_GUARDIAN"

    duplicate = await client.post(url, json={"child_id": child["id"]}, headers=guardian["headers"])
    assert duplicate.status_code == 409

    mine = await client.get(f"{url}/my-children", headers=guardian["headers"])
    assert [g["id"] for g in mine.json()] == [link["id"]]
    assert (await client.get(f"{url}/my-children", headers=child["headers"])).status_code == 403
    theirs = await client.get(f"{url}/my-guardians", headers=child["headers"])
    assert guardian["id"] in [g["guardian_id"] for g in theirs.json()]
    assert (await client.get(f"{url}/my-guardians", headers=adult["headers"])).status_code == 403

    # Only the guardian of the link decides.
    for outsider in (other_guardian, child, tree["master"]):
        refused = await client.post(f"{url}/{link['id']}/approve", headers=outsider["headers"])
        assert refused.status_code == 403
    # SEC-01: not even they approve it — that was self-consent. The approval comes from
    # the consent link e-mailed to the guardian (memberships.decide_consent).
    approved = await client.post(f"{url}/{link['id']}/approve", headers=guardian["headers"])
    assert approved.status_code == 403
    rejected = await client.post(f"{url}/{link['id']}/reject", headers=guardian["headers"])
    assert rejected.status_code == 200
    assert rejected.json()["consent_status"] == "REJECTED"
    assert rejected.json()["consent_granted_at"] is None
    gone = await client.post(f"{url}/{uuid.uuid4()}/approve", headers=guardian["headers"])
    assert gone.status_code == 404


async def test_admin_self_edit_stays_within_own_limits(client, tree, factory):
    admin = await factory.user(
        "self-admin", role="ADMIN_UNION", organization_id=tree["union_a"]["id"]
    )
    url = f"{USERS}/{admin['id']}"

    # Never upwards, never sideways, never out of the own subtree, never self-verified.
    for body in (
        {"role": "MASTER_GC"},
        {"role": "ADMIN_DIVISION"},
        {"organization_id": tree["union_b"]["id"]},
        {"organization_id": tree["division"]["id"]},
        {"organization_id": None},
        {"verification_status": "VERIFIED"},
    ):
        response = await client.patch(url, json=body, headers=admin["headers"])
        assert response.status_code == 403, body

    # Inside the own subtree, and downwards, is allowed.
    moved = await client.patch(
        url, json={"organization_id": tree["assoc_a"]["id"]}, headers=admin["headers"]
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["organization_id"] == tree["assoc_a"]["id"]
    demoted = await client.patch(url, json={"role": "COORDINATOR_ZONE"}, headers=admin["headers"])
    assert demoted.status_code == 200 and demoted.json()["role"] == "COORDINATOR_ZONE"

    # MASTER_GC is global, also for itself.
    master = await factory.user("self-master", role="MASTER_GC")
    placed = await client.patch(
        f"{USERS}/{master['id']}",
        json={"org_node_id": tree["division"]["id"]},
        headers=master["headers"],
    )
    assert placed.status_code == 200, placed.text
    assert placed.json()["org_node_id"] == tree["division"]["id"]
