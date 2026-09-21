"""Club sign-up: a director requests a club under an association, a coordinator
of that association approves or rejects it.

Runs against the imported world directory (NTAM = Asociación Norte de
Tamaulipas). Everything created here carries the factory prefix and is removed
by the module factory's cleanup.
"""

import uuid
from datetime import date, timedelta

import pytest

from app.services import email as email_service
from tests.conftest import DEFAULT_PASSWORD, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("clubsignup")

AUTH = "/api/v1/auth"
ORG = "/api/v1/org-nodes"
USERS = "/api/v1/users"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _payload(factory, label, **extra):
    return {
        "email": factory.email(label),
        "password": DEFAULT_PASSWORD,
        "name": factory.name(label),
        **extra,
    }


async def _find_ntam(client) -> dict:
    """The way the register form finds it: public search, no token."""
    response = await client.get(f"{ORG}/search", params={"q": "Tamaulipas", "type": "association"})
    assert response.status_code == 200, response.text
    matches = [row for row in response.json() if row["code"] == "NTAM"]
    if not matches:
        pytest.skip("world directory not imported in this database (NTAM missing)")
    return matches[0]


async def _ntam_node(ntam: dict) -> dict:
    row = await fetch_one(
        "SELECT id::text AS id, path::text AS path FROM organizations WHERE id = :id",
        id=uuid.UUID(ntam["id"]),
    )
    return {"id": row["id"], "path": row["path"]}


async def _register_director(client, factory, label, ntam, **club_extra) -> dict:
    club = {"name": factory.name(f"club-{label}"), "association_id": ntam["id"], **club_extra}
    # E6 / decision D3: the director always declares a church (and never a zone).
    if "church" not in club and "church_id" not in club:
        club.setdefault("church_name", factory.name(f"iglesia-{label}"))
    response = await client.post(
        f"{AUTH}/register", json=_payload(factory, label, role="CLUB_DIRECTOR", club=club)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    body["headers"] = _bearer(body["access_token"])
    body["club_name"] = club["name"]
    return body


async def _approve(client, club_id, admin, factory, **body):
    """Approving is also placing (E6): the association assigns the zone, and
    the church the director declared is created under it."""
    payload = {"zone_name": factory.name("zona"), **body}
    return await client.post(f"{ORG}/{club_id}/approve", json=payload, headers=admin["headers"])


@pytest.fixture
def sent_emails(monkeypatch):
    sent = []

    async def capture(to, name, club_name, approved, reason=None):
        sent.append({"to": to, "club": club_name, "approved": approved, "reason": reason})
        return True

    monkeypatch.setattr(email_service, "send_club_decision_email", capture)
    return sent


async def test_search_finds_ntam_by_name_and_code(client):
    ntam = await _find_ntam(client)
    assert ntam["name"] == "Asociación Norte de Tamaulipas"
    assert ntam["type"] == "ASSOCIATION"
    assert ntam["parent_name"]  # the union, to tell homonyms apart

    by_code = await client.get(f"{ORG}/search", params={"q": "ntam"})
    assert "NTAM" in [row["code"] for row in by_code.json()]
    # Only the requested type, and wildcards are literal.
    unions = await client.get(f"{ORG}/search", params={"q": "Tamaulipas", "type": "union"})
    assert all(row["type"] == "UNION" for row in unions.json())
    assert (await client.get(f"{ORG}/search", params={"q": "%%%"})).json() == []


async def test_minor_cannot_pick_club_director(client, factory):
    ntam = await _find_ntam(client)
    club = {"name": factory.name("club-minor"), "association_id": ntam["id"],
            "church_name": factory.name("iglesia-minor")}
    declared = await client.post(
        f"{AUTH}/register",
        json=_payload(factory, "minor-a", role="CLUB_DIRECTOR", is_minor=True, club=club),
    )
    assert declared.status_code == 400, declared.text
    birth = (date.today() - timedelta(days=365 * 12)).isoformat()
    by_age = await client.post(
        f"{AUTH}/register",
        json=_payload(factory, "minor-b", role="CLUB_DIRECTOR", birth_date=birth, club=club),
    )
    assert by_age.status_code == 400, by_age.text

    assert await fetch_all(
        "SELECT 1 FROM users WHERE email LIKE :like", like=f"{factory.prefix}-minor-%"
    ) == []
    assert await fetch_all(
        "SELECT 1 FROM organizations WHERE name = :name", name=club["name"]
    ) == []


async def test_club_payload_rules(client, factory):
    ntam = await _find_ntam(client)
    club = {"name": factory.name("club-rules"), "association_id": ntam["id"],
            "church_name": factory.name("iglesia-rules")}

    # Only directors open clubs.
    student = await client.post(
        f"{AUTH}/register", json=_payload(factory, "rules-student", role="STUDENT", club=club)
    )
    assert student.status_code == 400
    # The parent must be an active association: a union is refused, atomically.
    union = await fetch_one("SELECT id::text AS id FROM organizations WHERE code = 'NMUC'")
    wrong_parent = await client.post(
        f"{AUTH}/register",
        json=_payload(
            factory, "rules-union", role="CLUB_DIRECTOR", club={**club, "association_id": union["id"]}
        ),
    )
    assert wrong_parent.status_code == 400
    unknown = await client.post(
        f"{AUTH}/register",
        json=_payload(
            factory,
            "rules-unknown",
            role="CLUB_DIRECTOR",
            club={**club, "association_id": str(uuid.uuid4())},
        ),
    )
    assert unknown.status_code == 400
    # Nothing half-created: no user, no club.
    assert await fetch_all(
        "SELECT 1 FROM users WHERE email LIKE :like", like=f"{factory.prefix}-rules-%"
    ) == []
    assert await fetch_all(
        "SELECT 1 FROM organizations WHERE name = :name", name=club["name"]
    ) == []


async def test_director_signup_creates_pending_club_hidden_from_public(client, factory):
    ntam = await _find_ntam(client)
    ntam_node = await _ntam_node(ntam)
    director = await _register_director(
        client, factory, "dir-pending", ntam, church="Iglesia Central", city="Reynosa"
    )
    assert director["role"] == "CLUB_DIRECTOR"
    assert director["status"] == "ACTIVE"
    assert director["club_approval"] == "PENDING"
    club_id = director["organization_id"]
    assert club_id

    row = await fetch_one(
        "SELECT type, status, parent_id::text AS parent_id, path::text AS path, city,"
        " metadata_json FROM organizations WHERE id = :id",
        id=uuid.UUID(club_id),
    )
    assert row["type"] == "club" and row["status"] == "pending"
    assert row["parent_id"] == ntam["id"]
    assert row["path"] == f"{ntam_node['path']}.{uuid.UUID(club_id).hex}"
    assert row["city"] == "Reynosa"
    assert row["metadata_json"]["requested_by"] == director["id"]
    assert row["metadata_json"]["church"] == "Iglesia Central"
    assert row["metadata_json"]["contact"] == director["email"]

    me = (await client.get(f"{AUTH}/me", headers=director["headers"])).json()
    assert me["status"] == "ACTIVE"
    assert me["club_approval"] == "PENDING" and me["club_approval_reason"] is None
    assert me["organization_id"] == club_id

    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'ORGANIZATION' AND entity_id = :id",
        id=club_id,
    )
    assert [r["action"] for r in audit] == ["CLUB_REQUEST"]

    # Public reads never show it...
    for response in (
        await client.get(ORG, params={"q": factory.prefix, "type": "club"}),
        await client.get(f"{ORG}/type/CLUB", params={"limit": 1000}),
        await client.get(f"{ORG}/{ntam['id']}/children"),
        await client.get(f"{ORG}/search", params={"q": factory.prefix, "type": "club"}),
    ):
        assert response.status_code == 200, response.text
        assert club_id not in [node["id"] for node in response.json()]
    assert (await client.get(f"{ORG}/{club_id}")).status_code == 404
    # ...nor do other signed-in people see it...
    stranger = await factory.user("stranger", role="INSTRUCTOR")
    assert (await client.get(f"{ORG}/{club_id}", headers=stranger["headers"])).status_code == 404
    listed = await client.get(f"{ORG}/type/CLUB", headers=stranger["headers"])
    assert club_id not in [node["id"] for node in listed.json()]
    # ...but the requesting director does.
    own = await client.get(f"{ORG}/{club_id}", headers=director["headers"])
    assert own.status_code == 200 and own.json()["status"] == "PENDING"
    listed = await client.get(f"{ORG}/type/CLUB", headers=director["headers"])
    assert club_id in [node["id"] for node in listed.json()]

    # One open request at a time, and no duplicate names inside the association.
    again = await client.post(
        f"{ORG}/clubs",
        json={
            "name": factory.name("club-second"),
            "association_id": ntam["id"],
            "church_name": factory.name("iglesia-second"),
        },
        headers=director["headers"],
    )
    assert again.status_code == 409
    clash = await client.post(
        f"{AUTH}/register",
        json=_payload(
            factory,
            "dir-clash",
            role="CLUB_DIRECTOR",
            club={
                "name": director["club_name"].upper(),
                "association_id": ntam["id"],
                "church_name": factory.name("iglesia-clash"),
            },
        ),
    )
    assert clash.status_code == 409
    assert await fetch_one(
        "SELECT 1 AS x FROM users WHERE email = :email", email=factory.email("dir-clash")
    ) is None


async def test_pending_director_manages_nobody(client, factory):
    ntam = await _find_ntam(client)
    director = await _register_director(client, factory, "dir-gate", ntam)
    member = await factory.user("gate-member", organization_id=director["organization_id"])

    listing = await client.get(USERS, headers=director["headers"])
    assert [u["id"] for u in listing.json()] == [director["id"]]
    detail = await client.get(f"{USERS}/{member['id']}", headers=director["headers"])
    assert detail.status_code == 403
    # Writes on the tree stay closed to directors, approved or not.
    patch = await client.patch(
        f"{ORG}/{director['organization_id']}", json={"name": "x"}, headers=director["headers"]
    )
    assert patch.status_code == 403
    self_approve = await client.post(
        f"{ORG}/{director['organization_id']}/approve", headers=director["headers"]
    )
    assert self_approve.status_code == 403


async def test_only_coordinators_of_the_association_decide(client, factory, sent_emails):
    ntam = await _find_ntam(client)
    ntam_node = await _ntam_node(ntam)
    director = await _register_director(client, factory, "dir-approve", ntam)
    club_id = director["organization_id"]

    # A parallel association with its own zone coordinator.
    division = await factory.org("other-div", "division")
    union = await factory.org("other-uni", "union", division)
    other_assoc = await factory.org("other-assoc", "association", union)
    other_zone = await factory.org("other-zone", "zone", other_assoc)
    outsider = await factory.user(
        "coord-other", role="COORDINATOR_ZONE", organization_id=other_zone["id"]
    )
    outsider_admin = await factory.user(
        "admin-other", role="ADMIN_ASSOCIATION", organization_id=other_assoc["id"]
    )
    student = await factory.user("student", role="STUDENT", organization_id=ntam_node["id"])
    floating = await factory.user("coord-floating", role="COORDINATOR_ZONE")
    ntam_admin = await factory.user(
        "admin-ntam", role="ADMIN_ASSOCIATION", organization_id=ntam_node["id"]
    )

    assert (await client.post(f"{ORG}/{club_id}/approve")).status_code == 401
    assert (await client.get(f"{ORG}/pending-clubs")).status_code == 401
    for label, user in (("student", student), ("director", director)):
        denied = await client.post(f"{ORG}/{club_id}/approve", headers=user["headers"])
        assert denied.status_code == 403, label
        assert (
            await client.get(f"{ORG}/pending-clubs", headers=user["headers"])
        ).status_code == 403, label
    for label, user in (
        ("coordinator of another association", outsider),
        ("admin of another association", outsider_admin),
        ("coordinator without organization", floating),
    ):
        for action in ("approve", "reject"):
            denied = await client.post(f"{ORG}/{club_id}/{action}", headers=user["headers"])
            assert denied.status_code == 403, (label, action)
        pending = await client.get(f"{ORG}/pending-clubs", headers=user["headers"])
        assert pending.status_code == 200
        assert club_id not in [c["id"] for c in pending.json()], label
        # Out of scope means invisible too.
        assert (await client.get(f"{ORG}/{club_id}", headers=user["headers"])).status_code == 404
    still = await fetch_one(
        "SELECT status FROM organizations WHERE id = :id", id=uuid.UUID(club_id)
    )
    assert still["status"] == "pending"
    assert sent_emails == []

    # The association's admin sees it, with who asked and where.
    pending = await client.get(f"{ORG}/pending-clubs", headers=ntam_admin["headers"])
    assert pending.status_code == 200, pending.text
    entry = next(c for c in pending.json() if c["id"] == club_id)
    assert entry["status"] == "PENDING" and entry["type"] == "CLUB"
    assert entry["association"]["code"] == "NTAM"
    assert entry["requested_by"] == {
        "id": director["id"],
        "name": director["name"],
        "email": director["email"],
    }
    assert (await client.get(f"{ORG}/{club_id}", headers=ntam_admin["headers"])).status_code == 200
    # PATCH / DELETE are not a back door around the decision.
    sneaky = await client.patch(
        f"{ORG}/{club_id}", json={"status": "ACTIVE"}, headers=ntam_admin["headers"]
    )
    assert sneaky.status_code == 409
    assert (
        await client.delete(f"{ORG}/{club_id}", headers=ntam_admin["headers"])
    ).status_code == 409

    approved = await _approve(client, club_id, ntam_admin, factory)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "ACTIVE"

    club = await fetch_one(
        "SELECT status, metadata_json FROM organizations WHERE id = :id", id=uuid.UUID(club_id)
    )
    assert club["status"] == "active"
    assert club["metadata_json"]["decision"]["status"] == "APPROVED"
    assert club["metadata_json"]["decision"]["by"] == ntam_admin["id"]
    assert club["metadata_json"]["requested_by"] == director["id"]  # kept
    user_row = await fetch_one(
        "SELECT status, club_approval, club_approval_reason, club_approval_at"
        " FROM users WHERE id = :id",
        id=uuid.UUID(director["id"]),
    )
    assert user_row["status"] == "ACTIVE" and user_row["club_approval"] == "APPROVED"
    assert user_row["club_approval_reason"] is None and user_row["club_approval_at"] is not None

    audit = await fetch_all(
        "SELECT action, user_id::text AS user_id, user_role, metadata_json FROM audit_log"
        " WHERE entity_type = 'ORGANIZATION' AND entity_id = :id ORDER BY created_at",
        id=club_id,
    )
    # E6: approving is also placing, so the move is audited on its own.
    assert [r["action"] for r in audit] == ["CLUB_REQUEST", "CLUB_PLACE", "CLUB_APPROVE"]
    assert audit[1]["metadata_json"]["previous_parent_id"] == ntam["id"]
    assert audit[2]["user_id"] == ntam_admin["id"]
    assert audit[2]["user_role"] == "ADMIN_ASSOCIATION"
    assert audit[2]["metadata_json"]["director_id"] == director["id"]

    assert sent_emails == [
        {"to": director["email"], "club": director["club_name"], "approved": True, "reason": None}
    ]

    # Now it is public, gone from the queue, and cannot be decided twice.
    assert (await client.get(f"{ORG}/{club_id}")).status_code == 200
    public = await client.get(ORG, params={"q": factory.prefix, "type": "club"})
    assert club_id in [node["id"] for node in public.json()]
    pending = await client.get(f"{ORG}/pending-clubs", headers=ntam_admin["headers"])
    assert club_id not in [c["id"] for c in pending.json()]
    for action in ("approve", "reject"):
        twice = await client.post(f"{ORG}/{club_id}/{action}", headers=ntam_admin["headers"])
        assert twice.status_code == 409, action
    # The approved director sees the members of the club.
    member = await factory.user("approved-member", organization_id=club_id)
    listing = await client.get(USERS, headers=director["headers"])
    assert {director["id"], member["id"]} <= {u["id"] for u in listing.json()}


async def test_zone_coordinator_rejects_with_reason_and_director_can_retry(
    client, factory, sent_emails
):
    ntam = await _find_ntam(client)
    ntam_node = await _ntam_node(ntam)
    zone = await factory.org("ntam-zone", "zone", ntam_node)
    coordinator = await factory.user(
        "coord-ntam", role="COORDINATOR_ZONE", organization_id=zone["id"]
    )
    director = await _register_director(client, factory, "dir-reject", ntam)
    club_id = director["organization_id"]

    pending = await client.get(f"{ORG}/pending-clubs", headers=coordinator["headers"])
    assert club_id in [c["id"] for c in pending.json()]

    reason = "El club ya está registrado con otro nombre."
    rejected = await client.post(
        f"{ORG}/{club_id}/reject", json={"reason": f"  {reason}  "}, headers=coordinator["headers"]
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    club = await fetch_one(
        "SELECT status, metadata_json FROM organizations WHERE id = :id", id=uuid.UUID(club_id)
    )
    assert club["status"] == "rejected"
    assert club["metadata_json"]["decision"]["reason"] == reason
    me = (await client.get(f"{AUTH}/me", headers=director["headers"])).json()
    assert me["club_approval"] == "REJECTED" and me["club_approval_reason"] == reason
    assert me["status"] == "ACTIVE"

    audit = await fetch_all(
        "SELECT action, details, metadata_json FROM audit_log"
        " WHERE entity_type = 'ORGANIZATION' AND entity_id = :id ORDER BY created_at",
        id=club_id,
    )
    assert [r["action"] for r in audit] == ["CLUB_REQUEST", "CLUB_REJECT"]
    assert audit[1]["metadata_json"]["reason"] == reason
    assert sent_emails == [
        {"to": director["email"], "club": director["club_name"], "approved": False, "reason": reason}
    ]

    # Rejected stays private (the director still sees why).
    assert (await client.get(f"{ORG}/{club_id}")).status_code == 404
    public = await client.get(ORG, params={"q": factory.prefix, "type": "club"})
    assert club_id not in [node["id"] for node in public.json()]
    assert (await client.get(f"{ORG}/{club_id}", headers=director["headers"])).status_code == 200

    # A reason is optional.
    other = await _register_director(client, factory, "dir-reject-bare", ntam)
    bare = await client.post(
        f"{ORG}/{other['organization_id']}/reject", headers=coordinator["headers"]
    )
    assert bare.status_code == 200, bare.text
    row = await fetch_one(
        "SELECT club_approval, club_approval_reason FROM users WHERE id = :id",
        id=uuid.UUID(other["id"]),
    )
    assert row["club_approval"] == "REJECTED" and row["club_approval_reason"] is None

    # After a rejection the director may send a corrected request from the panel.
    retry = await client.post(
        f"{ORG}/clubs",
        json={
            "name": factory.name("club-retry"),
            "association_id": ntam["id"],
            "church_name": factory.name("iglesia-retry"),
            "city": "Reynosa",
        },
        headers=director["headers"],
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["status"] == "PENDING" and retry.json()["parent_id"] == ntam["id"]
    me = (await client.get(f"{AUTH}/me", headers=director["headers"])).json()
    assert me["club_approval"] == "PENDING" and me["club_approval_reason"] is None
    assert me["organization_id"] == retry.json()["id"]


async def test_director_without_club_creates_it_from_the_panel(client, factory):
    ntam = await _find_ntam(client)
    created = await client.post(
        f"{AUTH}/register", json=_payload(factory, "dir-later", role="CLUB_DIRECTOR")
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["club_approval"] is None and body["organization_id"] is None
    headers = _bearer(body["access_token"])

    club = {
        "name": factory.name("club-later"),
        "association_id": ntam["id"],
        "church_name": factory.name("iglesia-later"),
    }
    assert (await client.post(f"{ORG}/clubs", json=club)).status_code == 401
    student = await factory.user("later-student", role="STUDENT")
    assert (
        await client.post(f"{ORG}/clubs", json=club, headers=student["headers"])
    ).status_code == 403

    response = await client.post(f"{ORG}/clubs", json=club, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PENDING"
    me = (await client.get(f"{AUTH}/me", headers=headers)).json()
    assert me["club_approval"] == "PENDING"
    assert me["organization_id"] == response.json()["id"]


async def test_decision_email_never_raises_without_resend():
    # No RESEND_API_KEY in tests: skipped, reported as not sent, no exception.
    sent = await email_service.send_club_decision_email(
        "nobody@example.com", "Dir <b>", "Club & Co", False, "<script>"
    )
    assert sent is False
    html = email_service.club_decision_email_html("Dir <b>", "Club & Co", False, "<script>")
    assert "<script>" not in html and "&lt;script&gt;" in html


async def test_association_search_ignores_accents(client):
    """People type 'asociacion norte' without accents; the directory stores 'Asociación Norte…'."""
    response = await client.get("/api/v1/org-nodes/search", params={"q": "asociacion norte de tam", "type": "association"})
    assert response.status_code == 200
    assert any(item["code"] == "NTAM" for item in response.json())
