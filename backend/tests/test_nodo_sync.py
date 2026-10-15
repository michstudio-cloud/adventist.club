"""scripts/nodo_sync.py (029): the pure parts of export/import with the local event node.

The round trip against two real databases is exercised by hand (nodo-evento/README.md
«Prueba»); here: the three-way comparison that decides what an import applies, the columns
that never travel, and that connection strings are never echoed.
"""
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "nodo_sync.py"
_spec = importlib.util.spec_from_file_location("nodo_sync", _PATH)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def _empty():
    return {table: [] for table in sync.EVENT_TABLE_NAMES}


def _with(table, *rows):
    data = _empty()
    data[table] = list(rows)
    return data


def _snap(table, *rows):
    return {table: {r["id"]: sync.fingerprint(r, table) for r in rows}}


EV = "evaluations"


def _ev(id_, points, status="CONFIRMED"):
    return {"id": id_, "points": points, "status": status, "revision": 1}


def test_unchanged_rows_produce_nothing():
    row = _ev("e1", "10.00")
    plan = sync.plan_changes(_snap(EV, row), _with(EV, row), _with(EV, dict(row)), prefer=None)
    assert plan["unchanged"][EV] == 1
    assert not plan["insert"][EV] and not plan["update"][EV] and not plan["delete"][EV]
    assert not plan["conflicts"] and not plan["cloud_only"]


def test_node_changes_are_applied_new_rows_inserted_and_deletions_propagated():
    a, b = _ev("a", "10.00"), _ev("b", "5.00")
    node_a = {**a, "status": "VOID", "revision": 2}
    new = _ev("n", "7.00")
    plan = sync.plan_changes(_snap(EV, a, b), _with(EV, node_a, new), _with(EV, a, b), prefer=None)
    assert plan["update"][EV] == [node_a]
    assert plan["insert"][EV] == [new]
    assert plan["delete"][EV] == ["b"]
    assert not plan["conflicts"]


def test_both_sides_changed_is_a_conflict_unless_a_side_is_preferred():
    a = _ev("a", "10.00")
    node, cloud = {**a, "points": "8.00"}, {**a, "points": "9.00"}
    plan = sync.plan_changes(_snap(EV, a), _with(EV, node), _with(EV, cloud), prefer=None)
    assert [c["id"] for c in plan["conflicts"]] == ["a"] and plan["conflicts"][0]["fields"] == ["points"]
    assert not plan["update"][EV]
    plan = sync.plan_changes(_snap(EV, a), _with(EV, node), _with(EV, cloud), prefer="node")
    assert plan["update"][EV] == [node] and not plan["conflicts"]
    plan = sync.plan_changes(_snap(EV, a), _with(EV, node), _with(EV, cloud), prefer="prod")
    assert not plan["update"][EV] and not plan["conflicts"] and plan["cloud_only"][0]["resolved"] == "prod"


def test_cloud_only_changes_are_kept_and_reported():
    a = _ev("a", "10.00")
    cloud = {**a, "points": "9.00"}
    late = _ev("late", "1.00")  # created in the cloud after the export
    plan = sync.plan_changes(_snap(EV, a), _with(EV, a), _with(EV, cloud, late), prefer=None)
    assert not plan["update"][EV] and not plan["delete"][EV] and not plan["conflicts"]
    assert {w["id"] for w in plan["cloud_only"]} == {"a", "late"}


def test_deleted_on_node_but_changed_in_cloud_is_a_conflict():
    a = _ev("a", "10.00")
    plan = sync.plan_changes(_snap(EV, a), _empty(), _with(EV, {**a, "points": "1.00"}), prefer=None)
    assert not plan["delete"][EV] and plan["conflicts"][0]["id"] == "a"


def test_immutable_revisions_never_update():
    rev = {"id": "r", "reason": "x"}
    plan = sync.plan_changes(_snap("evaluation_revisions", rev),
                             _with("evaluation_revisions", {**rev, "reason": "y"}),
                             _with("evaluation_revisions", rev), prefer="node")
    assert not plan["update"]["evaluation_revisions"]
    assert plan["conflicts"][0]["what"] == "fila inmutable distinta"


def test_lock_logo_and_template_never_count_as_changes():
    event = {"id": "e", "name": "Camporee", "status": "IN_PROGRESS", "offline_locked_at": "2026-11-19T10:00:00+00:00",
             "offline_lock_reason": "x", "brand_logo_url": "https://media.adventist.club/l.png", "template_of_id": "t"}
    node = {**event, "offline_locked_at": None, "offline_lock_reason": None,
            "brand_logo_url": "/nodo-media/events/l.png", "template_of_id": None}
    assert sync.fingerprint(event, "events") == sync.fingerprint(node, "events")
    plan = sync.plan_changes(_snap("events", event), _with("events", node), _with("events", event), prefer=None)
    assert plan["unchanged"]["events"] == 1
    closed = {**node, "status": "CLOSED"}
    plan = sync.plan_changes(_snap("events", event), _with("events", closed), _with("events", event), prefer=None)
    assert plan["update"]["events"] == [closed]


@pytest.mark.parametrize("dsn", [
    "postgresql://neondb_owner:S3cret-pw@ep-x.neon.tech/neondb?sslmode=require&channel_binding=require",
    "postgresql+asyncpg://nodo:S3cret-pw@db:5432/nodo?sslmode=disable",
])
def test_connection_strings_are_never_echoed(dsn):
    assert "S3cret-pw" not in sync.describe(dsn) and "neondb_owner" not in sync.describe(dsn)
    cleaned = sync.clean_dsn(dsn)
    assert cleaned.startswith("postgresql://") and "channel_binding" not in cleaned


def test_the_cli_refuses_the_same_database_on_both_sides(capsys):
    with pytest.raises(SystemExit):
        sync.main(["import", "--event", "00000000-0000-0000-0000-000000000001", "--actor-email", "a@example.com",
                   "--prod-url", "postgresql://u:pw@h/db", "--node-url", "postgresql://u:pw@h/db"])
    assert "misma base" in capsys.readouterr().err


def test_revisions_of_a_conflicting_evaluation_wait_with_it():
    a = _ev("a", "10.00")
    rev = {"id": "r1", "evaluation_id": "a", "revision": 1, "reason": "corrección"}
    node_rows, prod_rows = _with(EV, {**a, "points": "8.00", "revision": 2}), _with(EV, {**a, "points": "9.00"})
    node_rows["evaluation_revisions"] = [rev]
    plan = sync.plan_changes(_snap(EV, a), node_rows, prod_rows, prefer=None)
    assert not plan["insert"]["evaluation_revisions"]
    assert any(w["id"] == "r1" for w in plan["cloud_only"])
    plan = sync.plan_changes(_snap(EV, a), node_rows, prod_rows, prefer="node")
    assert plan["insert"]["evaluation_revisions"] == [rev]
