"""Bloque I §2: `migrations/import_event_roster.py` (padrón de clubes de Aventureros).

Spec: docs/superpowers/specs/2026-09-24-eventos.md §2.

The synthetic file `tests/data/event_roster_sample.csv` (`;`-separated, Spanish header
variants, `{P}` = this run's prefix) covers: a new club in an existing zone and church, a
new zone + church created once and reused by the next row, a director with an account
(appointed) and without one (invited), an existing club found by name + church with
accents/case changed (adventurers added to its ministries), an existing club that already
has that director, an existing club found by `codigo`, and the problem rows: missing and
invalid e-mail, the same director for two clubs, an exact repeated row, a church that lives
under another zone, a director who already leads another club, an unknown ministry.

Proved: the dry run writes nothing; `--commit` builds exactly the expected tree through
the services, audited with the actor; a second run creates nothing and duplicates no
invitation; e-mails only with `--send-emails` + `--commit`; the CLI writes the report
next to the input and the join URLs to a mode-600 file, and never prints a token.
"""
import csv
import os
import pathlib
import stat
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import TEST_DATABASE_URL, fetch_all, fetch_one, module_factory, requires_db

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "migrations"))
import import_event_roster as roster  # noqa: E402

pytestmark = requires_db
factory = module_factory("roster")

SAMPLE = BACKEND / "tests" / "data" / "event_roster_sample.csv"
SCRIPT = BACKEND / "migrations" / "import_event_roster.py"


async def _club_with(factory, label, parent, ministries=("adventurers",), code=None):
    club = await factory.org(label, "club", parent)
    async with SessionLocal() as db:
        ids = [
            (await db.execute(text("SELECT id FROM ministries WHERE slug = :s"), {"s": slug})).scalar_one()
            for slug in ministries
        ]
        await db.execute(
            text("UPDATE organizations SET ministry_id = :m, code = :code WHERE id = :id"),
            {"m": ids[0], "code": code, "id": uuid.UUID(club["id"])},
        )
        for ministry_id in ids:
            await db.execute(
                text("INSERT INTO organization_ministries (organization_id, ministry_id) VALUES (:o, :m)"),
                {"o": uuid.UUID(club["id"]), "m": ministry_id},
            )
        await db.commit()
    return club


async def _director(factory, label, club):
    user = await factory.user(label, "CLUB_DIRECTOR", club["id"])
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO club_memberships (id, user_id, club_id, role, status, source,"
                " started_at, created_at, updated_at) VALUES (gen_random_uuid(), :u, :c,"
                " 'CLUB_DIRECTOR', 'ACTIVE', 'ADMIN', now(), now(), now())"
            ),
            {"u": uuid.UUID(user["id"]), "c": uuid.UUID(club["id"])},
        )
        await db.commit()
    return user


@pytest_asyncio.fixture(scope="module")
async def world(factory, tmp_path_factory):
    division = await factory.org("div", "division")
    union = await factory.org("uni", "union", division)
    assoc = await factory.org("asoc", "association", union)
    other_assoc = await factory.org("otra-asoc", "association", union)
    norte = await factory.org("Zona Norte", "zone", assoc)
    sur = await factory.org("Zona Sur", "zone", assoc)
    central = await factory.org("Central", "church", norte)
    await factory.org("Sur Uno", "church", sur)
    existente = await _club_with(factory, "Club Existente", central, ministries=("pathfinders",))
    con_director = await _club_with(factory, "Club Con Director", central)
    ocupado = await _club_with(factory, "Club Ocupado", central)
    por_codigo = await _club_with(factory, "Club Codigo", central, code=f"{factory.prefix}-COD")
    d1 = await _director(factory, "d1", con_director)
    d2 = await _director(factory, "d2", ocupado)
    con_cuenta = await factory.user("con-cuenta", "STUDENT")
    master = await factory.user("master", "MASTER_GC")
    assoc_admin = await factory.user("asoc-admin", "ADMIN_ASSOCIATION", assoc["id"])
    other_admin = await factory.user("otro-admin", "ADMIN_ASSOCIATION", other_assoc["id"])

    folder = tmp_path_factory.mktemp("roster")
    source = folder / "padron.csv"
    source.write_text(SAMPLE.read_text(encoding="utf-8").replace("{P}", factory.prefix), encoding="utf-8")
    return {
        "assoc": assoc,
        "existente": existente,
        "con_director": con_director,
        "por_codigo": por_codigo,
        "d1": d1,
        "d2": d2,
        "con_cuenta": con_cuenta,
        "master": master,
        "assoc_admin": assoc_admin,
        "other_admin": other_admin,
        "source": source,
        "prefix": factory.prefix,
    }


def _rows(source):
    headers, body = roster.read_table(source)
    mapping, _ = roster.map_columns(headers)
    rows = roster.parse_rows(body, mapping)
    roster.validate_rows(rows)
    return rows


async def _run(world, *, commit, actor="master", send_emails=False, send=None, rows=None):
    async with SessionLocal() as db:
        return await roster.run(
            db,
            rows if rows is not None else _rows(world["source"]),
            association_ref=world["assoc"]["id"],
            actor_email=world[actor]["email"],
            commit=commit,
            send_emails=send_emails,
            send=send,
        )


def _by_line(result):
    return {row.line: row for row in result.rows}


async def _counts(world) -> dict:
    like = world["prefix"] + "%"
    row = await fetch_one(
        "SELECT (SELECT count(*) FROM organizations WHERE name LIKE :like) AS orgs,"
        " (SELECT count(*) FROM org_invitations WHERE email LIKE :like) AS invitations,"
        " (SELECT count(*) FROM club_memberships m JOIN organizations o ON o.id = m.club_id"
        "   WHERE o.name LIKE :like) AS memberships,"
        " (SELECT count(*) FROM organization_ministries om JOIN organizations o"
        "   ON o.id = om.organization_id WHERE o.name LIKE :like) AS ministries,"
        " (SELECT count(*) FROM audit_log WHERE user_id = :actor) AS audits,"
        " (SELECT count(*) FROM notification_log WHERE email LIKE :like) AS mails",
        like=like,
        actor=uuid.UUID(world["master"]["id"]),
    )
    return dict(row)


# ----------------------------------------------------------------------------
# The file
# ----------------------------------------------------------------------------
def test_header_mapping_is_accent_and_synonym_tolerant(world):
    headers, body = roster.read_table(world["source"])
    mapping, lines = roster.map_columns(headers)
    named = {field: headers[index] for field, index in mapping.items()}
    assert named == {
        "club": "Nombre del Club",
        "iglesia": "Iglesia",
        "zona": "Distrito",
        "ciudad": "Ciudad",
        "director": "Director(a)",
        "correo_director": "Correo del Director",
        "telefono": "Teléfono",
        "codigo": "Código",
        "ministerio": "Ministerio",
    }
    assert any("Observaciones" in line for line in lines)  # unused columns are reported
    assert len(body) == 14 and body[0][0] == 2  # file line numbers


def test_map_override_and_missing_required_column():
    headers = ["Club", "Templo", "Zona", "E-mail Dir."]
    with pytest.raises(SystemExit) as refused:
        roster.map_columns(headers)
    assert "iglesia" in str(refused.value) and "correo_director" in str(refused.value)
    mapping, _ = roster.map_columns(headers, {"iglesia": "templo", "correo_director": "E-MAIL DIR"})
    assert mapping == {"club": 0, "iglesia": 1, "zona": 2, "correo_director": 3}


def test_file_level_problems(world):
    rows = {row.line: row for row in _rows(world["source"])}
    assert rows[2].problems == [] and rows[2].ministries == ["adventurers"]
    assert rows[3].ministries == ["adventurers", "pathfinders"]
    assert rows[3].email.endswith("@example.com")  # lower-cased
    assert rows[7].problems == ["missing_correo_director"]
    assert rows[8].problems == ["invalid_email"]
    assert rows[9].problems == ["same_director_for_several_clubs(lines 9, 10)"]
    assert rows[10].problems == rows[9].problems
    assert rows[11].problems == ["duplicate_row(line 2)"] and rows[11].status == "skipped"
    assert rows[15].problems == ["unknown_ministry:ninjas"]


def test_xlsx_without_openpyxl_is_refused_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "xlsx_available", lambda: False)
    path = tmp_path / "padron.xlsx"
    path.write_bytes(b"PK")
    with pytest.raises(SystemExit) as refused:
        roster.read_table(path)
    assert "openpyxl" in str(refused.value) and "CSV" in str(refused.value)


# ----------------------------------------------------------------------------
# Against the database
# ----------------------------------------------------------------------------
async def test_actor_must_administer_the_association(world):
    with pytest.raises(SystemExit) as refused:
        await _run(world, commit=False, actor="other_admin")
    assert "no administra" in str(refused.value)
    with pytest.raises(SystemExit):
        await _run(world, commit=False, send_emails=True)  # --send-emails needs --commit


async def test_dry_run_reports_everything_and_writes_nothing(world):
    before = await _counts(world)
    result = await _run(world, commit=False)
    assert await _counts(world) == before
    rows = _by_line(result)

    assert (rows[2].status, rows[2].club_action, rows[2].zone_action, rows[2].church_action,
            rows[2].director_account, rows[2].director_action) == (
        "ok", "create", "reuse", "reuse", "no", "invite")
    assert (rows[3].club_action, rows[3].zone_action, rows[3].church_action,
            rows[3].director_account, rows[3].director_action) == (
        "create", "create", "create", "yes", "appoint")
    # The zone row 3 creates is reused by row 4 (simulated, but seen).
    assert (rows[4].zone_action, rows[4].church_action, rows[4].director_action) == (
        "reuse", "create", "invite")
    assert (rows[5].club_action, rows[5].ministry_action, rows[5].director_action) == (
        "exists", "add adventurers", "invite")
    assert rows[5].club_id == world["existente"]["id"]
    assert rows[2].club_id == "" and rows[2].invitation_id == ""  # simulated ids are not reported
    assert (rows[6].club_action, rows[6].director_action) == ("exists", "already_director")
    assert rows[12].status == "error" and rows[12].problems[0].startswith("church_in_other_zone")
    assert rows[13].status == "error" and rows[13].problems == ["director_has_club"]
    assert (rows[14].club_id, rows[14].ministry_action, rows[14].director_action) == (
        world["por_codigo"]["id"], "ok", "invite")
    assert any("código" in note for note in rows[14].notes)
    assert {r.line for r in result.rows if r.status == "ok"} == {2, 3, 4, 5, 6, 14}
    assert [name.split(" ", 1)[1] for name in result.zones_created] == ["Zona Oriente"]
    assert len(result.churches_created) == 2
    assert len(result.invitations) == 4  # rows 2, 4, 5, 14


async def test_commit_builds_the_tree_then_a_rerun_changes_nothing(world):
    sent = []

    async def never(invitation, actor):  # pragma: no cover — must not be called
        sent.append(invitation)
        return True

    result = await _run(world, commit=True, send=never)
    assert sent == []
    rows = _by_line(result)
    assert {r.line for r in result.rows if r.status == "ok"} == {2, 3, 4, 5, 6, 14}

    new_clubs = await fetch_all(
        "SELECT o.id::text, o.name, o.status, o.code, p.name AS church, g.name AS zone,"
        " array(SELECT m.slug FROM organization_ministries om JOIN ministries m"
        "   ON m.id = om.ministry_id WHERE om.organization_id = o.id ORDER BY om.created_at) AS ministries"
        " FROM organizations o JOIN organizations p ON p.id = o.parent_id"
        " JOIN organizations g ON g.id = p.parent_id WHERE o.id = ANY(:ids)",
        ids=[uuid.UUID(rows[n].club_id) for n in (2, 3, 4)],
    )
    by_id = {row["id"]: row for row in new_clubs}
    a, b, c = (by_id[rows[n].club_id] for n in (2, 3, 4))
    assert a["status"] == "active" and a["ministries"] == ["adventurers"]
    assert a["church"].endswith("Central") and a["zone"].endswith("Zona Norte")
    assert b["ministries"] == ["adventurers", "pathfinders"] and b["zone"].endswith("Zona Oriente")
    assert c["code"].endswith("-C01") and c["zone"] == b["zone"]
    zones = await fetch_all(
        "SELECT id FROM organizations WHERE type = 'zone' AND name = :n",
        n=b["zone"],
    )
    assert len(zones) == 1  # created once, reused by the next row

    # Director with an account: appointed, principal role and club follow.
    director = await fetch_one(
        "SELECT u.role, u.organization_id::text AS org, m.status FROM users u"
        " JOIN club_memberships m ON m.user_id = u.id AND m.role = 'CLUB_DIRECTOR'"
        " WHERE u.id = :id",
        id=uuid.UUID(world["con_cuenta"]["id"]),
    )
    assert dict(director) == {"role": "CLUB_DIRECTOR", "org": rows[3].club_id, "status": "ACTIVE"}

    # Without an account: a pending CLUB_DIRECTOR invitation on the club, signed by the actor.
    invitations = await fetch_all(
        "SELECT email, organization_id::text AS org, role, created_by_id::text AS by"
        " FROM org_invitations WHERE email LIKE :like ORDER BY email",
        like=f"{world["prefix"]}%",
    )
    assert {(i["email"].split("-roster-")[1], i["org"]) for i in invitations} == {
        ("nuevo-a@example.com", rows[2].club_id),
        ("nuevo-c@example.com", rows[4].club_id),
        ("nuevo-d@example.com", world["existente"]["id"]),
        ("por-codigo@example.com", world["por_codigo"]["id"]),
    }
    assert {(i["role"], i["by"]) for i in invitations} == {("CLUB_DIRECTOR", world["master"]["id"])}

    # The existing club kept its place and its principal ministry, and gained adventurers.
    existing = await fetch_one(
        "SELECT o.parent_id::text AS parent, m.slug AS principal,"
        " array(SELECT m2.slug FROM organization_ministries om JOIN ministries m2"
        "   ON m2.id = om.ministry_id WHERE om.organization_id = o.id ORDER BY om.created_at) AS all"
        " FROM organizations o JOIN ministries m ON m.id = o.ministry_id WHERE o.id = :id",
        id=uuid.UUID(world["existente"]["id"]),
    )
    assert existing["principal"] == "pathfinders"
    assert existing["all"] == ["pathfinders", "adventurers"]

    # Audited with the actor; no e-mail was queued or logged.
    actions = {
        row["action"]
        for row in await fetch_all(
            "SELECT action FROM audit_log WHERE user_id = :u", u=uuid.UUID(world["master"]["id"])
        )
    }
    assert {"CREATE", "UPDATE", "ORG_INVITATION_CREATE", "CLUB_DIRECTOR_ASSIGN"} <= actions
    assert (await _counts(world))["mails"] == 0

    # Nothing of the problem rows exists.
    for line in (7, 8, 9, 10, 12, 13, 15):
        assert await fetch_one(
            "SELECT id FROM organizations WHERE lower(name) = lower(:n)", n=rows[line].club
        ) is None, line

    # Second run: finds everything, creates nothing, duplicates no invitation.
    before = await _counts(world)
    again = _by_line(await _run(world, commit=True, send=never))
    assert await _counts(world) == before
    assert {n: (again[n].club_action, again[n].director_action) for n in (2, 3, 4, 5, 6, 14)} == {
        2: ("exists", "invitation_pending"),
        3: ("exists", "already_director"),
        4: ("exists", "invitation_pending"),
        5: ("exists", "invitation_pending"),
        6: ("exists", "already_director"),
        14: ("exists", "invitation_pending"),
    }
    assert again[5].ministry_action == "ok"
    assert sent == []


async def test_emails_only_with_send_emails_and_commit(world, factory):
    source = world["source"].with_name("uno.csv")
    source.write_text(
        "club,iglesia,zona,ciudad,director,correo_director\n"
        f"{factory.prefix} Club Correo,{factory.prefix} Central,{factory.prefix} Zona Norte,"
        f"Reynosa,Eva,{factory.prefix}-eva@example.com\n",
        encoding="utf-8",
    )
    sent = []

    async def fake_send(invitation, actor):
        sent.append((invitation.email, invitation.club_name, bool(invitation.token)))
        return True

    result = await _run(world, commit=True, send_emails=True, send=fake_send, rows=_rows(source))
    assert result.emails_sent == 1 and sent == [
        (f"{factory.prefix}-eva@example.com", f"{factory.prefix} Club Correo", True)
    ]
    log = await fetch_one(
        "SELECT kind, entity_id FROM notification_log WHERE email = :e",
        e=f"{factory.prefix}-eva@example.com",
    )
    assert dict(log) == {"kind": "ORG_INVITATION", "entity_id": result.rows[0].invitation_id}


def _cli(*args):
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    env.pop("RESEND_API_KEY", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True, text=True, env=env, cwd=BACKEND, timeout=120,
    )


async def test_cli_dry_run_and_commit_files(world, factory):
    source = world["source"].with_name("cli.csv")
    source.write_text(
        "Club;Iglesia;Zona;Ciudad;Director;Correo\n"
        f"{factory.prefix} Club Cli;{factory.prefix} Central;{factory.prefix} Zona Norte;Reynosa;Fer;"
        f"{factory.prefix}-fer@example.com\n",
        encoding="utf-8",
    )
    common = (source, "--association", world["assoc"]["id"], "--actor-email", world["assoc_admin"]["email"])

    refused = _cli(*common, "--send-emails")
    assert refused.returncode != 0 and "--commit" in refused.stderr

    dry = _cli(*common)
    assert dry.returncode == 0, dry.stderr + dry.stdout
    assert "SIMULACRO" in dry.stdout and "correo_director  <- 'Correo'" in dry.stdout
    report = list(csv.DictReader(source.with_name("cli.roster-dry-run.csv").open(encoding="utf-8")))
    assert [(r["status"], r["club_action"], r["director_action"]) for r in report] == [
        ("ok", "create", "invite")
    ]
    assert await fetch_one("SELECT id FROM org_invitations WHERE email = :e", e=f"{factory.prefix}-fer@example.com") is None
    assert not list(source.parent.glob("cli.join-urls-*"))

    done = _cli(*common, "--commit")
    assert done.returncode == 0, done.stderr + done.stdout
    [urls] = list(source.parent.glob("cli.join-urls-*.SENSITIVE.csv"))
    assert stat.S_IMODE(urls.stat().st_mode) == 0o600
    [line] = list(csv.DictReader(urls.open(encoding="utf-8")))
    token = line["join_url"].split("t=", 1)[1]
    assert len(token) > 20
    assert token not in done.stdout and token not in done.stderr
    assert "SENSIBLE" in done.stdout

    # The token in the file is the live one: its hash is the stored invitation.
    from app.security import sha256_hex

    row = await fetch_one(
        "SELECT o.name FROM org_invitations i JOIN organizations o ON o.id = i.organization_id"
        " WHERE i.token_hash = :h AND i.accepted_at IS NULL",
        h=sha256_hex(token),
    )
    assert row["name"] == f"{factory.prefix} Club Cli"

    # Re-running writes no new URL file (nothing new to invite).
    again = _cli(*common, "--commit")
    assert again.returncode == 0, again.stderr + again.stdout
    assert len(list(source.parent.glob("cli.join-urls-*"))) == 1


def test_xlsx_is_read_when_openpyxl_is_installed(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append([None])  # a blank first row: the header is the first non-empty one
    sheet.append(["Club", "Iglesia", "Zona", "Ciudad", "Director", "E-mail", "Celular", "Clave"])
    sheet.append(["X", "Igl", "Zona", "Reynosa", "Ana", "a@example.com", 8991112222, 1234.0])
    path = tmp_path / "padron.xlsx"
    book.save(path)
    headers, body = roster.read_table(path)
    mapping, _ = roster.map_columns(headers)
    [row] = roster.parse_rows(body, mapping)
    assert (row.line, row.telefono, row.codigo, row.correo_director) == (3, "8991112222", "1234", "a@example.com")
