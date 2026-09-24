"""Organization tree: public reads, admin-only scoped writes, ltree paths."""

import uuid

from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("org")

ORG = "/api/v1/org-nodes"


async def _create(client, headers, factory, label, org_type, parent_id=None, **extra):
    body = {"name": factory.name(label), "type": org_type, "parent_id": parent_id, **extra}
    return await client.post(ORG, json=body, headers=headers)


async def test_write_without_token_is_401(client, factory):
    response = await client.post(ORG, json={"name": factory.name("anon"), "type": "DIVISION"})
    assert response.status_code == 401
    node_id = uuid.uuid4()
    assert (await client.patch(f"{ORG}/{node_id}", json={"name": "x"})).status_code == 401
    assert (await client.delete(f"{ORG}/{node_id}")).status_code == 401


async def test_non_admin_roles_cannot_write(client, factory):
    division = await factory.org("rbac-div", "division")
    for role in ("STUDENT", "PARENT_GUARDIAN", "INSTRUCTOR", "CLUB_DIRECTOR"):
        user = await factory.user(f"rbac-{role.lower()}", role=role, organization_id=division["id"])
        created = await _create(client, user["headers"], factory, "nope", "union", division["id"])
        assert created.status_code == 403, role
        patched = await client.patch(
            f"{ORG}/{division['id']}", json={"name": "hacked"}, headers=user["headers"]
        )
        assert patched.status_code == 403, role
        deleted = await client.delete(f"{ORG}/{division['id']}", headers=user["headers"])
        assert deleted.status_code == 403, role


async def test_master_creates_division_then_union_with_ltree_path(client, factory):
    master = await factory.user("master", role="MASTER_GC")
    division = await _create(client, master["headers"], factory, "div", "DIVISION", country="MX")
    assert division.status_code == 201, division.text
    division = division.json()
    assert division["type"] == "DIVISION" and division["status"] == "ACTIVE"
    assert division["path_ids"] == []

    union = await _create(client, master["headers"], factory, "uni", "union", division["id"])
    assert union.status_code == 201, union.text
    union = union.json()
    assert union["parent_id"] == division["id"]
    assert union["path_ids"] == [division["id"]]

    rows = await fetch_all(
        "SELECT id::text, type, status, nlevel(path) AS depth, path::text AS path"
        " FROM organizations WHERE id IN (:a, :b)",
        a=uuid.UUID(division["id"]),
        b=uuid.UUID(union["id"]),
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id[division["id"]]["depth"] == 1
    assert by_id[union["id"]]["depth"] == 2
    assert by_id[division["id"]]["type"] == "division"  # stored lowercase
    assert by_id[division["id"]]["status"] == "active"
    assert by_id[division["id"]]["path"] == uuid.UUID(division["id"]).hex
    assert by_id[union["id"]]["path"] == (
        f"{uuid.UUID(division['id']).hex}.{uuid.UUID(union['id']).hex}"
    )

    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'ORGANIZATION' AND entity_id = :id",
        id=union["id"],
    )
    assert [row["action"] for row in audit] == ["CREATE"]

    # Reads are public.
    assert (await client.get(f"{ORG}/{union['id']}")).status_code == 200
    children = await client.get(f"{ORG}/{division['id']}/children")
    assert [c["id"] for c in children.json()] == [union["id"]]
    by_type = await client.get(f"{ORG}/type/UNION")
    assert union["id"] in [n["id"] for n in by_type.json()]
    listing = await client.get(ORG, params={"q": factory.prefix, "type": "division"})
    assert division["id"] in [n["id"] for n in listing.json()]
    assert (await client.get(f"{ORG}/{uuid.uuid4()}")).status_code == 404


async def test_wrong_type_order_is_400(client, factory):
    master = await factory.user("order-master", role="MASTER_GC")
    division = await factory.org("order-div", "division")
    union = await factory.org("order-uni", "union", division)

    skip = await _create(client, master["headers"], factory, "skip", "association", division["id"])
    assert skip.status_code == 400
    upside_down = await _create(client, master["headers"], factory, "up", "division", union["id"])
    assert upside_down.status_code == 400
    same = await _create(client, master["headers"], factory, "same", "union", union["id"])
    assert same.status_code == 400
    root_union = await _create(client, master["headers"], factory, "rootu", "union")
    assert root_union.status_code == 400
    unknown = await _create(client, master["headers"], factory, "unk", "club_network", union["id"])
    assert unknown.status_code == 422

    # The whole chain, in order, works.
    parent = union
    for org_type in ("association", "zone", "church", "club", "unit"):
        # A club always names its ministry (019_club_ministry.sql, rule 3 of ESTADO.md).
        extra = {"ministry": "pathfinders"} if org_type == "club" else {}
        if org_type == "club":
            nameless = await _create(
                client, master["headers"], factory, "club-sin", "club", parent["id"]
            )
            assert nameless.status_code == 422, nameless.text
        created = await _create(
            client, master["headers"], factory, org_type, org_type, parent["id"], **extra
        )
        assert created.status_code == 201, created.text
        if org_type == "club":
            assert created.json()["ministry"]["slug"] == "pathfinders"
        else:
            assert created.json()["ministry"] is None
        parent = created.json()
    leaf = await fetch_one(
        "SELECT nlevel(path) AS depth FROM organizations WHERE id = :id", id=uuid.UUID(parent["id"])
    )
    assert leaf["depth"] == 7
    below_unit = await _create(client, master["headers"], factory, "below", "unit", parent["id"])
    assert below_unit.status_code == 400


async def test_admin_is_confined_to_own_subtree(client, factory):
    division = await factory.org("scope-div", "division")
    union_a = await factory.org("scope-uni-a", "union", division)
    union_b = await factory.org("scope-uni-b", "union", division)
    assoc_b = await factory.org("scope-assoc-b", "association", union_b)
    admin_a = await factory.user("scope-admin-a", role="ADMIN_UNION", organization_id=union_a["id"])

    own = await _create(
        client, admin_a["headers"], factory, "scope-own", "association", union_a["id"]
    )
    assert own.status_code == 201, own.text

    foreign = await _create(
        client, admin_a["headers"], factory, "scope-foreign", "association", union_b["id"]
    )
    assert foreign.status_code == 403
    deeper = await _create(client, admin_a["headers"], factory, "scope-deep", "zone", assoc_b["id"])
    assert deeper.status_code == 403
    # Not above itself either: a sibling union under the shared division.
    sibling = await _create(
        client, admin_a["headers"], factory, "scope-sib", "union", division["id"]
    )
    assert sibling.status_code == 403
    root = await _create(client, admin_a["headers"], factory, "scope-root", "division")
    assert root.status_code == 403

    patch_foreign = await client.patch(
        f"{ORG}/{assoc_b['id']}", json={"name": factory.name("hijack")}, headers=admin_a["headers"]
    )
    assert patch_foreign.status_code == 403
    delete_foreign = await client.delete(f"{ORG}/{assoc_b['id']}", headers=admin_a["headers"])
    assert delete_foreign.status_code == 403

    patch_own = await client.patch(
        f"{ORG}/{own.json()['id']}",
        json={"city": "Monterrey", "location": {"type": "Point", "coordinates": [-100.3, 25.6]}},
        headers=admin_a["headers"],
    )
    assert patch_own.status_code == 200, patch_own.text
    assert patch_own.json()["city"] == "Monterrey"
    assert patch_own.json()["latitude"] == 25.6 and patch_own.json()["longitude"] == -100.3

    # An admin without an organization has no scope at all.
    floating = await factory.user("scope-floating", role="ADMIN_DIVISION")
    nowhere = await _create(
        client, floating["headers"], factory, "scope-nw", "union", division["id"]
    )
    assert nowhere.status_code == 403


async def test_delete_is_soft_and_refused_with_active_children(client, factory):
    master = await factory.user("del-master", role="MASTER_GC")
    division = await factory.org("del-div", "division")
    union = await factory.org("del-uni", "union", division)

    refused = await client.delete(f"{ORG}/{division['id']}", headers=master["headers"])
    assert refused.status_code == 400
    refused_patch = await client.patch(
        f"{ORG}/{division['id']}", json={"status": "INACTIVE"}, headers=master["headers"]
    )
    assert refused_patch.status_code == 400

    deleted = await client.delete(f"{ORG}/{union['id']}", headers=master["headers"])
    assert deleted.status_code == 204
    row = await fetch_one(
        "SELECT status, updated_at FROM organizations WHERE id = :id", id=uuid.UUID(union["id"])
    )
    assert row["status"] == "inactive"  # still there
    assert row["updated_at"].tzinfo is not None

    # With no active children left the parent can go.
    deleted = await client.delete(f"{ORG}/{division['id']}", headers=master["headers"])
    assert deleted.status_code == 204
    # No new nodes under an inactive parent.
    orphan = await _create(
        client, master["headers"], factory, "del-orphan", "union", division["id"]
    )
    assert orphan.status_code == 400


async def test_free_form_rows_are_left_alone(client, factory):
    master = await factory.user("free-master", role="MASTER_GC")
    network = await factory.org("free-network", "club_network")

    assert (await client.get(f"{ORG}/{network['id']}")).status_code == 200
    patched = await client.patch(
        f"{ORG}/{network['id']}", json={"name": factory.name("renamed")}, headers=master["headers"]
    )
    assert patched.status_code == 400
    deleted = await client.delete(f"{ORG}/{network['id']}", headers=master["headers"])
    assert deleted.status_code == 400
    child = await _create(
        client, master["headers"], factory, "free-child", "club", network["id"], ministry="pathfinders"
    )
    assert child.status_code == 400
    row = await fetch_one(
        "SELECT name, status FROM organizations WHERE id = :id", id=uuid.UUID(network["id"])
    )
    assert row["name"] == factory.name("free-network") and row["status"] == "active"
