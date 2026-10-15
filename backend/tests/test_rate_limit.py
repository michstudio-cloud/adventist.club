"""Login rate limits behind the web app's server (docs/RATE_LIMITS.md).

Every login goes through the Next.js session bridge, so without the trusted client address all
visitors share the bridge's address; and at a camporee 20-40 judges log in from one venue NAT.
"""
import bcrypt
import pytest
from starlette.requests import Request

from app import security
from app.config import settings
from app.rate_limit import (
    CLIENT_IP_HEADER,
    PROXY_AUTH_HEADER,
    client_ip,
    limiter,
    reset_account_limits,
)
from app.services.audit import _valid_ip
from tests.conftest import DEFAULT_PASSWORD, module_factory, requires_db

LOGIN = "/api/v1/auth/login"
FORGOT = "/api/v1/auth/forgot-password"
SECRET = "proxy-shared-secret-for-tests-" + "s" * 16
factory = module_factory("ratelimit")


def _request(headers: dict[str, str], peer: str = "10.0.0.5") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": (peer, 1234)})


def _bridge(ip: str, secret: str = SECRET) -> dict[str, str]:
    return {PROXY_AUTH_HEADER: secret, CLIENT_IP_HEADER: ip}


@pytest.fixture
def with_secret(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", SECRET)


@pytest.fixture
def limits_on(monkeypatch):
    """The suite runs with RATE_LIMIT_ENABLED=false; these tests switch the limiter on, with
    empty buckets, and a fast dummy hash so unknown e-mails do not each cost a full bcrypt."""
    monkeypatch.setattr(security, "_DUMMY_HASH", bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode())
    limiter.reset()
    reset_account_limits()
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()
    reset_account_limits()


# ----------------------------------------------------------------------------
# The trusted client address
# ----------------------------------------------------------------------------
def test_forwarded_ip_is_used_when_the_secret_matches(with_secret):
    assert client_ip(_request(_bridge("198.51.100.7"))) == "198.51.100.7"
    assert client_ip(_request(_bridge("2001:db8::1"))) == "2001:db8::1"


def test_forwarded_ip_is_ignored_without_a_configured_secret(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", None)
    request = _request({**_bridge("198.51.100.7"), "X-Forwarded-For": "203.0.113.1"})
    assert client_ip(request) == "203.0.113.1"  # today's behaviour
    assert client_ip(_request(_bridge("198.51.100.7"))) == "10.0.0.5"


def test_forwarded_ip_is_ignored_with_a_wrong_or_missing_secret(with_secret):
    wrong = _request({**_bridge("198.51.100.7", secret=SECRET + "x"), "X-Forwarded-For": "203.0.113.1"})
    assert client_ip(wrong) == "203.0.113.1"
    spoofed = _request({CLIENT_IP_HEADER: "198.51.100.7"})  # header without any secret
    assert client_ip(spoofed) == "10.0.0.5"
    empty = _request(_bridge("198.51.100.7", secret=""))
    assert client_ip(empty) == "10.0.0.5"


def test_short_secret_is_never_trusted(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "short")
    assert settings.proxy_shared_secret is None
    assert client_ip(_request(_bridge("198.51.100.7", secret="short"))) == "10.0.0.5"


def test_garbage_forwarded_ip_falls_back(with_secret):
    request = _request({**_bridge("not-an-ip"), "X-Forwarded-For": "203.0.113.1"})
    assert client_ip(request) == "203.0.113.1"


def test_audit_records_the_trusted_address(with_secret):
    assert _valid_ip(_request(_bridge("198.51.100.7"))) == "198.51.100.7"
    assert _valid_ip(_request({CLIENT_IP_HEADER: "198.51.100.7"})) == "10.0.0.5"


def test_limit_settings_fall_back_on_garbage():
    from app.config import Settings

    parsed = Settings(DATABASE_URL="postgresql://x@127.0.0.1/x", RATE_LIMIT_LOGIN="lots", RATE_LIMIT_LOGIN_ACCOUNT=" ")
    assert parsed.RATE_LIMIT_LOGIN == "60/minute"
    assert parsed.RATE_LIMIT_LOGIN_ACCOUNT == "10/15 minutes"


# ----------------------------------------------------------------------------
# Login limits (per account + per address)
# ----------------------------------------------------------------------------
@requires_db
async def test_thirty_judges_behind_one_address_all_log_in(client, factory, limits_on, with_secret):
    venue = _bridge("198.51.100.20")
    judges = [await factory.user(f"judge{i}") for i in range(30)]
    for judge in judges:
        response = await client.post(
            LOGIN, json={"email": judge["email"], "password": DEFAULT_PASSWORD}, headers=venue
        )
        assert response.status_code == 200, response.text


@requires_db
async def test_the_eleventh_attempt_for_one_email_is_limited(client, factory, limits_on):
    judge = await factory.user("brute")
    body = {"email": judge["email"], "password": "Wrong-Pass1"}
    for attempt in range(10):
        # From a new address every time: rotating addresses buys no extra guesses.
        response = await client.post(LOGIN, json=body, headers={"X-Forwarded-For": f"203.0.113.{attempt}"})
        assert response.status_code == 401, (attempt, response.text)

    # Even the right password, in any letter case, waits now.
    limited = await client.post(
        LOGIN,
        json={"email": judge["email"].upper(), "password": DEFAULT_PASSWORD},
        headers={"X-Forwarded-For": "203.0.113.99"},
    )
    assert limited.status_code == 429
    retry_after = int(limited.headers["Retry-After"])
    assert 0 < retry_after <= 15 * 60
    assert limited.json()["detail"].startswith("Demasiados intentos. Espera ")
    assert "minuto" in limited.json()["detail"]

    # Somebody else on the same address is not affected.
    other = await factory.user("bystander")
    ok = await client.post(
        LOGIN, json={"email": other["email"], "password": DEFAULT_PASSWORD},
        headers={"X-Forwarded-For": "203.0.113.99"},
    )
    assert ok.status_code == 200


@requires_db
async def test_the_right_password_forgets_earlier_mistakes(client, factory, limits_on):
    judge = await factory.user("typo")
    for _ in range(9):
        assert (await client.post(LOGIN, json={"email": judge["email"], "password": "nope"})).status_code == 401
    assert (
        await client.post(LOGIN, json={"email": judge["email"], "password": DEFAULT_PASSWORD})
    ).status_code == 200
    for _ in range(9):
        assert (await client.post(LOGIN, json={"email": judge["email"], "password": "nope"})).status_code == 401


@requires_db
async def test_per_address_cap(client, limits_on, with_secret):
    """60/minute per address (default RATE_LIMIT_LOGIN), counted per TRUSTED address."""
    venue = _bridge("198.51.100.30")
    for i in range(60):
        response = await client.post(
            LOGIN, json={"email": f"nobody{i}@example.com", "password": "x"}, headers=venue
        )
        assert response.status_code == 401, (i, response.text)
    capped = await client.post(LOGIN, json={"email": "nobody-last@example.com", "password": "x"}, headers=venue)
    assert capped.status_code == 429
    assert 0 < int(capped.headers["Retry-After"]) <= 60
    assert capped.json()["detail"].startswith("Demasiados intentos. Espera ")

    # Another venue, through the same bridge, is its own bucket.
    elsewhere = await client.post(
        LOGIN, json={"email": "nobody-last@example.com", "password": "x"}, headers=_bridge("198.51.100.31")
    )
    assert elsewhere.status_code == 401

    # A spoofed address without the secret lands in the bridge's own bucket, never in a fresh one.
    spoofed = {CLIENT_IP_HEADER: "198.51.100.32", "X-Forwarded-For": "198.51.100.30"}
    blocked = await client.post(LOGIN, json={"email": "nobody-x@example.com", "password": "x"}, headers=spoofed)
    assert blocked.status_code == 429


@requires_db
async def test_forgot_password_is_limited_per_email_and_per_address(client, limits_on, with_secret):
    one = _bridge("198.51.100.40")
    for _ in range(3):  # RATE_LIMIT_FORGOT_PASSWORD_ACCOUNT = 3/hour
        assert (await client.post(FORGOT, json={"email": "same@example.com"}, headers=one)).status_code == 200
    limited = await client.post(FORGOT, json={"email": "SAME@example.com"}, headers=_bridge("198.51.100.41"))
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0

    for i in range(17):  # RATE_LIMIT_FORGOT_PASSWORD = 20/hour per address (3 + 17)
        response = await client.post(FORGOT, json={"email": f"user{i}@example.com"}, headers=one)
        assert response.status_code == 200, i
    capped = await client.post(FORGOT, json={"email": "fresh@example.com"}, headers=one)
    assert capped.status_code == 429
    assert "Retry-After" in capped.headers
