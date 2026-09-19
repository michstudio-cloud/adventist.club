"""Honors: public catalogue, review workflow, versioning, audit trail."""

import uuid

import pytest_asyncio

from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("honors")

HONORS = "/api/v1/honors"
LEGACY_FIELDS = {"id", "name", "slug", "image_url", "source_url", "active"}


@pytest_asyncio.fixture(scope="module")
async def staff(factory):
    division = await factory.org("div", "division")
    union = await factory.org("uni", "union", division)
    association = await factory.org("assoc", "association", union)
    zone = await factory.org("zone", "zone", association)
    other_union = await factory.org("other-uni", "union", division)
    other_assoc = await factory.org("other-assoc", "association", other_union)
    other_zone = await factory.org("other-zone", "zone", other_assoc)
    return {
        "zone": zone,
        "association": association,
        "instructor": await factory.user("instructor", "INSTRUCTOR", zone["id"]),
        "instructor2": await factory.user("instructor2", "INSTRUCTOR", zone["id"]),
        "coordinator": await factory.user("coordinator", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "other_coordinator": await factory.user(
            "other-coord", "COORDINATOR_ZONE", other_zone["id"]
        ),
        "other_assoc_admin": await factory.user(
            "other-assoc", "ADMIN_ASSOCIATION", other_assoc["id"]
        ),
        "master": await factory.user("master", "MASTER_GC"),
        "student": await factory.user("student", "STUDENT", zone["id"]),
    }


def _body(factory, label, **extra):
    return {
        "name": factory.name(label),
        "code": f"{factory.prefix}-{label}"[:40],
        "description": "Knots and lashings",
        "category": "recreation",
        "difficulty_level": "BEGINNER",
        "type": "LOCAL",
        "patch_image_url": "https://media.example/patches/knots.png",
        "requirements": [
            {
                "order": 1,
                "description": "Tie a bowline",
                "question_bank": [
                    {
                        "question_text": "Which knot makes a fixed loop?",
                        "question_type": "MULTIPLE_CHOICE",
                        "options": ["Bowline", "Sheet bend"],
                        "correct_answer": "SECRET-ANSWER-BOWLINE",
                        "points": 2,
                        "explanation": "SECRET-EXPLANATION",
                    },
                    {
                        "question_text": "A reef knot is secure for climbing",
                        "question_type": "TRUE_FALSE",
                        "correct_answer": "SECRET-ANSWER-FALSE",
                    },
                ],
            },
            {"order": 2, "description": "Demonstrate a square lashing", "is_theoretical": False},
        ],
        "resources": [{"name": "Manual", "url": "https://media.example/manual.pdf", "type": "pdf"}],
        **extra,
    }


async def _create(client, staff, factory, label, author="instructor", **extra):
    response = await client.post(
        HONORS, json=_body(factory, label, **extra), headers=staff[author]["headers"]
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _review(client, user, honor_id, action, comments=None):
    return await client.post(
        f"{HONORS}/{honor_id}/review",
        json={"action": action, "comments": comments},
        headers=user["headers"],
    )


async def _publish_through_workflow(client, staff, honor_id):
    submitted = await client.post(
        f"{HONORS}/{honor_id}/submit", headers=staff["instructor"]["headers"]
    )
    assert submitted.status_code == 200, submitted.text
    assert (await _review(client, staff["coordinator"], honor_id, "APPROVE")).status_code == 200
    final = await _review(client, staff["assoc_admin"], honor_id, "APPROVE")
    assert final.status_code == 200, final.text
    return final.json()


async def _public_ids(client, factory):
    response = await client.get(HONORS, params={"q": factory.prefix})
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()}


async def test_public_list_keeps_legacy_contract(client):
    response = await client.get(HONORS)
    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list) and rows, "the sample PUBLISHED catalogue row must be listed"
    for row in rows:
        assert LEGACY_FIELDS <= set(row)
        assert row["active"] is True and row["status"] == "PUBLISHED"
    assert response.headers["X-Total-Count"] == str(len(rows))
    live = await fetch_all(
        "SELECT h.id::text FROM honors h JOIN ministries m ON m.id = h.ministry_id"
        " WHERE m.slug = 'pathfinders' AND h.active AND h.status = 'PUBLISHED'"
    )
    assert {row["id"] for row in rows} == {row["id"] for row in live}

    assert (await client.get(HONORS, params={"ministry": "does-not-exist"})).json() == []
    paged = await client.get(HONORS, params={"limit": 1, "offset": 0})
    assert len(paged.json()) == 1
    # LIKE wildcards in `q` are literals, not a 500 and not a match-all.
    assert (await client.get(HONORS, params={"q": "%_%"})).status_code == 200


async def test_categories(client):
    response = await client.get(f"{HONORS}/categories")
    assert response.status_code == 200
    slugs = {row["slug"] for row in response.json()}
    assert {"recreation", "nature", "vocational"} <= slugs
    assert all(set(row) == {"id", "name", "slug"} for row in response.json())


async def test_create_requires_author_role(client, staff, factory):
    body = _body(factory, "forbidden")
    assert (await client.post(HONORS, json=body)).status_code == 401
    for role in ("student", "coordinator"):
        response = await client.post(HONORS, json=body, headers=staff[role]["headers"])
        assert response.status_code == 403, role
    unknown_category = await client.post(
        HONORS,
        json=_body(factory, "badcat", category="nope"),
        headers=staff["instructor"]["headers"],
    )
    assert unknown_category.status_code == 400


async def test_full_workflow_to_published(client, staff, factory):
    honor = await _create(client, staff, factory, "flow")
    honor_id = honor["id"]
    assert honor["status"] == "DRAFT" and honor["version"] == 1
    assert honor["org_scope_id"] == staff["zone"]["id"]
    assert honor["category"]["slug"] == "recreation"
    assert (
        honor["image_url"] == honor["patch_image_url"] == "https://media.example/patches/knots.png"
    )
    assert [r["question_count"] for r in honor["requirements"]] == [2, 0]

    # Drafts are invisible to the public, in the list and by id.
    assert honor_id not in await _public_ids(client, factory)
    assert (await client.get(f"{HONORS}/{honor_id}")).status_code == 404
    assert (
        await client.get(f"{HONORS}/{honor_id}", headers=staff["student"]["headers"])
    ).status_code == 404
    assert (
        await client.get(f"{HONORS}/{honor_id}", headers=staff["instructor"]["headers"])
    ).status_code == 200
    # Anonymous callers cannot ask for another status either.
    drafts = await client.get(HONORS, params={"q": factory.prefix, "status": "DRAFT"})
    assert honor_id not in {row["id"] for row in drafts.json()}

    # Only the creator submits; nobody reviews a draft.
    assert (
        await client.post(f"{HONORS}/{honor_id}/submit", headers=staff["instructor2"]["headers"])
    ).status_code == 403
    assert (await _review(client, staff["coordinator"], honor_id, "APPROVE")).status_code == 403

    submitted = await client.post(
        f"{HONORS}/{honor_id}/submit", headers=staff["instructor"]["headers"]
    )
    assert submitted.status_code == 200 and submitted.json()["status"] == "ZONE_REVIEW"
    assert honor_id not in await _public_ids(client, factory)
    again = await client.post(f"{HONORS}/{honor_id}/submit", headers=staff["instructor"]["headers"])
    assert again.status_code == 400
    locked = await client.put(
        f"{HONORS}/{honor_id}", json={"description": "x"}, headers=staff["instructor"]["headers"]
    )
    assert locked.status_code == 400

    pending = await client.get(f"{HONORS}/pending/reviews", headers=staff["coordinator"]["headers"])
    assert honor_id in {row["id"] for row in pending.json()}
    elsewhere = await client.get(
        f"{HONORS}/pending/reviews", headers=staff["other_coordinator"]["headers"]
    )
    assert honor_id not in {row["id"] for row in elsewhere.json()}

    # Wrong role, wrong scope, wrong stage.
    assert (await _review(client, staff["instructor2"], honor_id, "APPROVE")).status_code == 403
    assert (
        await _review(client, staff["other_coordinator"], honor_id, "APPROVE")
    ).status_code == 403
    assert (await _review(client, staff["coordinator"], honor_id, "MAYBE")).status_code == 422

    zone = await _review(client, staff["coordinator"], honor_id, "APPROVE", "Looks good")
    assert zone.status_code == 200, zone.text
    assert zone.json()["status"] == "ASSOCIATION_REVIEW"
    assert zone.json()["approved_zone_org_id"] == staff["zone"]["id"]
    assert honor_id not in await _public_ids(client, factory)

    # The zone coordinator has no say at association level.
    assert (await _review(client, staff["coordinator"], honor_id, "APPROVE")).status_code == 403
    assert (
        await _review(client, staff["other_assoc_admin"], honor_id, "APPROVE")
    ).status_code == 403

    final = await _review(client, staff["assoc_admin"], honor_id, "APPROVE", "Approved")
    assert final.status_code == 200, final.text
    published = final.json()
    assert published["status"] == "PUBLISHED" and published["active"] is True
    assert published["published_at"] is not None
    assert published["approved_association_org_id"] == staff["association"]["id"]
    assert [r["action"] for r in published["review_history"]] == ["APPROVE", "APPROVE"]

    assert honor_id in await _public_ids(client, factory)
    by_category = await client.get(HONORS, params={"q": factory.prefix, "category": "recreation"})
    assert honor_id in {row["id"] for row in by_category.json()}
    other_category = await client.get(HONORS, params={"q": factory.prefix, "category": "nature"})
    assert honor_id not in {row["id"] for row in other_category.json()}

    # Reviews are inserted, one row per decision.
    reviews = await fetch_all(
        "SELECT action, reviewer_role, comments, reviewed_at FROM honor_reviews"
        " WHERE honor_id = :id ORDER BY reviewed_at",
        id=uuid.UUID(honor_id),
    )
    assert [(r["action"], r["reviewer_role"]) for r in reviews] == [
        ("APPROVE", "COORDINATOR_ZONE"),
        ("APPROVE", "ADMIN_ASSOCIATION"),
    ]
    assert all(r["reviewed_at"].tzinfo is not None for r in reviews)

    audit = await fetch_all(
        "SELECT action, user_role FROM audit_log WHERE entity_type = 'HONOR' AND entity_id = :id"
        " ORDER BY created_at",
        id=honor_id,
    )
    assert [row["action"] for row in audit] == ["CREATE", "SUBMIT", "APPROVE", "APPROVE"]
    assert audit[0]["user_role"] == "INSTRUCTOR"


async def test_public_detail_never_leaks_answers(client, staff, factory):
    honor = await _create(client, staff, factory, "leak")
    # Not in the author's create/detail response either: answers live in /instructor only.
    assert "SECRET-" not in str(honor)
    await _publish_through_workflow(client, staff, honor["id"])

    for headers in (None, staff["student"]["headers"], staff["instructor"]["headers"]):
        response = await client.get(f"{HONORS}/{honor['id']}", headers=headers)
        assert response.status_code == 200, response.text
        assert "correct_answer" not in response.text
        assert "question_bank" not in response.text
        assert "SECRET-" not in response.text
        assert [r["question_count"] for r in response.json()["requirements"]] == [2, 0]
    anonymous = (await client.get(f"{HONORS}/{honor['id']}")).json()
    assert "review_history" not in anonymous
    assert anonymous["resources"][0]["url"] == "https://media.example/manual.pdf"

    listing = await client.get(HONORS, params={"q": factory.prefix})
    assert "correct_answer" not in listing.text and "SECRET-" not in listing.text


async def test_instructor_detail_has_question_bank(client, staff, factory):
    honor = await _create(client, staff, factory, "bank")
    url = f"{HONORS}/{honor['id']}/instructor"

    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=staff["student"]["headers"])).status_code == 403
    # Someone else's draft stays hidden, even from another instructor.
    assert (await client.get(url, headers=staff["instructor2"]["headers"])).status_code == 404

    own = await client.get(url, headers=staff["instructor"]["headers"])
    assert own.status_code == 200, own.text
    bank = own.json()["requirements_with_questions"][0]["question_bank"]
    assert [q["correct_answer"] for q in bank] == ["SECRET-ANSWER-BOWLINE", "SECRET-ANSWER-FALSE"]
    assert bank[0]["options"] == ["Bowline", "Sheet bend"] and bank[0]["points"] == 2
    assert bank[0]["explanation"] == "SECRET-EXPLANATION"

    await _publish_through_workflow(client, staff, honor["id"])
    published = await client.get(url, headers=staff["instructor2"]["headers"])
    assert published.status_code == 200
    assert "SECRET-ANSWER-BOWLINE" in published.text


async def test_request_changes_and_reject_return_to_draft(client, staff, factory):
    honor = await _create(client, staff, factory, "changes")
    honor_id = honor["id"]
    headers = staff["instructor"]["headers"]
    await client.post(f"{HONORS}/{honor_id}/submit", headers=headers)

    back = await _review(
        client, staff["coordinator"], honor_id, "REQUEST_CHANGES", "Add a requirement"
    )
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "DRAFT"
    assert back.json()["review_history"][-1]["comments"] == "Add a requirement"

    # Editable again: replace the requirements, then resubmit.
    edited = await client.put(
        f"{HONORS}/{honor_id}",
        json={
            "description": "Improved",
            "requirements": [
                {
                    "description": "Only one now",
                    "question_bank": [
                        {
                            "question_text": "Q",
                            "question_type": "SHORT_ANSWER",
                            "correct_answer": "SECRET-A",
                        }
                    ],
                }
            ],
        },
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "Improved"
    assert [r["description"] for r in edited.json()["requirements"]] == ["Only one now"]
    questions = await fetch_all(
        "SELECT q.id FROM honor_questions q JOIN honor_requirements r ON r.id = q.requirement_id"
        " WHERE r.honor_id = :id",
        id=uuid.UUID(honor_id),
    )
    assert len(questions) == 1  # the old bank went with the old requirements

    await client.post(f"{HONORS}/{honor_id}/submit", headers=headers)
    await _review(client, staff["coordinator"], honor_id, "APPROVE")
    rejected = await _review(client, staff["assoc_admin"], honor_id, "REJECT", "Not yet")
    assert rejected.status_code == 200 and rejected.json()["status"] == "DRAFT"
    assert rejected.json()["published_at"] is None

    actions = await fetch_all(
        "SELECT action FROM honor_reviews WHERE honor_id = :id ORDER BY reviewed_at",
        id=uuid.UUID(honor_id),
    )
    assert [row["action"] for row in actions] == ["REQUEST_CHANGES", "APPROVE", "REJECT"]
    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'HONOR' AND entity_id = :id"
        " ORDER BY created_at",
        id=honor_id,
    )
    assert [row["action"] for row in audit] == [
        "CREATE",
        "SUBMIT",
        "REQUEST_CHANGES",
        "UPDATE",
        "SUBMIT",
        "APPROVE",
        "REJECT",
    ]


async def test_update_rules(client, staff, factory):
    honor = await _create(client, staff, factory, "update")
    url = f"{HONORS}/{honor['id']}"
    assert (await client.put(url, json={"description": "x"})).status_code == 401
    stranger = await client.put(
        url, json={"description": "x"}, headers=staff["instructor2"]["headers"]
    )
    assert stranger.status_code == 403
    reviewer = await client.put(
        url, json={"description": "x"}, headers=staff["assoc_admin"]["headers"]
    )
    assert reviewer.status_code == 403
    by_master = await client.put(
        url,
        json={"description": "By master", "category": "nature"},
        headers=staff["master"]["headers"],
    )
    assert by_master.status_code == 200, by_master.text
    assert by_master.json()["description"] == "By master"
    assert by_master.json()["category"]["slug"] == "nature"
    assert by_master.json()["code"] == honor["code"]  # immutable


async def test_empty_honor_cannot_be_submitted(client, staff, factory):
    honor = await _create(client, staff, factory, "empty", requirements=[])
    response = await client.post(
        f"{HONORS}/{honor['id']}/submit", headers=staff["instructor"]["headers"]
    )
    assert response.status_code == 400


async def test_duplicate_code_is_409(client, staff, factory):
    await _create(client, staff, factory, "dupe")
    body = _body(factory, "dupe", name=factory.name("dupe again"))
    for author in ("instructor", "instructor2"):
        response = await client.post(HONORS, json=body, headers=staff[author]["headers"])
        assert response.status_code == 409, response.text
    rows = await fetch_all("SELECT id FROM honors WHERE code = :code", code=body["code"])
    assert len(rows) == 1


async def test_duplicate_code_race_is_409_not_500(client, staff, factory):
    import asyncio

    body = _body(factory, "race")
    responses = await asyncio.gather(
        *[client.post(HONORS, json=body, headers=staff["instructor"]["headers"]) for _ in range(4)]
    )
    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409]


async def test_same_name_gets_a_unique_slug(client, staff, factory):
    first = await _create(client, staff, factory, "slug-a", name=factory.name("same name"))
    second = await _create(client, staff, factory, "slug-b", name=factory.name("same name"))
    assert first["slug"] != second["slug"]


async def test_version_archives_old_and_links_previous(client, staff, factory):
    honor = await _create(client, staff, factory, "ver")
    await _publish_through_workflow(client, staff, honor["id"])
    url = f"{HONORS}/{honor['id']}/version"
    body = {"changes_description": "Second edition"}

    assert (await client.post(url, json=body)).status_code == 401
    assert (
        await client.post(url, json=body, headers=staff["instructor2"]["headers"])
    ).status_code == 403
    assert (
        await client.post(url, json=body, headers=staff["other_assoc_admin"]["headers"])
    ).status_code == 403

    created = await client.post(url, json=body, headers=staff["instructor"]["headers"])
    assert created.status_code == 201, created.text
    new = created.json()
    assert new["id"] != honor["id"]
    assert new["status"] == "DRAFT" and new["version"] == 2
    assert new["version_metadata"] == {
        "version": 2,
        "previous_version_id": honor["id"],
        "changes_description": "Second edition",
    }
    assert new["code"] == f"{honor['code']}_v2" and new["slug"] != honor["slug"]
    # Content is carried over, question bank included.
    assert [r["question_count"] for r in new["requirements"]] == [2, 0]
    assert len(new["resources"]) == 1

    old = await fetch_one(
        "SELECT status, active FROM honors WHERE id = :id", id=uuid.UUID(honor["id"])
    )
    assert old["status"] == "ARCHIVED" and old["active"] is False
    row = await fetch_one(
        "SELECT previous_version_id::text AS prev, version FROM honors WHERE id = :id",
        id=uuid.UUID(new["id"]),
    )
    assert row["prev"] == honor["id"] and row["version"] == 2

    public = await _public_ids(client, factory)
    assert honor["id"] not in public and new["id"] not in public
    assert (await client.get(f"{HONORS}/{honor['id']}")).status_code == 404

    # An archived honor cannot be versioned again.
    again = await client.post(url, json=body, headers=staff["instructor"]["headers"])
    assert again.status_code == 400

    audit = await fetch_all(
        "SELECT entity_id, action FROM audit_log WHERE entity_type = 'HONOR'"
        " AND entity_id IN (:old, :new) AND action IN ('CREATE', 'ARCHIVE')",
        old=honor["id"],
        new=new["id"],
    )
    assert {(r["entity_id"], r["action"]) for r in audit} == {
        (honor["id"], "CREATE"),
        (honor["id"], "ARCHIVE"),
        (new["id"], "CREATE"),
    }


async def test_publish_shortcut_is_master_only(client, staff, factory):
    honor = await _create(client, staff, factory, "shortcut")
    url = f"{HONORS}/{honor['id']}/publish"
    assert (await client.post(url, headers=staff["assoc_admin"]["headers"])).status_code == 403
    assert (await client.post(url, headers=staff["instructor"]["headers"])).status_code == 403
    done = await client.post(url, headers=staff["master"]["headers"])
    assert done.status_code == 200 and done.json()["status"] == "PUBLISHED"
    assert honor["id"] in await _public_ids(client, factory)


async def test_delete_archives(client, staff, factory):
    honor = await _create(client, staff, factory, "del")
    url = f"{HONORS}/{honor['id']}"
    assert (await client.delete(url, headers=staff["instructor2"]["headers"])).status_code == 403
    assert (await client.delete(url, headers=staff["instructor"]["headers"])).status_code == 204
    row = await fetch_one(
        "SELECT status, active FROM honors WHERE id = :id", id=uuid.UUID(honor["id"])
    )
    assert row["status"] == "ARCHIVED" and row["active"] is False
    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'HONOR' AND entity_id = :id"
        " ORDER BY created_at",
        id=honor["id"],
    )
    assert [r["action"] for r in audit] == ["CREATE", "DELETE"]

    published = await _create(client, staff, factory, "del-pub")
    await _publish_through_workflow(client, staff, published["id"])
    url = f"{HONORS}/{published['id']}"
    assert (await client.delete(url, headers=staff["instructor"]["headers"])).status_code == 400
    assert (await client.delete(url, headers=staff["master"]["headers"])).status_code == 204


async def test_my_created_and_stats(client, staff, factory):
    honor = await _create(client, staff, factory, "mine", author="instructor2")
    mine = await client.get(f"{HONORS}/my/created", headers=staff["instructor2"]["headers"])
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert set(body) == {"items", "total", "limit", "offset", "has_more"}
    assert honor["id"] in {row["id"] for row in body["items"]}
    assert all(row["status"] for row in body["items"])
    none = await client.get(
        f"{HONORS}/my/created",
        params={"status": "PUBLISHED"},
        headers=staff["instructor2"]["headers"],
    )
    assert honor["id"] not in {row["id"] for row in none.json()["items"]}
    assert (
        await client.get(f"{HONORS}/my/created", headers=staff["student"]["headers"])
    ).status_code == 403
    assert (await client.get(f"{HONORS}/my/created")).status_code == 401

    # Literal routes are not swallowed by /{honor_id}.
    assert (await client.get(f"{HONORS}/pending/reviews")).status_code == 401
    assert (await client.get(f"{HONORS}/stats/overview")).status_code == 401
    assert (
        await client.get(f"{HONORS}/stats/overview", headers=staff["instructor"]["headers"])
    ).status_code == 403
    for role in ("assoc_admin", "master"):
        stats = await client.get(f"{HONORS}/stats/overview", headers=staff[role]["headers"])
        assert stats.status_code == 200, stats.text
        body = stats.json()
        assert set(body) == {
            "total_honors",
            "by_status",
            "by_category",
            "by_difficulty",
            "recently_published",
        }
        assert body["total_honors"] == sum(body["by_status"].values())
        assert body["by_status"].get("PUBLISHED", 0) >= 1


async def test_public_endpoints_ignore_a_bad_token(client):
    headers = {"Authorization": "Bearer not-a-jwt"}
    assert (await client.get(HONORS, headers=headers)).status_code == 200
    assert (await client.get(f"{HONORS}/categories", headers=headers)).status_code == 200
