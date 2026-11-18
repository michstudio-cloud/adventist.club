"""Passkeys (WebAuthn, migration 032; docs/PASSKEYS.md).

The phone is `tests/soft_authenticator.py`: a P-256 key that answers the ceremonies like a
synced passkey does. What is under test is everything the server must refuse (another origin,
another RP ID, a reused or expired challenge, no user verification, a counter that went back,
another account's passkey) and how a passkey counts for the MFA policy of §5.8.
"""
import time
import uuid

import pyotp
import pytest
import pytest_asyncio

from app.config import settings
from app.security import create_access_token, decode_claims, TOKEN_ACCESS
from app.services import email as email_service
from app.services import mfa as mfa_service
from app.services import nodo_sync_core
from app.services import passkeys as passkey_service
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DEFAULT_PASSWORD, fetch_all, fetch_one, module_factory, requires_db
from tests.soft_authenticator import SoftAuthenticator, b64url

pytestmark = requires_db
factory = module_factory("passkeys")

AUTH = "/api/v1/auth"
PASSKEYS = f"{AUTH}/passkeys"
USERS = "/api/v1/users"
RP_ID = "adventist.club"
ORIGIN = "https://adventist.club"
EVENTS_ORIGIN = "https://events.adventist.club"
ORIGINS = f"{ORIGIN},{EVENTS_ORIGIN},https://admin.adventist.club,https://conquistadores.app"


@pytest.fixture(autouse=True)
def passkeys_on(monkeypatch):
    monkeypatch.setattr(settings, "WEBAUTHN_RP_ID", RP_ID)
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGINS", ORIGINS)


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(settings, "MASTER_MFA_ENFORCED", True)


@pytest.fixture(autouse=True)
def security_emails(monkeypatch):
    """No test ever reaches the network: the send is asserted on, not performed."""
    sent = []

    async def added(to, name, passkey_name):
        sent.append({"kind": "added", "to": to, "passkey": passkey_name})
        return True

    async def removed(to, name, passkey_name):
        sent.append({"kind": "removed", "to": to, "passkey": passkey_name})
        return True

    async def other(*args, **kwargs):
        sent.append({"kind": "other", "to": args[0]})
        return True

    monkeypatch.setattr(email_service, "send_passkey_added_email", added)
    monkeypatch.setattr(email_service, "send_passkey_removed_email", removed)
    monkeypatch.setattr(email_service, "send_mfa_reset_email", other)
    monkeypatch.setattr(email_service, "send_recovery_code_used_email", other)
    return sent


async def execute(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _session(user: dict, *, mfa: bool = False, age: int = 0) -> dict:
    """Headers of a session of `user` that logged in `age` seconds ago."""
    token = create_access_token(uuid.UUID(user["id"]), mfa=mfa, auth_time=int(time.time()) - age)
    return _bearer(token)


async def _register(client, headers: dict, phone: SoftAuthenticator, *, name: str | None = "iPhone", origin: str = ORIGIN):
    options = await client.post(f"{PASSKEYS}/register/options", headers=headers)
    assert options.status_code == 200, options.text
    body = options.json()
    return await client.post(
        f"{PASSKEYS}/register/verify",
        json={
            "challenge_id": body["challenge_id"],
            "credential": phone.create(body["options"], origin=origin),
            "name": name,
        },
        headers=headers,
    )


async def _login_options(client, temp_token: str | None = None) -> dict:
    response = await client.post(
        f"{PASSKEYS}/login/options", json={"temp_token": temp_token} if temp_token else {}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _login(client, phone: SoftAuthenticator, *, temp_token: str | None = None, **lies):
    body = await _login_options(client, temp_token)
    lies.setdefault("origin", ORIGIN)
    return await client.post(
        f"{PASSKEYS}/login/verify",
        json={"challenge_id": body["challenge_id"], "credential": phone.get(body["options"], **lies)},
    )


async def _password_login(client, user: dict):
    response = await client.post(f"{AUTH}/login", json={"email": user["email"], "password": DEFAULT_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


async def _last_failure(user: dict) -> dict:
    row = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE action = 'PASSKEY_LOGIN_FAILED' AND entity_id = :id"
        " ORDER BY created_at DESC LIMIT 1",
        id=user["id"],
    )
    assert row is not None
    return row["metadata_json"]


# ----------------------------------------------------------------------------
# Switched off: exactly the API of before 032
# ----------------------------------------------------------------------------
async def test_without_configuration_the_routes_do_not_exist(client, factory, monkeypatch):
    monkeypatch.setattr(settings, "WEBAUTHN_RP_ID", None)
    user = await factory.user("off")
    assert (await client.get(f"{PASSKEYS}/config")).json() == {"enabled": False, "rp_id": None}
    assert (await client.post(f"{PASSKEYS}/register/options", headers=user["headers"])).status_code == 404
    assert (await client.get(PASSKEYS, headers=user["headers"])).status_code == 404
    assert (await client.post(f"{PASSKEYS}/login/options", json={})).status_code == 404
    assert (await client.post(
        f"{PASSKEYS}/login/verify", json={"challenge_id": str(uuid.uuid4()), "credential": {}}
    )).status_code == 404
    # An RP ID without allowed origins is not a configuration either.
    monkeypatch.setattr(settings, "WEBAUTHN_RP_ID", RP_ID)
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGINS", " ")
    assert (await client.get(f"{PASSKEYS}/config")).json() == {"enabled": False, "rp_id": None}


async def test_switched_off_a_registered_passkey_stops_counting(client, factory, monkeypatch):
    """The emergency switch: an obliged account is asked to enrol (TOTP) again, never let in."""
    owner = await factory.user("off-owner", role="MASTER_GC")
    assert (await _register(client, _session(owner), SoftAuthenticator())).status_code == 200
    monkeypatch.setattr(settings, "WEBAUTHN_RP_ID", None)
    login = await _password_login(client, owner)
    assert login["mfa_required"] is False and login["mfa_enrollment_required"] is True


# ----------------------------------------------------------------------------
# Register and sign in without a password
# ----------------------------------------------------------------------------
async def test_register_and_sign_in_without_password(client, factory, security_emails):
    user = await factory.user("ana")
    phone = SoftAuthenticator()
    assert (await client.get(f"{PASSKEYS}/config")).json() == {"enabled": True, "rp_id": RP_ID}

    options = (await client.post(f"{PASSKEYS}/register/options", headers=user["headers"])).json()["options"]
    assert options["rp"] == {"name": "Adventist.Club", "id": RP_ID}
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["attestation"] == "none"

    done = await _register(client, user["headers"], phone, name="  iPhone   de Ana ")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["passkey"]["name"] == "iPhone de Ana"
    assert body["passkey"]["provider"] == "iCloud" and body["passkey"]["synced"] is True
    # An ordinary account gets no recovery codes: its password still works on its own.
    assert body["recovery_codes"] is None and body["reauth_required"] is False
    assert security_emails == [{"kind": "added", "to": user["email"], "passkey": "iPhone de Ana"}]

    stored = await fetch_one("SELECT * FROM webauthn_credentials WHERE user_id = :id", id=uuid.UUID(user["id"]))
    assert bytes(stored["credential_id"]) == phone.credential_id
    assert stored["transports"] == ["internal", "hybrid"] and stored["backup_eligible"] is True
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE action = 'PASSKEY_REGISTER' AND entity_id = :id", id=user["id"]
    )
    assert audit["metadata_json"]["provider"] == "iCloud"

    listing = await client.get(PASSKEYS, headers=user["headers"])
    assert [p["name"] for p in listing.json()] == ["iPhone de Ana"]
    assert listing.json()[0]["last_used_at"] is None

    # Passwordless: no e-mail, no temp token; the passkey says whose it is.
    login_options = await _login_options(client)
    assert login_options["options"]["rpId"] == RP_ID
    assert login_options["options"]["userVerification"] == "required"
    assert not login_options["options"].get("allowCredentials")
    signed_in = await _login(client, phone, origin=EVENTS_ORIGIN)  # any allowed origin of the RP ID
    assert signed_in.status_code == 200, signed_in.text
    tokens = signed_in.json()
    # Born of a second factor: possession of the device + biometrics.
    assert decode_claims(tokens["access_token"], TOKEN_ACCESS)["mfa"] is True
    me = await client.get(f"{AUTH}/me", headers=_bearer(tokens["access_token"]))
    assert me.status_code == 200 and me.json()["email"] == user["email"]
    refreshed = await client.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert decode_claims(refreshed.json()["access_token"], TOKEN_ACCESS)["mfa"] is True
    assert (await client.get(PASSKEYS, headers=user["headers"])).json()[0]["last_used_at"] is not None


async def test_a_passkey_does_not_harden_an_ordinary_account(client, factory):
    """D2: for an account whose role does not oblige it, a passkey is a shortcut."""
    user = await factory.user("shortcut")
    assert (await _register(client, user["headers"], SoftAuthenticator())).status_code == 200
    login = await _password_login(client, user)
    assert login["mfa_required"] is False and login["access_token"] and login["mfa_methods"] == []
    assert login["mfa_enrollment_required"] is False


# ----------------------------------------------------------------------------
# What the server refuses at login
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def bea(factory):
    return await factory.user("bea")


@pytest_asyncio.fixture
async def bea_phone(client, bea, passkeys_on):
    # One passkey per test: Bea is shared by the module and an account holds at most ten.
    await execute("DELETE FROM webauthn_credentials WHERE user_id = :id", id=uuid.UUID(bea["id"]))
    phone = SoftAuthenticator()
    assert (await _register(client, bea["headers"], phone)).status_code == 200
    return phone


@pytest.mark.parametrize(
    "lies, reason",
    [
        ({"origin": "https://adventist.club.evil.example"}, "origin"),
        ({"origin": "http://adventist.club"}, "origin"),
        ({"rp_id": "evil.example"}, "rp_id"),
        ({"user_verified": False}, "user_verification"),
    ],
)
async def test_login_refuses_a_ceremony_from_elsewhere(client, bea, bea_phone, lies, reason):
    refused = await _login(client, bea_phone, **lies)
    assert refused.status_code == 401, refused.text
    assert refused.json()["detail"] == passkey_service.NOT_RECOGNIZED_DETAIL
    failure = await _last_failure(bea)
    assert failure["reason"] == reason and failure["email"] == bea["email"]


async def test_login_refuses_a_forged_signature(client, bea, bea_phone):
    impostor = SoftAuthenticator()
    impostor.credential_id = bea_phone.credential_id  # knows the id, not the private key
    refused = await _login(client, impostor)
    assert refused.status_code == 401
    assert (await _last_failure(bea))["reason"] == "signature"


async def test_login_refuses_a_passkey_that_names_another_account(client, bea, bea_phone):
    bea_phone.user_handle = uuid.uuid4().bytes
    refused = await _login(client, bea_phone)
    assert refused.status_code == 401
    assert (await _last_failure(bea))["reason"] == "user_handle"


async def test_an_oversized_answer_is_refused_before_it_is_parsed(client):
    body = await _login_options(client)
    huge = await client.post(
        f"{PASSKEYS}/login/verify",
        json={"challenge_id": body["challenge_id"], "credential": {"id": "A" * 40_000}},
    )
    assert huge.status_code == 422
    # ... and it did not even spend the challenge.
    row = await fetch_one("SELECT used_at FROM webauthn_challenges WHERE id = :id", id=uuid.UUID(body["challenge_id"]))
    assert row["used_at"] is None


async def test_login_refuses_an_unknown_passkey(client):
    refused = await _login(client, SoftAuthenticator())
    assert refused.status_code == 401
    assert refused.json()["detail"] == passkey_service.NOT_RECOGNIZED_DETAIL


async def test_a_challenge_is_single_use(client, bea, bea_phone):
    body = await _login_options(client)
    answer = {"challenge_id": body["challenge_id"], "credential": bea_phone.get(body["options"], origin=ORIGIN)}
    assert (await client.post(f"{PASSKEYS}/login/verify", json=answer)).status_code == 200
    replay = await client.post(f"{PASSKEYS}/login/verify", json=answer)
    assert replay.status_code == 400
    assert replay.json()["detail"] == passkey_service.CHALLENGE_EXPIRED_DETAIL


async def test_a_failed_verification_still_spends_the_challenge(client, bea, bea_phone):
    """Otherwise a refused answer could be retried against the same challenge."""
    body = await _login_options(client)
    bad = bea_phone.get(body["options"], origin="https://evil.example")
    assert (await client.post(
        f"{PASSKEYS}/login/verify", json={"challenge_id": body["challenge_id"], "credential": bad}
    )).status_code == 401
    good = bea_phone.get(body["options"], origin=ORIGIN)
    assert (await client.post(
        f"{PASSKEYS}/login/verify", json={"challenge_id": body["challenge_id"], "credential": good}
    )).status_code == 400


async def test_a_challenge_expires(client, bea, bea_phone):
    body = await _login_options(client)
    await execute(
        "UPDATE webauthn_challenges SET expires_at = now() - interval '1 second' WHERE id = :id",
        id=uuid.UUID(body["challenge_id"]),
    )
    late = await client.post(
        f"{PASSKEYS}/login/verify",
        json={"challenge_id": body["challenge_id"], "credential": bea_phone.get(body["options"], origin=ORIGIN)},
    )
    assert late.status_code == 400


async def test_a_register_challenge_does_not_open_a_session(client, bea, bea_phone):
    options = (await client.post(f"{PASSKEYS}/register/options", headers=bea["headers"])).json()
    crossed = await client.post(
        f"{PASSKEYS}/login/verify",
        json={"challenge_id": options["challenge_id"],
              "credential": bea_phone.get({"challenge": options["options"]["challenge"], "rpId": RP_ID}, origin=ORIGIN)},
    )
    assert crossed.status_code == 400


async def test_a_counter_that_goes_back_is_a_clone(client, factory):
    user = await factory.user("yubi")
    key = SoftAuthenticator(synced=False, aaguid=bytes(16))  # a device-bound security key
    done = await _register(client, user["headers"], key, name=None)
    assert done.status_code == 200, done.text
    assert done.json()["passkey"] == done.json()["passkey"] | {"name": "Passkey", "provider": None, "synced": False}
    assert (await _login(client, key)).status_code == 200
    assert (await _login(client, key)).status_code == 200
    stored = await fetch_one("SELECT sign_count FROM webauthn_credentials WHERE user_id = :id", id=uuid.UUID(user["id"]))
    assert stored["sign_count"] == 3
    cloned = await _login(client, key, sign_count=2)
    assert cloned.status_code == 401
    assert (await _last_failure(user))["reason"] == "counter"


async def test_an_inactive_account_does_not_sign_in(client, factory):
    user = await factory.user("gone")
    phone = SoftAuthenticator()
    assert (await _register(client, user["headers"], phone)).status_code == 200
    await execute("UPDATE users SET status = 'SUSPENDED' WHERE id = :id", id=uuid.UUID(user["id"]))
    refused = await _login(client, phone)
    assert refused.status_code == 403 and "SUSPENDED" in refused.json()["detail"]


# ----------------------------------------------------------------------------
# What the server refuses at registration
# ----------------------------------------------------------------------------
async def test_register_refuses_another_origin_and_audits_it(client, factory):
    user = await factory.user("reg-origin")
    refused = await _register(client, user["headers"], SoftAuthenticator(), origin="https://evil.example")
    assert refused.status_code == 400
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE action = 'PASSKEY_REGISTER_FAILED' AND entity_id = :id",
        id=user["id"],
    )
    assert audit["metadata_json"] == {"reason": "origin"}
    assert await fetch_all("SELECT 1 FROM webauthn_credentials WHERE user_id = :id", id=uuid.UUID(user["id"])) == []


async def test_register_refuses_another_accounts_challenge(client, factory):
    one, two = await factory.user("reg-one"), await factory.user("reg-two")
    options = (await client.post(f"{PASSKEYS}/register/options", headers=one["headers"])).json()
    stolen = await client.post(
        f"{PASSKEYS}/register/verify",
        json={"challenge_id": options["challenge_id"],
              "credential": SoftAuthenticator().create(options["options"], origin=ORIGIN)},
        headers=two["headers"],
    )
    assert stolen.status_code == 400
    assert stolen.json()["detail"] == passkey_service.CHALLENGE_EXPIRED_DETAIL


async def test_register_refuses_without_user_verification(client, factory):
    user = await factory.user("reg-uv")
    options = (await client.post(f"{PASSKEYS}/register/options", headers=user["headers"])).json()
    refused = await client.post(
        f"{PASSKEYS}/register/verify",
        json={"challenge_id": options["challenge_id"],
              "credential": SoftAuthenticator().create(options["options"], origin=ORIGIN, user_verified=False)},
        headers=user["headers"],
    )
    assert refused.status_code == 400


async def test_the_same_passkey_is_not_registered_twice(client, factory):
    one, two = await factory.user("dup-one"), await factory.user("dup-two")
    phone = SoftAuthenticator()
    assert (await _register(client, one["headers"], phone)).status_code == 200
    again = (await client.post(f"{PASSKEYS}/register/options", headers=one["headers"])).json()["options"]
    assert [c["type"] for c in again["excludeCredentials"]] == ["public-key"]
    duplicate = await _register(client, two["headers"], phone)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == passkey_service.ALREADY_REGISTERED_DETAIL


async def test_at_most_ten_passkeys(client, factory, monkeypatch):
    monkeypatch.setattr(passkey_service, "MAX_PASSKEYS", 2)
    user = await factory.user("many")
    for _ in range(2):
        assert (await _register(client, user["headers"], SoftAuthenticator())).status_code == 200
    full = await client.post(f"{PASSKEYS}/register/options", headers=user["headers"])
    assert full.status_code == 409


async def test_managing_passkeys_needs_a_recent_login(client, factory):
    user = await factory.user("stale")
    old = _session(user, age=16 * 60)
    refused = await client.post(f"{PASSKEYS}/register/options", headers=old)
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "passkey_reauth_required"
    assert refused.headers["x-reauth-required"] == "1"

    done = await _register(client, user["headers"], SoftAuthenticator())
    passkey_id = done.json()["passkey"]["id"]
    assert (await client.delete(f"{PASSKEYS}/{passkey_id}", headers=old)).status_code == 403
    # Renaming is cosmetic; listing is a read.
    assert (await client.patch(f"{PASSKEYS}/{passkey_id}", json={"name": "Viejo"}, headers=old)).status_code == 200
    assert (await client.get(PASSKEYS, headers=old)).json()[0]["name"] == "Viejo"


async def test_rename_and_delete_only_ones_own(client, factory, security_emails):
    owner, other = await factory.user("mine"), await factory.user("theirs")
    done = await _register(client, owner["headers"], SoftAuthenticator(), name="Mac")
    passkey_id = done.json()["passkey"]["id"]
    assert (await client.patch(f"{PASSKEYS}/{passkey_id}", json={"name": "x"}, headers=other["headers"])).status_code == 404
    assert (await client.delete(f"{PASSKEYS}/{passkey_id}", headers=other["headers"])).status_code == 404
    assert (await client.patch(f"{PASSKEYS}/{passkey_id}", json={"name": "   "}, headers=owner["headers"])).status_code == 422

    removed = await client.delete(f"{PASSKEYS}/{passkey_id}", headers=owner["headers"])
    assert removed.status_code == 200, removed.text
    assert (await client.get(PASSKEYS, headers=owner["headers"])).json() == []
    assert security_emails[-1] == {"kind": "removed", "to": owner["email"], "passkey": "Mac"}
    actions = [r["action"] for r in await fetch_all(
        "SELECT action FROM audit_log WHERE entity_id = :id ORDER BY created_at", id=owner["id"]
    )]
    assert actions == ["PASSKEY_REGISTER", "PASSKEY_DELETE"]


# ----------------------------------------------------------------------------
# The MFA policy (spec E §5.8): a passkey is the second factor of an obliged role
# ----------------------------------------------------------------------------
async def test_master_enrols_with_a_passkey_and_operates(client, factory, enforced):
    owner = await factory.user("owner", role="MASTER_GC")
    phone = SoftAuthenticator()

    # Enforcement on, no second factor: blocked everywhere but the enrolment.
    assert (await client.get(USERS, headers=owner["headers"])).status_code == 403
    assert (await _password_login(client, owner))["mfa_enrollment_required"] is True

    done = await _register(client, owner["headers"], phone)
    assert done.status_code == 200, done.text
    body = done.json()
    assert len(body["recovery_codes"]) == 10 and body["reauth_required"] is True
    enabled = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE action = 'MFA_ENABLE' AND entity_id = :id", id=owner["id"]
    )
    assert enabled["metadata_json"] == {"method": "passkey"}
    # TOTP is untouched: the column still means "authenticator app".
    row = await fetch_one("SELECT mfa_enabled, mfa_secret FROM users WHERE id = :id", id=uuid.UUID(owner["id"]))
    assert row["mfa_enabled"] is False and row["mfa_secret"] is None

    # The password-born session no longer complies: sign in again.
    stale = await client.get(USERS, headers=owner["headers"])
    assert stale.status_code == 401 and stale.json()["detail"] == mfa_service.REAUTH_REQUIRED_DETAIL

    # Passwordless: one step, and the session is born of the second factor.
    tokens = (await _login(client, phone)).json()
    assert (await client.get(USERS, headers=_bearer(tokens["access_token"]))).status_code == 200

    # With the password: it alone opens nothing; the passkey is the second step.
    login = await _password_login(client, owner)
    assert login["mfa_required"] is True and login["access_token"] == ""
    assert login["mfa_methods"] == ["passkey", "recovery"] and login["mfa_enrollment_required"] is False
    step = await _login_options(client, login["temp_token"])
    assert [c["id"] for c in step["options"]["allowCredentials"]] == [b64url(phone.credential_id)]
    second = await client.post(
        f"{PASSKEYS}/login/verify",
        json={"challenge_id": step["challenge_id"], "credential": phone.get(step["options"], origin=ORIGIN)},
    )
    assert second.status_code == 200, second.text
    assert (await client.get(USERS, headers=_bearer(second.json()["access_token"]))).status_code == 200

    # A second passkey of an already enrolled account brings no new codes.
    fresh = _bearer(second.json()["access_token"])
    more = await _register(client, fresh, SoftAuthenticator(), name="Android")
    assert more.status_code == 200 and more.json()["recovery_codes"] is None


async def test_second_step_only_accepts_that_accounts_passkey(client, factory):
    owner, intruder = await factory.user("step-owner", role="MASTER_GC"), await factory.user("step-intruder")
    owner_phone, intruder_phone = SoftAuthenticator(), SoftAuthenticator()
    assert (await _register(client, owner["headers"], owner_phone)).status_code == 200
    assert (await _register(client, intruder["headers"], intruder_phone)).status_code == 200

    login = await _password_login(client, owner)
    refused = await _login(client, intruder_phone, temp_token=login["temp_token"])
    assert refused.status_code == 401
    assert (await _last_failure(intruder))["reason"] == "other_account"
    assert (await client.post(f"{PASSKEYS}/login/options", json={"temp_token": "nope"})).status_code == 401
    assert (await _login(client, owner_phone, temp_token=login["temp_token"])).status_code == 200


async def test_recovery_code_is_the_way_in_without_the_passkey(client, factory):
    owner = await factory.user("lost", role="MASTER_GC")
    codes = (await _register(client, owner["headers"], SoftAuthenticator())).json()["recovery_codes"]
    login = await _password_login(client, owner)

    # There is no authenticator app on this account: a 6-digit code proves nothing.
    guess = await client.post(f"{AUTH}/mfa/verify", json={"temp_token": login["temp_token"], "totp_code": "123456"})
    assert guess.status_code == 401
    recovered = await client.post(f"{AUTH}/mfa/verify", json={"temp_token": login["temp_token"], "recovery_code": codes[0]})
    assert recovered.status_code == 200, recovered.text
    assert decode_claims(recovered.json()["access_token"], TOKEN_ACCESS)["mfa"] is True
    spent = await client.post(f"{AUTH}/mfa/verify", json={"temp_token": login["temp_token"], "recovery_code": codes[0]})
    assert spent.status_code == 401


async def test_the_last_passkey_of_an_obliged_account_stays(client, factory):
    owner = await factory.user("last", role="MASTER_GC")
    phone = SoftAuthenticator()
    first = (await _register(client, owner["headers"], phone)).json()["passkey"]["id"]
    session = _bearer((await _login(client, phone)).json()["access_token"])

    # With a second factor on the account, a session that did not pass it manages nothing.
    assert (await client.delete(f"{PASSKEYS}/{first}", headers=owner["headers"])).status_code == 403
    assert (await client.post(f"{PASSKEYS}/register/options", headers=owner["headers"])).status_code == 403

    kept = await client.delete(f"{PASSKEYS}/{first}", headers=session)
    assert kept.status_code == 409 and kept.json()["detail"] == passkey_service.LAST_PASSKEY_DETAIL

    second = (await _register(client, session, SoftAuthenticator(), name="Android")).json()["passkey"]["id"]
    assert (await client.delete(f"{PASSKEYS}/{first}", headers=session)).status_code == 200
    assert [p["id"] for p in (await client.get(PASSKEYS, headers=session)).json()] == [second]


async def test_recovery_codes_are_regenerated_with_a_recent_passkey_login(client, factory):
    owner = await factory.user("regen", role="MASTER_GC")
    phone = SoftAuthenticator()
    old_codes = (await _register(client, owner["headers"], phone)).json()["recovery_codes"]

    # Not from a session that never passed the passkey, nor from an old one.
    assert (await client.post(f"{AUTH}/mfa/recovery-codes", headers=owner["headers"])).status_code == 403
    assert (await client.post(
        f"{AUTH}/mfa/recovery-codes", headers=_session(owner, mfa=True, age=16 * 60)
    )).status_code == 403

    session = _bearer((await _login(client, phone)).json()["access_token"])
    fresh = await client.post(f"{AUTH}/mfa/recovery-codes", headers=session)
    assert fresh.status_code == 200, fresh.text
    new_codes = fresh.json()["recovery_codes"]
    assert len(new_codes) == 10 and not set(new_codes) & set(old_codes)

    # An account without any second factor has nothing to regenerate.
    plain = await factory.user("regen-plain")
    assert (await client.post(f"{AUTH}/mfa/recovery-codes", headers=plain["headers"])).status_code == 400


# ----------------------------------------------------------------------------
# Next to TOTP: nothing about it changes
# ----------------------------------------------------------------------------
async def _enrol_totp(client, user: dict) -> pyotp.TOTP:
    setup = await client.post(f"{AUTH}/mfa/setup", headers=user["headers"])
    totp = pyotp.TOTP(setup.json()["secret"])
    done = await client.post(f"{AUTH}/mfa/verify-setup", json={"totp_code": totp.now()}, headers=user["headers"])
    assert done.status_code == 200, done.text
    return totp


async def test_passkey_as_alternative_to_the_totp_code(client, factory):
    user = await factory.user("both")
    totp = await _enrol_totp(client, user)
    phone = SoftAuthenticator()
    # The account has a second factor now: only a session born of it adds a passkey.
    assert (await client.post(f"{PASSKEYS}/register/options", headers=user["headers"])).status_code == 403
    assert (await _register(client, _session(user, mfa=True), phone)).status_code == 200

    login = await _password_login(client, user)
    assert login["mfa_required"] is True and login["mfa_methods"] == ["passkey", "totp", "recovery"]
    with_passkey = await _login(client, phone, temp_token=login["temp_token"])
    assert with_passkey.status_code == 200
    # ... and the code of the app keeps working exactly as before.
    with_code = await client.post(f"{AUTH}/mfa/verify", json={"temp_token": login["temp_token"], "totp_code": totp.now()})
    assert with_code.status_code == 200

    # Regenerating codes still demands the live TOTP code of an account that has the app.
    session = _bearer(with_passkey.json()["access_token"])
    assert (await client.post(f"{AUTH}/mfa/recovery-codes", headers=session)).status_code == 400
    assert (await client.post(
        f"{AUTH}/mfa/recovery-codes", json={"totp_code": totp.now()}, headers=session
    )).status_code == 200

    # Turning the app off keeps the passkey: it is a way to sign in, not the factor switched off.
    assert (await client.post(f"{AUTH}/mfa/disable", headers=session)).status_code == 200
    assert len((await client.get(PASSKEYS, headers=session)).json()) == 1
    assert (await _password_login(client, user))["mfa_required"] is False
    assert (await _login(client, phone)).status_code == 200


async def test_login_without_passkeys_is_unchanged_for_totp_accounts(client, factory):
    user = await factory.user("totp-only")
    await _enrol_totp(client, user)
    login = await _password_login(client, user)
    assert login["mfa_required"] is True and login["mfa_methods"] == ["totp", "recovery"]
    assert (await client.post(f"{PASSKEYS}/login/options", json={"temp_token": login["temp_token"]})).status_code == 400


async def test_peer_reset_removes_the_passkeys_too(client, factory):
    """The device may be lost or in other hands: the reset leaves nothing behind."""
    admin, target = await factory.user("reset-admin", role="MASTER_GC"), await factory.user("reset-target", role="MASTER_GC")
    phone = SoftAuthenticator()
    assert (await _register(client, target["headers"], phone)).status_code == 200
    reset = await client.post(
        f"{USERS}/{target['id']}/mfa-reset", json={"reason": "Perdió el teléfono"}, headers=admin["headers"]
    )
    assert reset.status_code == 200, reset.text
    assert await fetch_all("SELECT 1 FROM webauthn_credentials WHERE user_id = :id", id=uuid.UUID(target["id"])) == []
    assert await fetch_all("SELECT 1 FROM mfa_recovery_codes WHERE user_id = :id", id=uuid.UUID(target["id"])) == []
    assert (await _login(client, phone)).status_code == 401
    assert (await _password_login(client, target))["mfa_enrollment_required"] is True


# ----------------------------------------------------------------------------
# Pure rules
# ----------------------------------------------------------------------------
class _Account:
    def __init__(self, role="STUDENT", mfa_enabled=False, mfa_secret=None):
        self.role, self.mfa_enabled, self.mfa_secret = role, mfa_enabled, mfa_secret


def test_second_step_methods():
    methods = mfa_service.second_step_methods
    assert methods(_Account(), 0) == []
    assert methods(_Account(), 3) == []  # a shortcut, not a factor
    assert methods(_Account(mfa_enabled=True, mfa_secret="x"), 0) == ["totp", "recovery"]
    assert methods(_Account(mfa_enabled=True, mfa_secret="x"), 1) == ["passkey", "totp", "recovery"]
    assert methods(_Account("MASTER_GC"), 0) == []
    assert methods(_Account("MASTER_GC"), 1) == ["passkey", "recovery"]
    assert mfa_service.enrollment_pending(_Account("MASTER_GC"), 0) is True
    assert mfa_service.enrollment_pending(_Account("MASTER_GC"), 1) is False
    assert mfa_service.enrollment_pending(_Account(), 0) is False


def test_the_event_node_bundle_never_carries_passkeys():
    """On the node everybody signs in with the password only (docs/PASSKEYS.md §nodo)."""
    exported = set(nodo_sync_core.EVENT_TABLE_NAMES) | set(nodo_sync_core.MESH_TABLES)
    assert not {name for name in exported if "webauthn" in name}
