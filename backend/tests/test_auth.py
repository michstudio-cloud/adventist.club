"""Auth flows: register, login, refresh, password reset, email verification, MFA."""

import json
from datetime import date, timedelta

import bcrypt
import pyotp

from app.services import email as email_service
from tests.conftest import DEFAULT_PASSWORD, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("auth")

AUTH = "/api/v1/auth"


def _payload(factory, label, **extra):
    return {
        "email": factory.email(label),
        "password": DEFAULT_PASSWORD,
        "name": factory.name(label),
        **extra,
    }


async def _register(client, factory, label, **extra):
    response = await client.post(f"{AUTH}/register", json=_payload(factory, label, **extra))
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_register_login_me(client, factory):
    created = await _register(client, factory, "alice")
    assert created["role"] == "STUDENT"
    assert created["status"] == "ACTIVE"
    assert created["token_type"] == "bearer"
    assert "password" not in json.dumps(created).lower()
    assert "code" not in created

    login = await client.post(
        f"{AUTH}/login", json={"email": factory.email("alice"), "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["mfa_required"] is False
    assert body["access_token"] and body["refresh_token"]

    me = await client.get(f"{AUTH}/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    profile = me.json()
    assert profile["email"] == factory.email("alice")
    assert profile["verification_status"] == "PENDING"
    assert "password_hash" not in profile and "mfa_secret" not in profile

    row = await fetch_one(
        "SELECT last_login, created_at FROM users WHERE email = :e", e=factory.email("alice")
    )
    assert row["last_login"] is not None and row["last_login"].tzinfo is not None
    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE user_email = :e", e=factory.email("alice")
    )
    assert [r["action"] for r in audit] == ["REGISTER"]


async def test_me_requires_token(client):
    assert (await client.get(f"{AUTH}/me")).status_code == 401
    assert (await client.get(f"{AUTH}/me", headers=_bearer("garbage"))).status_code == 401


async def test_wrong_password_is_401(client, factory):
    await _register(client, factory, "wrongpw")
    response = await client.post(
        f"{AUTH}/login", json={"email": factory.email("wrongpw"), "password": "Nope12345"}
    )
    assert response.status_code == 401
    unknown = await client.post(
        f"{AUTH}/login", json={"email": factory.email("ghost"), "password": "Nope12345"}
    )
    assert unknown.status_code == 401
    assert unknown.json() == response.json()


async def test_credentials_are_not_accepted_as_query_params(client, factory):
    await _register(client, factory, "query")
    response = await client.post(
        f"{AUTH}/login",
        params={"email": factory.email("query"), "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 422


async def test_duplicate_email_case_insensitive(client, factory):
    await _register(client, factory, "dupe")
    payload = _payload(factory, "dupe")
    payload["email"] = payload["email"].upper()
    response = await client.post(f"{AUTH}/register", json=payload)
    assert response.status_code == 409, response.text
    rows = await fetch_all("SELECT id FROM users WHERE email = :e", e=factory.email("dupe"))
    assert len(rows) == 1

    # ... and login is case-insensitive too.
    login = await client.post(
        f"{AUTH}/login",
        json={"email": factory.email("dupe").upper(), "password": DEFAULT_PASSWORD},
    )
    assert login.status_code == 200


async def test_privileged_roles_cannot_be_self_assigned(client, factory):
    for index, role in enumerate(
        [
            "MASTER_GC",
            "ADMIN_DIVISION",
            "ADMIN_UNION",
            "ADMIN_ASSOCIATION",
            "COORDINATOR_ZONE",
        ]
    ):
        response = await client.post(
            f"{AUTH}/register", json=_payload(factory, f"priv{index}", role=role)
        )
        assert response.status_code in (400, 403), (role, response.text)
    rows = await fetch_all(
        "SELECT email FROM users WHERE email LIKE :like", like=f"{factory.prefix}-priv%"
    )
    assert rows == []


async def test_allowed_self_registration_roles(client, factory):
    # CLUB_DIRECTOR is self-service since 003_club_signup (the club needs approval).
    for role in ("STUDENT", "PARENT_GUARDIAN", "INSTRUCTOR", "CLUB_DIRECTOR"):
        created = await _register(client, factory, f"role-{role.lower()}", role=role)
        assert created["role"] == role


async def test_minor_can_only_be_student(client, factory):
    response = await client.post(
        f"{AUTH}/register", json=_payload(factory, "minor1", role="INSTRUCTOR", is_minor=True)
    )
    assert response.status_code == 400

    # A birth date under 18 counts even when the checkbox says otherwise.
    birth = (date.today() - timedelta(days=365 * 10)).isoformat()
    response = await client.post(
        f"{AUTH}/register",
        json=_payload(factory, "minor2", role="PARENT_GUARDIAN", birth_date=birth),
    )
    assert response.status_code == 400

    created = await _register(client, factory, "minor3", role="STUDENT", birth_date=birth)
    row = await fetch_one("SELECT is_minor FROM users WHERE id = :id", id=created["id"])
    assert row["is_minor"] is True


async def test_password_policy_on_register(client, factory):
    for index, weak in enumerate(["Short1a", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere"]):
        payload = _payload(factory, f"weak{index}")
        payload["password"] = weak
        response = await client.post(f"{AUTH}/register", json=payload)
        assert response.status_code == 400, weak


async def test_legacy_bcrypt_hash_can_login(client, factory):
    legacy_hash = bcrypt.hashpw(b"Legacy-Pass1", bcrypt.gensalt(rounds=12, prefix=b"2b")).decode()
    assert legacy_hash.startswith("$2b$12$")
    await factory.user("legacy", password_hash=legacy_hash)
    ok = await client.post(
        f"{AUTH}/login", json={"email": factory.email("legacy"), "password": "Legacy-Pass1"}
    )
    assert ok.status_code == 200, ok.text
    bad = await client.post(
        f"{AUTH}/login", json={"email": factory.email("legacy"), "password": "Legacy-Pass2"}
    )
    assert bad.status_code == 401


async def test_malformed_hash_is_a_failed_login_not_a_500(client, factory):
    await factory.user("badhash", password_hash="not-a-bcrypt-hash")
    response = await client.post(
        f"{AUTH}/login", json={"email": factory.email("badhash"), "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 401


async def test_inactive_account_cannot_login(client, factory):
    await factory.user("suspended", status="SUSPENDED")
    response = await client.post(
        f"{AUTH}/login", json={"email": factory.email("suspended"), "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 403


async def test_refresh(client, factory):
    created = await _register(client, factory, "refresh")
    ok = await client.post(f"{AUTH}/refresh", json={"refresh_token": created["refresh_token"]})
    assert ok.status_code == 200, ok.text
    new_access = ok.json()["access_token"]
    assert (await client.get(f"{AUTH}/me", headers=_bearer(new_access))).status_code == 200

    # An access token is not a refresh token, and vice versa.
    wrong = await client.post(f"{AUTH}/refresh", json={"refresh_token": created["access_token"]})
    assert wrong.status_code == 401
    as_access = await client.get(f"{AUTH}/me", headers=_bearer(created["refresh_token"]))
    assert as_access.status_code == 401


async def test_forgot_and_reset_password(client, factory, monkeypatch):
    sent = []

    async def fake_send(to, name, code, token):
        sent.append((to, code, token))
        return True

    monkeypatch.setattr(email_service, "send_password_reset_email", fake_send)
    created = await _register(client, factory, "reset")

    known = await client.post(f"{AUTH}/forgot-password", json={"email": factory.email("reset")})
    unknown = await client.post(f"{AUTH}/forgot-password", json={"email": factory.email("nobody")})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert set(known.json()) == {"message"}

    row = await fetch_one(
        "SELECT token, code, expires_at FROM email_verifications"
        " WHERE user_id = :id AND type = 'password_reset' AND used = false",
        id=created["id"],
    )
    assert row is not None
    assert row["expires_at"].tzinfo is not None
    # The email goes out after the commit, with the same token that was stored.
    assert sent == [(factory.email("reset"), row["code"], row["token"])]
    # No endpoint ever returns a code or a token.
    assert row["code"] not in known.text and row["token"] not in known.text

    # A weak password is refused and does not burn the token.
    weak = await client.post(
        f"{AUTH}/reset-password", json={"token": row["token"], "new_password": "weak"}
    )
    assert weak.status_code == 400

    new_password = "BrandNew-Pass9"
    done = await client.post(
        f"{AUTH}/reset-password", json={"token": row["token"], "new_password": new_password}
    )
    assert done.status_code == 200, done.text

    old = await client.post(
        f"{AUTH}/login", json={"email": factory.email("reset"), "password": DEFAULT_PASSWORD}
    )
    assert old.status_code == 401
    new = await client.post(
        f"{AUTH}/login", json={"email": factory.email("reset"), "password": new_password}
    )
    assert new.status_code == 200

    reused = await client.post(
        f"{AUTH}/reset-password", json={"token": row["token"], "new_password": "Another-Pass9"}
    )
    assert reused.status_code == 400
    still = await client.post(
        f"{AUTH}/login", json={"email": factory.email("reset"), "password": new_password}
    )
    assert still.status_code == 200


async def test_reset_password_with_email_and_code(client, factory):
    created = await _register(client, factory, "resetcode")
    await client.post(f"{AUTH}/forgot-password", json={"email": factory.email("resetcode")})
    row = await fetch_one(
        "SELECT code FROM email_verifications"
        " WHERE user_id = :id AND type = 'password_reset' AND used = false",
        id=created["id"],
    )
    wrong_code = "000000" if row["code"] != "000000" else "111111"
    bad = await client.post(
        f"{AUTH}/reset-password",
        json={
            "email": factory.email("resetcode"),
            "code": wrong_code,
            "new_password": "Next-Pass99",
        },
    )
    assert bad.status_code == 400
    # Secrets in the query string are not accepted.
    in_query = await client.post(
        f"{AUTH}/reset-password",
        params={
            "email": factory.email("resetcode"),
            "code": row["code"],
            "new_password": "Next-Pass99",
        },
    )
    assert in_query.status_code == 422
    good = await client.post(
        f"{AUTH}/reset-password",
        json={
            "email": factory.email("resetcode"),
            "code": row["code"],
            "new_password": "Next-Pass99",
        },
    )
    assert good.status_code == 200, good.text


async def test_expired_reset_token_is_rejected(client, factory):
    created = await _register(client, factory, "expired")
    await client.post(f"{AUTH}/forgot-password", json={"email": factory.email("expired")})
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as db:
        await db.execute(
            text(
                "UPDATE email_verifications SET expires_at = now() - interval '1 minute'"
                " WHERE user_id = :id AND type = 'password_reset'"
            ),
            {"id": created["id"]},
        )
        await db.commit()
    row = await fetch_one(
        "SELECT token FROM email_verifications WHERE user_id = :id AND type = 'password_reset'",
        id=created["id"],
    )
    response = await client.post(
        f"{AUTH}/reset-password", json={"token": row["token"], "new_password": "Next-Pass99"}
    )
    assert response.status_code == 400


async def test_verify_email_by_code(client, factory):
    created = await _register(client, factory, "vcode")
    headers = _bearer(created["access_token"])
    row = await fetch_one(
        "SELECT code FROM email_verifications WHERE user_id = :id AND type = 'email_verification'",
        id=created["id"],
    )
    wrong_code = "000000" if row["code"] != "000000" else "111111"
    bad = await client.post(f"{AUTH}/verify-email", json={"code": wrong_code}, headers=headers)
    assert bad.status_code == 400
    good = await client.post(f"{AUTH}/verify-email", json={"code": row["code"]}, headers=headers)
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "VERIFIED"
    again = await client.post(f"{AUTH}/verify-email", json={"code": row["code"]}, headers=headers)
    assert again.status_code == 400
    me = await client.get(f"{AUTH}/me", headers=headers)
    assert me.json()["verification_status"] == "VERIFIED"


async def test_verify_email_by_link_token(client, factory):
    created = await _register(client, factory, "vlink")
    row = await fetch_one(
        "SELECT token FROM email_verifications WHERE user_id = :id AND type = 'email_verification'",
        id=created["id"],
    )
    # The token is a secret: it is only accepted in a JSON body.
    in_query = await client.post(f"{AUTH}/verify-email-link", params={"token": row["token"]})
    assert in_query.status_code == 422
    assert (
        await client.get(f"{AUTH}/verify-email-link", params={"token": row["token"]})
    ).status_code == 405

    bad = await client.post(f"{AUTH}/verify-email-link", json={"token": "x" * 43})
    assert bad.status_code == 400
    good = await client.post(f"{AUTH}/verify-email-link", json={"token": row["token"]})
    assert good.status_code == 200, good.text
    assert good.json()["already_verified"] is False
    user = await fetch_one("SELECT verification_status FROM users WHERE id = :id", id=created["id"])
    assert user["verification_status"] == "VERIFIED"
    used = await fetch_one(
        "SELECT used, used_at FROM email_verifications WHERE token = :t", t=row["token"]
    )
    assert used["used"] is True and used["used_at"].tzinfo is not None
    # Double click on the link is not an error.
    again = await client.post(f"{AUTH}/verify-email-link", json={"token": row["token"]})
    assert again.status_code == 200 and again.json()["already_verified"] is True


async def test_resend_verification_invalidates_previous_code(client, factory):
    created = await _register(client, factory, "resend")
    headers = _bearer(created["access_token"])
    first = await fetch_one(
        "SELECT code FROM email_verifications WHERE user_id = :id", id=created["id"]
    )
    response = await client.post(f"{AUTH}/send-verification-email", headers=headers)
    assert response.status_code == 200
    assert "code" not in response.json() and "token" not in response.json()
    live = await fetch_all(
        "SELECT code FROM email_verifications WHERE user_id = :id AND used = false",
        id=created["id"],
    )
    assert len(live) == 1
    if live[0]["code"] != first["code"]:
        stale = await client.post(
            f"{AUTH}/verify-email", json={"code": first["code"]}, headers=headers
        )
        assert stale.status_code == 400


async def test_mfa_full_flow(client, factory):
    created = await _register(client, factory, "mfa")
    headers = _bearer(created["access_token"])

    setup = await client.post(f"{AUTH}/mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["qr_code_url"].startswith("data:image/png;base64,")
    totp = pyotp.TOTP(secret)

    # Not active until verified: login still returns real tokens.
    login = await client.post(
        f"{AUTH}/login", json={"email": factory.email("mfa"), "password": DEFAULT_PASSWORD}
    )
    assert login.json()["mfa_required"] is False

    bad = await client.post(
        f"{AUTH}/mfa/verify-setup", json={"totp_code": "000000"}, headers=headers
    )
    assert bad.status_code == 400
    ok = await client.post(
        f"{AUTH}/mfa/verify-setup", json={"totp_code": totp.now()}, headers=headers
    )
    assert ok.status_code == 200, ok.text

    login = await client.post(
        f"{AUTH}/login", json={"email": factory.email("mfa"), "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200
    body = login.json()
    assert body["mfa_required"] is True
    assert body["access_token"] == "" and body["refresh_token"] == ""
    temp_token = body["temp_token"]

    # The temp token is good for nothing but the MFA step.
    assert (await client.get(f"{AUTH}/me", headers=_bearer(temp_token))).status_code == 401
    assert (
        await client.post(f"{AUTH}/refresh", json={"refresh_token": temp_token})
    ).status_code == 401

    wrong = await client.post(
        f"{AUTH}/mfa/verify", json={"temp_token": temp_token, "totp_code": "000000"}
    )
    assert wrong.status_code == 401
    not_temp = await client.post(
        f"{AUTH}/mfa/verify", json={"temp_token": created["access_token"], "totp_code": totp.now()}
    )
    assert not_temp.status_code == 401

    verified = await client.post(
        f"{AUTH}/mfa/verify", json={"temp_token": temp_token, "totp_code": totp.now()}
    )
    assert verified.status_code == 200, verified.text
    tokens = verified.json()
    me = await client.get(f"{AUTH}/me", headers=_bearer(tokens["access_token"]))
    assert me.status_code == 200 and me.json()["mfa_enabled"] is True

    disabled = await client.post(f"{AUTH}/mfa/disable", headers=_bearer(tokens["access_token"]))
    assert disabled.status_code == 200
    row = await fetch_one(
        "SELECT mfa_enabled, mfa_secret FROM users WHERE id = :id", id=created["id"]
    )
    assert row["mfa_enabled"] is False and row["mfa_secret"] is None


async def test_master_gc_cannot_disable_mfa(client, factory):
    master = await factory.user("master", role="MASTER_GC")
    response = await client.post(f"{AUTH}/mfa/disable", headers=master["headers"])
    assert response.status_code == 403


async def test_reset_token_can_only_be_claimed_once_concurrently(client, factory):
    import asyncio

    created = await _register(client, factory, "race")
    await client.post(f"{AUTH}/forgot-password", json={"email": factory.email("race")})
    row = await fetch_one(
        "SELECT token FROM email_verifications"
        " WHERE user_id = :id AND type = 'password_reset' AND used = false",
        id=created["id"],
    )
    responses = await asyncio.gather(
        *[
            client.post(
                f"{AUTH}/reset-password",
                json={"token": row["token"], "new_password": f"Race-Pass-{i}9"},
            )
            for i in range(4)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 400, 400, 400]


async def test_email_failure_never_fails_the_request(client, factory, monkeypatch, caplog):
    from app.config import settings

    def boom(to, subject, html):
        raise RuntimeError("resend is down")

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(email_service, "_send_sync", boom)
    with caplog.at_level("ERROR"):
        created = await _register(client, factory, "mailfail")
    assert created["email"] == factory.email("mailfail")
    assert any("Failed to send email" in record.message for record in caplog.records)


async def test_email_is_skipped_and_logged_without_api_key(caplog):
    with caplog.at_level("INFO"):
        sent = await email_service.send_email("nobody@example.com", "subject", "<p>hi</p>")
    assert sent is False
    assert any("RESEND_API_KEY not configured" in record.message for record in caplog.records)


async def test_nobody_attaches_themselves_to_an_organization(client, factory):
    """Belonging to a club or field is granted by an administrator (later: an invitation), never
    self-declared: otherwise a stranger registers as INSTRUCTOR of any club and reads its members."""
    org = await factory.org("self-attach", "association")
    for role in ("STUDENT", "INSTRUCTOR"):
        response = await client.post(
            f"{AUTH}/register", json=_payload(factory, f"attach-{role.lower()}", role=role, organization_id=org["id"])
        )
        assert response.status_code == 403, response.text
        assert "organiz" in response.json()["detail"].lower()
    alias = await client.post(
        f"{AUTH}/register", json=_payload(factory, "attach-alias", org_node_id=org["id"])
    )
    assert alias.status_code == 403
    created = await fetch_one("SELECT count(*) AS n FROM users WHERE email LIKE :like", like=f"{factory.prefix}-attach-%")
    assert created["n"] == 0
