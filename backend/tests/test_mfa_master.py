"""Bloque E, incremento E1 — 2FA obligatorio para MASTER_GC.

Dos estados, los dos probados: el interruptor `MASTER_MFA_ENFORCED` apagado
(lo que hay hoy en producción, para poder desplegar antes de que el
responsable dé de alta su segundo factor) y encendido (la política de §5.8).
"""

import pyotp
import pytest
import pytest_asyncio

from app.config import settings
from app.services import email as email_service
from tests.conftest import (
    DEFAULT_PASSWORD,
    auth_headers,
    fetch_all,
    fetch_one,
    module_factory,
    requires_db,
)

pytestmark = requires_db
factory = module_factory("mfamaster")

AUTH = "/api/v1/auth"
USERS = "/api/v1/users"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def enforced(monkeypatch):
    """Turn the deploy switch on for one test."""
    monkeypatch.setattr(settings, "MASTER_MFA_ENFORCED", True)


@pytest.fixture
def security_emails(monkeypatch):
    """No test ever reaches the network: the send is asserted on, not performed."""
    sent = []

    async def capture_reset(to, name, reason):
        sent.append({"kind": "reset", "to": to, "reason": reason})
        return True

    async def capture_recovery(to, name):
        sent.append({"kind": "recovery_used", "to": to})
        return True

    monkeypatch.setattr(email_service, "send_mfa_reset_email", capture_reset)
    monkeypatch.setattr(email_service, "send_recovery_code_used_email", capture_recovery)
    return sent


async def _enrol(client, user: dict) -> pyotp.TOTP:
    """Complete the TOTP enrolment of an account and return its authenticator."""
    setup = await client.post(f"{AUTH}/mfa/setup", headers=user["headers"])
    assert setup.status_code == 200, setup.text
    totp = pyotp.TOTP(setup.json()["secret"])
    done = await client.post(
        f"{AUTH}/mfa/verify-setup", json={"totp_code": totp.now()}, headers=user["headers"]
    )
    assert done.status_code == 200, done.text
    user["recovery_codes"] = done.json()["recovery_codes"]
    return totp


async def _login_with_totp(client, factory, label: str, totp: pyotp.TOTP) -> dict:
    login = await client.post(
        f"{AUTH}/login", json={"email": factory.email(label), "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.json()["mfa_required"] is True
    verified = await client.post(
        f"{AUTH}/mfa/verify",
        json={"temp_token": login.json()["temp_token"], "totp_code": totp.now()},
    )
    assert verified.status_code == 200, verified.text
    return verified.json()


@pytest_asyncio.fixture(scope="module")
async def master(factory):
    return await factory.user("owner", role="MASTER_GC")


# ----------------------------------------------------------------------------
# The switch off: exactly today's behaviour
# ----------------------------------------------------------------------------
async def test_switch_off_master_without_mfa_operates_normally(client, master):
    """The deploy happens first; enforcement is turned on once the owner enrols."""
    assert settings.MASTER_MFA_ENFORCED is False
    assert (await client.get(f"{AUTH}/me", headers=master["headers"])).status_code == 200
    listing = await client.get(USERS, headers=master["headers"])
    assert listing.status_code == 200


# ----------------------------------------------------------------------------
# The switch on
# ----------------------------------------------------------------------------
async def test_enforced_master_without_mfa_can_only_enrol(client, master, enforced):
    blocked = await client.get(USERS, headers=master["headers"])
    assert blocked.status_code == 403
    assert "verificación en dos pasos" in blocked.json()["detail"]

    # Everything needed to enrol, and nothing else.
    assert (await client.get(f"{AUTH}/me", headers=master["headers"])).status_code == 200
    assert (await client.get(f"{USERS}/me", headers=master["headers"])).status_code == 200
    setup = await client.post(f"{AUTH}/mfa/setup", headers=master["headers"])
    assert setup.status_code == 200, setup.text
    bad_code = await client.post(
        f"{AUTH}/mfa/verify-setup", json={"totp_code": "000000"}, headers=master["headers"]
    )
    assert bad_code.status_code == 400  # reached the endpoint, the policy did not block it


async def test_enforced_other_roles_are_untouched(client, factory, enforced):
    director = await factory.user("director", role="CLUB_DIRECTOR")
    assert (await client.get(f"{USERS}/me", headers=director["headers"])).status_code == 200


async def test_enforced_login_tells_the_client_to_enrol(client, factory):
    fresh = await factory.user("nudge", role="MASTER_GC")
    login = await client.post(
        f"{AUTH}/login", json={"email": fresh["email"], "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.json()["mfa_enrollment_required"] is True


async def test_enforced_token_without_the_claim_is_refused(client, factory, enforced):
    enrolled = await factory.user("claim", role="MASTER_GC")
    totp = await _enrol(client, enrolled)

    # A token minted before the second factor (the one the fixture carries).
    stale = await client.get(USERS, headers=auth_headers(enrolled["id"]))
    assert stale.status_code == 401
    assert "Vuelve a iniciar sesión" in stale.json()["detail"]
    # The enrolment endpoints stay open: they only ever show your own account.
    assert (await client.get(f"{USERS}/me", headers=auth_headers(enrolled["id"]))).status_code == 200

    tokens = await _login_with_totp(client, factory, "claim", totp)
    assert (await client.get(USERS, headers=_bearer(tokens["access_token"]))).status_code == 200

    # refresh keeps the claim, otherwise the session would die after 30 minutes.
    refreshed = await client.post(
        f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    again = await client.get(USERS, headers=_bearer(refreshed.json()["access_token"]))
    assert again.status_code == 200


# ----------------------------------------------------------------------------
# Recovery codes
# ----------------------------------------------------------------------------
async def test_enrolment_returns_ten_codes_stored_only_as_hashes(client, factory):
    person = await factory.user("codes", role="MASTER_GC")
    await _enrol(client, person)
    codes = person["recovery_codes"]

    assert len(codes) == 10 and len(set(codes)) == 10
    for code in codes:
        left, _, right = code.partition("-")
        assert len(left) == 5 and len(right) == 5

    rows = await fetch_all(
        "SELECT code_hash, used_at FROM mfa_recovery_codes WHERE user_id = :id", id=person["id"]
    )
    assert len(rows) == 10
    assert all(row["used_at"] is None and len(row["code_hash"]) == 64 for row in rows)
    # The plain code exists nowhere in the database.
    stored = {row["code_hash"] for row in rows}
    assert not stored.intersection(codes)


async def test_recovery_code_logs_in_once_and_warns_the_owner(
    client, factory, security_emails, enforced
):
    person = await factory.user("recovery", role="MASTER_GC")
    await _enrol(client, person)
    code = person["recovery_codes"][0]

    login = await client.post(
        f"{AUTH}/login", json={"email": person["email"], "password": DEFAULT_PASSWORD}
    )
    temp_token = login.json()["temp_token"]
    used = await client.post(
        f"{AUTH}/mfa/verify", json={"temp_token": temp_token, "recovery_code": code}
    )
    assert used.status_code == 200, used.text
    # It counts as a second factor: the token it mints opens everything.
    assert (
        await client.get(USERS, headers=_bearer(used.json()["access_token"]))
    ).status_code == 200
    assert [mail["kind"] for mail in security_emails] == ["recovery_used"]
    assert security_emails[0]["to"] == person["email"]

    row = await fetch_one(
        "SELECT count(*) AS n FROM mfa_recovery_codes WHERE user_id = :id AND used_at IS NOT NULL",
        id=person["id"],
    )
    assert row["n"] == 1

    # Single use, and a code that never existed answers the same way.
    again = await client.post(
        f"{AUTH}/login", json={"email": person["email"], "password": DEFAULT_PASSWORD}
    )
    replay = await client.post(
        f"{AUTH}/mfa/verify",
        json={"temp_token": again.json()["temp_token"], "recovery_code": code},
    )
    assert replay.status_code == 401
    unknown = await client.post(
        f"{AUTH}/mfa/verify",
        json={"temp_token": again.json()["temp_token"], "recovery_code": "AAAAA-BBBBB"},
    )
    assert unknown.status_code == 401


async def test_regenerating_codes_replaces_the_unused_ones(client, factory):
    person = await factory.user("regen", role="MASTER_GC")
    totp = await _enrol(client, person)
    tokens = await _login_with_totp(client, factory, "regen", totp)
    headers = _bearer(tokens["access_token"])

    stale = await client.post(
        f"{AUTH}/mfa/recovery-codes", json={"totp_code": "000000"}, headers=headers
    )
    assert stale.status_code == 400

    regenerated = await client.post(
        f"{AUTH}/mfa/recovery-codes", json={"totp_code": totp.now()}, headers=headers
    )
    assert regenerated.status_code == 200, regenerated.text
    fresh = regenerated.json()["recovery_codes"]
    assert len(fresh) == 10 and not set(fresh).intersection(person["recovery_codes"])

    live = await fetch_one(
        "SELECT count(*) AS n FROM mfa_recovery_codes WHERE user_id = :id AND used_at IS NULL",
        id=person["id"],
    )
    assert live["n"] == 10
    audited = await fetch_one(
        "SELECT count(*) AS n FROM audit_log WHERE action = 'MFA_RECOVERY_REGENERATE'"
        " AND entity_id = :id",
        id=person["id"],
    )
    assert audited["n"] == 1


# ----------------------------------------------------------------------------
# Peer reset: only another MASTER_GC, never on oneself
# ----------------------------------------------------------------------------
async def test_mfa_reset_only_by_another_master(client, factory, security_emails):
    locked_out = await factory.user("locked", role="MASTER_GC")
    await _enrol(client, locked_out)
    peer = await factory.user("peer", role="MASTER_GC")
    admin = await factory.user("assoc-admin", role="ADMIN_ASSOCIATION")

    body = {"reason": "Perdió el teléfono y ya usó todos sus códigos"}
    refused = await client.post(
        f"{USERS}/{locked_out['id']}/mfa-reset", json=body, headers=admin["headers"]
    )
    assert refused.status_code == 403
    on_self = await client.post(
        f"{USERS}/{peer['id']}/mfa-reset", json=body, headers=peer["headers"]
    )
    assert on_self.status_code == 403

    done = await client.post(
        f"{USERS}/{locked_out['id']}/mfa-reset", json=body, headers=peer["headers"]
    )
    assert done.status_code == 200, done.text

    row = await fetch_one(
        "SELECT mfa_enabled, mfa_secret FROM users WHERE id = :id", id=locked_out["id"]
    )
    assert row["mfa_enabled"] is False and row["mfa_secret"] is None
    codes = await fetch_one(
        "SELECT count(*) AS n FROM mfa_recovery_codes WHERE user_id = :id AND used_at IS NULL",
        id=locked_out["id"],
    )
    assert codes["n"] == 0
    assert [mail["kind"] for mail in security_emails] == ["reset"]
    assert security_emails[0]["to"] == locked_out["email"]

    audited = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE action = 'MFA_RESET' AND entity_id = :id",
        id=locked_out["id"],
    )
    assert audited is not None and audited["metadata_json"]["reason"] == body["reason"]


async def test_disabling_mfa_drops_the_recovery_codes(client, factory):
    person = await factory.user("disable", role="INSTRUCTOR")
    totp = await _enrol(client, person)
    tokens = await _login_with_totp(client, factory, "disable", totp)

    off = await client.post(f"{AUTH}/mfa/disable", headers=_bearer(tokens["access_token"]))
    assert off.status_code == 200, off.text
    left = await fetch_one(
        "SELECT count(*) AS n FROM mfa_recovery_codes WHERE user_id = :id", id=person["id"]
    )
    assert left["n"] == 0
