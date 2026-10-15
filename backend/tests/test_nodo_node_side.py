"""The NODE side of the automatic mode against a real second database (030).

A fresh node database is built with scripts/db_bootstrap.sh (like the node's `migrate`
service). Proved: the bundle loads; the node keeps the pass HASHES (printed passes work) but
re-encrypts every pass with ITS OWN secret (production's secret is not needed on the node);
its own node row carries the scope; what the node gathers for the upload holds its captures and
the plain tokens; the cloud re-encrypts them with its secret after the upload; media URLs the
event shows are listed for the offline mirror.
"""
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio

from app.services import event_pass, nodo_crypto
from app.services import nodo_sync_core as core
from tests.conftest import RUN, TEST_DATABASE_URL, fetch_one, module_factory, requires_db
from tests.test_nodo_api import Node, _exec, _fast, hdr, make_event, w  # noqa: F401 (fixtures)

factory = module_factory("nodonode")

pytestmark = [requires_db, pytest.mark.skipif(shutil.which("psql") is None, reason="needs psql (db_bootstrap.sh)")]
NODE_SECRET = "secreto-propio-del-nodo-" + "y" * 16
BOOTSTRAP = Path(__file__).resolve().parent.parent / "scripts" / "db_bootstrap.sh"


@pytest_asyncio.fixture(scope="module")
async def node_db():
    parts = urlsplit(TEST_DATABASE_URL)
    name = f"{parts.path.lstrip('/')}_nodo_{RUN}"
    url = urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))
    subprocess.run(["bash", str(BOOTSTRAP), "--create", url], check=True, capture_output=True)
    yield url
    admin = await core.connect(urlunsplit((parts.scheme, parts.netloc, "/postgres", "", "")))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def test_bundle_loads_on_a_node_with_its_own_pass_secret(client, w, node_db):  # noqa: F811
    event = await make_event(client, w, "Nodo real")
    await _exec("UPDATE organizations SET logo_url = :u WHERE id = :id",
                u=f"https://media.adventist.club/clubs/{RUN}-a.webp", id=uuid.UUID(w["clubs"]["a"]["id"]))
    mac = Node("Mac de la zona Señal")
    await mac.prepare(client, event, hdr(w["coordinator"]), {"activity_ids": [event["acts"]["A"]], "check_in": True})
    bundle = mac.bundle
    assert f"https://media.adventist.club/clubs/{RUN}-a.webp" in core.media_urls(bundle)

    node = await core.connect(node_db)
    try:
        info = bundle["node"]
        local = {"id": info["id"], "label": info["label"], "scope": info["scope"],
                 "sign_key": nodo_crypto.b64(mac.sign_public), "import_key": bundle["import_key"],
                 "node_private_enc": nodo_crypto.box(NODE_SECRET, "nodo-sign-key", mac.sign_private)}
        result = await core.load_into_node(
            node, bundle, replace=False, local_logo=None, export_id=info["export_id"],
            pass_encrypt=lambda t: event_pass.encrypt(t, secret=NODE_SECRET), local_node=local)
        assert result == {"ok": 3, "missing": 0, "reencrypted": True}
        eid = uuid.UUID(event["id"])
        regs = await node.fetch("SELECT id::text, pass_token_hash, pass_token_enc FROM event_registrations"
                                " WHERE event_id = $1", eid)
        cloud = {r["id"]: r for r in bundle["event"]["event_registrations"]}
        for r in regs:
            assert r["pass_token_hash"] == cloud[r["id"]]["pass_token_hash"]  # printed passes keep working
            token = event_pass.decrypt(r["pass_token_enc"], secret=NODE_SECRET)
            assert token == bundle["passes"][r["id"]]
            assert event_pass.decrypt(r["pass_token_enc"]) is None  # the cloud's secret cannot open it
        me = await core.local_node(node, eid)
        assert me["label"] == "Mac de la zona Señal" and me["scope"]["activity_ids"] == [event["acts"]["A"]]
        assert (await node.fetchval("SELECT offline_locked_at FROM events WHERE id = $1", eid)) is None

        # a capture on the node, then what the node uploads
        now = datetime.now(timezone.utc)
        await node.execute(
            "INSERT INTO evaluations (id, registration_id, activity_id, inputs, points, rules_version, judge_id,"
            " idempotency_key, created_at, updated_at) VALUES ($1, $2, $3, '{\"points\": 140}', 140, 1, $4, $5,"
            " $6, $6)", uuid.uuid4(), uuid.UUID(event["regs"]["a"]), uuid.UUID(event["acts"]["A"]),
            uuid.UUID(w["judge"]["id"]), f"nodo-{uuid.uuid4().hex}", now)
        changes = await core.gather_node_changes(
            node, eid, pass_decrypt=lambda v: event_pass.decrypt(v, secret=NODE_SECRET))
        assert changes["node_id"] == info["id"] and changes["export_id"] == info["export_id"]
        assert changes["passes"] == bundle["passes"]
        assert len(changes["event"]["evaluations"]) == 1
    finally:
        await node.close()

    # upload it through the API: sealed + signed like the Mac does
    rep = await mac.upload(client, hdr(w["coordinator"]), body=mac.body(changes))
    assert rep["insert"] == {"evaluations": 1} and not rep["conflicts"] and rep["node_status"] == "FINAL"
    assert rep["lock_released"] is True
    # the cloud still opens every pass with ITS secret (nothing was re-encrypted with the node's)
    for rid, token in bundle["passes"].items():
        row = await fetch_one("SELECT pass_token_enc FROM event_registrations WHERE id = :i", i=uuid.UUID(rid))
        assert event_pass.decrypt(row["pass_token_enc"]) == token
