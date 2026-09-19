"""The service is live: it must boot with nothing but DATABASE_URL."""

import os
import pathlib
import subprocess
import sys
import textwrap

from tests.conftest import TEST_DATABASE_URL, requires_db

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent("""
    import asyncio, json
    import httpx
    import app.main as main
    from app.config import settings
    from app.db import engine

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://boot") as client:
            root = await client.get("/")
            me = await client.get("/api/v1/auth/me")
            login = await client.post(
                "/api/v1/auth/login", json={"email": "a@example.com", "password": "x"}
            )
            users = await client.get("/api/v1/users/me", headers={"Authorization": "Bearer x"})
            honors = await client.get("/api/v1/honors", headers={"Authorization": "Bearer x"})
            upload = await client.post(
                "/api/v1/media/upload", files={"file": ("a.png", b"x", "image/png")}
            )
        await engine.dispose()
        print(json.dumps({
            "root": [root.status_code, root.json()],
            "me": [me.status_code, me.json()],
            "login": [login.status_code, login.json()],
            "users": [users.status_code, users.json()],
            "honors": honors.status_code,
            "upload": [upload.status_code, upload.json()],
            "flags": [settings.auth_configured, settings.storage_configured,
                      settings.email_configured, bool(settings.SENTRY_DSN)],
        }))

    asyncio.run(run())
    """)


def _run(env_extra: dict) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(BACKEND_DIR), **env_extra}
    return subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@requires_db
def test_boots_with_only_database_url():
    import json

    result = _run({"DATABASE_URL": TEST_DATABASE_URL})
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])

    assert out["flags"] == [False, False, False, False]
    assert out["root"] == [200, {"service": "adventist.club", "api": "/api/v1", "docs": "/docs"}]
    assert out["me"] == [503, {"detail": "Auth no configurado"}]
    assert out["login"] == [503, {"detail": "Auth no configurado"}]
    assert out["users"] == [503, {"detail": "Auth no configurado"}]
    assert out["upload"] == [503, {"detail": "Auth no configurado"}]
    # Public endpoints keep working, even when a (useless) token is sent.
    assert out["honors"] == 200


@requires_db
def test_boots_with_blank_and_malformed_optional_env():
    import json

    result = _run(
        {
            "DATABASE_URL": TEST_DATABASE_URL,
            "JWT_SECRET": "",
            "SENTRY_DSN": "",
            "RESEND_API_KEY": " ",
            "R2_ACCOUNT_ID": "",
            "RATE_LIMIT_ENABLED": "maybe",
            "SENTRY_DEBUG_MODE": "",
            "ACCESS_TOKEN_EXPIRE_MINUTES": "soon",
            "REFRESH_TOKEN_EXPIRE_DAYS": "",
            "JWT_ALGORITHM": "",
            "ENVIRONMENT": "production",
        }
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["root"][0] == 200
    assert out["me"] == [503, {"detail": "Auth no configurado"}]


RATE_LIMIT_SCRIPT = textwrap.dedent("""
    import asyncio, json
    import httpx
    import app.main as main
    from app.db import engine

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        body = {"email": "rate-limit-nobody@example.com", "password": "Wrong-Pass1"}
        async with httpx.AsyncClient(transport=transport, base_url="http://boot") as client:
            mine = [(await client.post("/api/v1/auth/login", json=body)).status_code for _ in range(7)]
            other = await client.post(
                "/api/v1/auth/login", json=body, headers={"X-Forwarded-For": "203.0.113.9"}
            )
            last = await client.post("/api/v1/auth/login", json=body)
            public = [(await client.get("/")).status_code for _ in range(10)]
        await engine.dispose()
        print(json.dumps({"mine": mine, "other": other.status_code, "last": last.json(),
                          "public": sorted(set(public))}))

    asyncio.run(run())
    """)


@requires_db
def test_rate_limit_on_login_when_enabled():
    import json

    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BACKEND_DIR),
        "DATABASE_URL": TEST_DATABASE_URL,
        "JWT_SECRET": "rate-limit-test-secret-" + "x" * 32,
        "RATE_LIMIT_ENABLED": "true",
    }
    result = subprocess.run(
        [sys.executable, "-c", RATE_LIMIT_SCRIPT],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["mine"] == [401] * 5 + [429] * 2  # default RATE_LIMIT_LOGIN is 5/minute
    assert out["other"] == 401  # buckets are per client address
    assert "detail" in out["last"]
    assert out["public"] == [200]  # undecorated endpoints are never limited
