"""Login lockout on the event node (LOGIN_LOCKOUT_MODE=device) and the node operator's routes
(/api/v1/nodo/operador/*, NODO_OPERADOR_TOKEN). docs/RATE_LIMITS.md.

On the venue Wi-Fi a stranger must not be able to lock a judge out by typing wrong passwords for
the judge's e-mail: he only locks HIMSELF (that device, that account), while the per-account
ceiling still stops a brute force spread over many devices.
"""
import bcrypt
import pytest
from sqlalchemy import text

from app import security
from app.config import Settings, settings
from app.db import SessionLocal
from app.rate_limit import CLIENT_IP_HEADER, PROXY_AUTH_HEADER, limiter, reset_account_limits
from app.services import device_tracker
from tests.conftest import DEFAULT_PASSWORD, fetch_all, module_factory, requires_db

LOGIN = "/api/v1/auth/login"
OPERADOR = "/api/v1/nodo/operador"
SECRET = "proxy-shared-secret-for-tests-" + "s" * 16
TOKEN = "operador-token-for-tests-" + "t" * 24
factory = module_factory("nodoop")


def _phone(ip: str) -> dict[str, str]:
    """A phone on the venue Wi-Fi, through the node's Next (which names its real address)."""
    return {PROXY_AUTH_HEADER: SECRET, CLIENT_IP_HEADER: ip}


OP = {"X-Nodo-Operador": TOKEN}


@pytest.fixture
def node(monkeypatch):
    """The node's settings: device lockout, trusted client address, operator token, limits on."""
    monkeypatch.setattr(settings, "LOGIN_LOCKOUT_MODE", "device")
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", SECRET)
    monkeypatch.setattr(settings, "NODO_OPERADOR_TOKEN", TOKEN)
    monkeypatch.setattr(security, "_DUMMY_HASH", bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode())
    limiter.reset()
    reset_account_limits()
    device_tracker.reset()
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()
    reset_account_limits()
    device_tracker.reset()


@pytest.fixture
async def clean_audit(factory):
    yield
    async with SessionLocal() as db:
        await db.execute(
            text("DELETE FROM audit_log WHERE action IN ('AUTH_LOGIN_FAILED', 'AUTH_LOGIN_UNLOCK')"
                 " AND metadata_json->>'email' LIKE :like"),
            {"like": f"{factory.prefix}%"},
        )
        await db.commit()


async def _login(client, email: str, password: str, ip: str):
    return await client.post(LOGIN, json={"email": email, "password": password}, headers=_phone(ip))


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
def test_production_defaults_are_unchanged():
    parsed = Settings(DATABASE_URL="postgresql://x@127.0.0.1/x")
    assert parsed.LOGIN_LOCKOUT_MODE == "account"
    assert parsed.RATE_LIMIT_LOGIN_ACCOUNT == "10/15 minutes"
    assert parsed.nodo_operador_token is None
    odd = Settings(DATABASE_URL="postgresql://x@127.0.0.1/x", LOGIN_LOCKOUT_MODE="DEVICE ",
                   RATE_LIMIT_LOGIN_ACCOUNT_DEVICE="many", RATE_LIMIT_LOGIN_DEVICE="",
                   NODO_OPERADOR_TOKEN="short")
    assert odd.LOGIN_LOCKOUT_MODE == "device"
    assert odd.RATE_LIMIT_LOGIN_ACCOUNT_DEVICE == "5/5 minutes"
    assert odd.RATE_LIMIT_LOGIN_DEVICE == "20/5 minutes"
    assert odd.nodo_operador_token is None  # < 32 characters: ignored
    assert Settings(DATABASE_URL="postgresql://x@127.0.0.1/x", LOGIN_LOCKOUT_MODE="lol").LOGIN_LOCKOUT_MODE == "account"


# ----------------------------------------------------------------------------
# Device lockout
# ----------------------------------------------------------------------------
@requires_db
async def test_a_stranger_only_locks_himself_out(client, factory, node, clean_audit):
    judge = await factory.user("judge-a")
    for _ in range(5):  # RATE_LIMIT_LOGIN_ACCOUNT_DEVICE = 5/5 minutes
        assert (await _login(client, judge["email"], "Wrong-Pass1", "10.20.0.66")).status_code == 401
    limited = await _login(client, judge["email"], DEFAULT_PASSWORD, "10.20.0.66")  # even the right one
    assert limited.status_code == 429
    assert 0 < int(limited.headers["Retry-After"]) <= 5 * 60
    assert limited.json()["detail"].startswith("Demasiados intentos. Espera ")

    # The judge, on her own phone, is not affected.
    assert (await _login(client, judge["email"], DEFAULT_PASSWORD, "10.20.0.10")).status_code == 200

    # The same answer for an address nobody registered: a 429 never tells which accounts exist.
    ghost = factory.email("ghost")
    for _ in range(5):
        assert (await _login(client, ghost, "Wrong-Pass1", "10.20.0.67")).status_code == 401
    ghost_limited = await _login(client, ghost, "Wrong-Pass1", "10.20.0.67")
    assert ghost_limited.status_code == 429
    assert ghost_limited.json()["detail"] == limited.json()["detail"]


@requires_db
async def test_the_account_ceiling_still_stops_many_devices(client, factory, node, clean_audit):
    judge = await factory.user("judge-b")
    for i in range(10):  # RATE_LIMIT_LOGIN_ACCOUNT (10/15 minutes here; 60/15 minutes on the node)
        assert (await _login(client, judge["email"], "Wrong-Pass1", f"10.21.0.{i}")).status_code == 401
    limited = await _login(client, judge["email"], DEFAULT_PASSWORD, "10.21.0.200")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 5 * 60


@requires_db
async def test_one_device_scanning_many_accounts_is_stopped(client, factory, node, clean_audit):
    for i in range(20):  # RATE_LIMIT_LOGIN_DEVICE = 20/5 minutes (failures, any account)
        response = await _login(client, factory.email(f"scan{i}"), "x", "10.22.0.5")
        assert response.status_code == 401, (i, response.text)
    judge = await factory.user("judge-c")
    blocked = await _login(client, judge["email"], DEFAULT_PASSWORD, "10.22.0.5")
    assert blocked.status_code == 429
    assert 0 < int(blocked.headers["Retry-After"]) <= 5 * 60
    assert (await _login(client, judge["email"], DEFAULT_PASSWORD, "10.22.0.6")).status_code == 200


@requires_db
async def test_judges_sharing_one_address_never_lock_each_other(client, factory, node):
    """An access point with NAT: right passwords do not count against the address."""
    judges = [await factory.user(f"nat{i}") for i in range(25)]
    for judge in judges:
        response = await _login(client, judge["email"], DEFAULT_PASSWORD, "10.23.0.1")
        assert response.status_code == 200, response.text


@requires_db
async def test_the_right_password_clears_the_device_counter(client, factory, node, clean_audit):
    judge = await factory.user("judge-d")
    for _ in range(4):
        assert (await _login(client, judge["email"], "nope", "10.24.0.1")).status_code == 401
    assert (await _login(client, judge["email"], DEFAULT_PASSWORD, "10.24.0.1")).status_code == 200
    for _ in range(5):
        assert (await _login(client, judge["email"], "nope", "10.24.0.1")).status_code == 401
    assert (await _login(client, judge["email"], "nope", "10.24.0.1")).status_code == 429


@requires_db
async def test_the_device_is_the_trusted_client_address(client, factory, node, clean_audit):
    judge = await factory.user("judge-e")
    # Same socket / X-Forwarded-For (the node's Next), different phones: separate buckets.
    via = {"X-Forwarded-For": "172.30.40.20"}
    for _ in range(5):
        r = await client.post(LOGIN, json={"email": judge["email"], "password": "nope"},
                              headers={**_phone("10.25.0.1"), **via})
        assert r.status_code == 401
    r = await client.post(LOGIN, json={"email": judge["email"], "password": DEFAULT_PASSWORD},
                          headers={**_phone("10.25.0.2"), **via})
    assert r.status_code == 200
    # A phone naming an address without the secret is keyed by X-Forwarded-For, not a fresh bucket.
    for _ in range(5):
        r = await client.post(LOGIN, json={"email": judge["email"], "password": "nope"},
                              headers={CLIENT_IP_HEADER: "10.25.0.3", **via})
        assert r.status_code == 401
    spoofed = await client.post(LOGIN, json={"email": judge["email"], "password": "nope"},
                                headers={CLIENT_IP_HEADER: "10.25.0.4", **via})
    assert spoofed.status_code == 429


@requires_db
async def test_failed_logins_are_audited_on_the_node_only(client, factory, node, clean_audit, monkeypatch):
    judge = await factory.user("judge-f")
    await _login(client, judge["email"], "Secret-Typo-123", "10.26.0.1")
    await _login(client, factory.email("nobody-f"), "Secret-Typo-123", "10.26.0.1")
    rows = await fetch_all(
        "SELECT entity_id, metadata_json, host(ip_address) AS ip, user_id FROM audit_log"
        " WHERE action = 'AUTH_LOGIN_FAILED' AND metadata_json->>'email' LIKE :like ORDER BY created_at",
        like=f"{factory.prefix}-%-f@example.com",
    )
    assert [r["metadata_json"]["cuenta_existe"] for r in rows] == [True, False]
    assert rows[0]["entity_id"] == judge["id"] and rows[0]["ip"] == "10.26.0.1" and rows[0]["user_id"] is None
    assert all("Secret-Typo" not in str(r["metadata_json"]) for r in rows)

    monkeypatch.setattr(settings, "LOGIN_LOCKOUT_MODE", "account")  # production
    await _login(client, judge["email"], "Secret-Typo-123", "10.26.0.1")
    again = await fetch_all(
        "SELECT 1 FROM audit_log WHERE action = 'AUTH_LOGIN_FAILED' AND metadata_json->>'email' = :e",
        e=judge["email"],
    )
    assert len(again) == 1


@requires_db
async def test_production_mode_ignores_devices(client, factory, node, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_LOCKOUT_MODE", "account")
    judge = await factory.user("judge-g")
    for _ in range(10):  # all from one address: only the per-account limit applies
        assert (await _login(client, judge["email"], "nope", "10.27.0.1")).status_code == 401
    assert (await _login(client, judge["email"], DEFAULT_PASSWORD, "10.27.0.2")).status_code == 429


# ----------------------------------------------------------------------------
# Operator routes
# ----------------------------------------------------------------------------
@requires_db
@pytest.mark.parametrize("method,path", [
    ("POST", "/cuentas/desbloquear"), ("GET", "/cuentas/bloqueadas"), ("GET", "/dispositivos"),
])
async def test_operator_routes_need_the_token(client, node, monkeypatch, method, path):
    body = {"email": "x@example.com"} if method == "POST" else None
    assert (await client.request(method, OPERADOR + path, json=body)).status_code == 401
    wrong = await client.request(method, OPERADOR + path, json=body, headers={"X-Nodo-Operador": TOKEN + "x"})
    assert wrong.status_code == 403
    monkeypatch.setattr(settings, "NODO_OPERADOR_TOKEN", None)  # production
    assert (await client.request(method, OPERADOR + path, json=body, headers=OP)).status_code == 404
    monkeypatch.setattr(settings, "NODO_OPERADOR_TOKEN", "too-short")
    assert (await client.request(method, OPERADOR + path, json=body, headers={"X-Nodo-Operador": "too-short"})).status_code == 404


@requires_db
async def test_operator_lists_and_unlocks_an_account(client, factory, node, clean_audit):
    judge = await factory.user("judge-h")
    for _ in range(5):  # one device locked for this account
        await _login(client, judge["email"], "nope", "10.28.0.66")
    for i in range(5):  # and the global ceiling reached from other devices
        await _login(client, judge["email"].upper(), "nope", f"10.28.1.{i}")
    assert (await _login(client, judge["email"], DEFAULT_PASSWORD, "10.28.0.10")).status_code == 429

    listed = await client.get(OPERADOR + "/cuentas/bloqueadas", headers=OP)
    assert listed.status_code == 200
    body = listed.json()
    assert body["modo"] == "device"
    entry = next(c for c in body["cuentas"] if c["email"] == judge["email"])
    assert entry["bloqueo_global"] is True and entry["intentos"] == 10 and entry["limite"] == 10
    assert entry["hasta"]
    assert [d["ip"] for d in entry["dispositivos"]] == ["10.28.0.66"]
    assert entry["dispositivos"][0]["intentos"] == 5 and entry["dispositivos"][0]["hasta"]

    unlocked = await client.post(OPERADOR + "/cuentas/desbloquear", json={"email": " " + judge["email"].upper()},
                                 headers=OP)
    assert unlocked.status_code == 200, unlocked.text
    result = unlocked.json()
    assert result["email"] == judge["email"] and result["desbloqueada"] is True
    assert result["contadores_borrados"] == 8  # global + 7 devices with attempts
    assert "10.28.0.66" in result["dispositivos"]

    after = (await client.get(OPERADOR + "/cuentas/bloqueadas", headers=OP)).json()
    assert all(c["email"] != judge["email"] for c in after["cuentas"])
    assert (await _login(client, judge["email"], DEFAULT_PASSWORD, "10.28.0.66")).status_code == 200

    audit = await fetch_all(
        "SELECT entity_id, user_id, details, metadata_json FROM audit_log WHERE action = 'AUTH_LOGIN_UNLOCK'"
        " AND metadata_json->>'email' = :e", e=judge["email"],
    )
    assert len(audit) == 1
    assert audit[0]["entity_id"] == judge["id"] and audit[0]["user_id"] is None
    assert audit[0]["metadata_json"]["actor"] == "operador del nodo"
    assert "operador del nodo" in audit[0]["details"]


@requires_db
async def test_operator_sees_the_connected_devices(client, factory, node, clean_audit):
    judge = await factory.user("judge-i")
    assert (await _login(client, judge["email"], DEFAULT_PASSWORD, "10.29.0.1")).status_code == 200
    for i in range(4):
        await _login(client, factory.email(f"probe{i}"), "nope", "10.29.0.99")
    await _login(client, factory.email("probe0"), "nope", "10.29.0.99")
    await _login(client, factory.email("solo"), "nope", "10.29.0.98")

    response = await client.get(OPERADOR + "/dispositivos?minutos=30", headers=OP)
    assert response.status_code == 200
    body = response.json()
    assert body["minutos"] == 30
    devices = {d["ip"]: d for d in body["dispositivos"]}
    assert body["total"] == len(devices)

    ok = devices["10.29.0.1"]
    assert ok["cuenta"]["email"] == judge["email"] and ok["cuenta"]["nombre"] == factory.name("judge-i")
    assert ok["fallos_10min"] == 0 and ok["ultimo_fallo"] is None
    assert ok["peticiones"] >= 1 and ok["primera_vez"] and ok["ultima_vez"]

    scanner = devices["10.29.0.99"]
    assert scanner["cuenta"] is None
    assert scanner["fallos_10min"] == 5 and scanner["ultimo_fallo"]
    assert scanner["correos_distintos"] == 4 and scanner["sospechoso"] is True
    assert sorted(scanner["correos"]) == sorted(factory.email(f"probe{i}") for i in range(4))
    assert scanner["bloqueado_hasta"] is None

    quiet = devices["10.29.0.98"]
    assert quiet["correos_distintos"] == 1 and quiet["correos"] is None and quiet["sospechoso"] is False

    # The operator's own calls are not a phone's activity.
    assert all(not ip.startswith("127.") for ip in devices)
