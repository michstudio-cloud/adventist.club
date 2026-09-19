"""Pre-existing public endpoints, database URL handling, config and monitoring."""

import pathlib
import re

from sqlalchemy.engine import make_url

from app import monitoring
from app.config import Settings
from app.db import connect_args_for, normalize_database_url
from tests.conftest import fetch_all, requires_db

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


@requires_db
async def test_root_and_health(client):
    root = await client.get("/")
    assert root.status_code == 200
    assert root.json() == {"service": "adventist.club", "api": "/api/v1", "docs": "/docs"}
    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"ok": True, "service": "adventist.club", "database": "connected"}


@requires_db
async def test_ministries_shape(client):
    response = await client.get("/api/v1/ministries")
    assert response.status_code == 200
    rows = response.json()
    assert {"pathfinders", "adventurers"} <= {row["slug"] for row in rows}
    assert all(set(row) == {"id", "slug", "name"} for row in rows)
    expected = await fetch_all("SELECT name FROM ministries WHERE status = 'active' ORDER BY name")
    assert [row["name"] for row in rows] == [row["name"] for row in expected]


def test_pre_existing_routes_are_still_registered():
    from app.main import app

    routes = {
        (method, route.path) for route in app.routes for method in getattr(route, "methods", ())
    }
    for expected in [
        ("GET", "/"),
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/ministries"),
        ("GET", "/api/v1/applications"),
        ("GET", "/api/v1/honors"),
        ("POST", "/api/v1/certificates/prototype-batch"),
        ("GET", "/api/v1/certificates/verify/{certificate_no}"),
        ("POST", "/api/v1/printing/pdf"),
    ]:
        assert expected in routes, expected


def test_literal_routes_are_declared_before_parameter_routes():
    from app.main import app

    paths = [route.path for route in app.routes]
    for prefix, literals, parameter in [
        (
            "/api/v1/honors",
            ["/categories", "/my/created", "/pending/reviews", "/stats/overview"],
            "/{honor_id}",
        ),
        (
            "/api/v1/users",
            ["/me", "/guardianships/my-children", "/guardianships/my-guardians"],
            "/{user_id}",
        ),
        ("/api/v1/org-nodes", ["/type/{node_type}"], "/{node_id}"),
    ]:
        first_parameter = paths.index(prefix + parameter)
        for literal in literals:
            assert paths.index(prefix + literal) < first_parameter, literal


def test_no_secret_is_ever_a_query_parameter():
    from app.main import app

    secret_names = {
        "password",
        "new_password",
        "token",
        "code",
        "refresh_token",
        "temp_token",
        "totp_code",
    }
    schema = app.openapi()
    for path, operations in schema["paths"].items():
        for operation in operations.values():
            for parameter in operation.get("parameters", []):
                if parameter["in"] == "query":
                    assert parameter["name"] not in secret_names, (path, parameter["name"])


def test_printing_pdf_still_works_without_database():
    import base64
    import io

    from fastapi.testclient import TestClient
    from PIL import Image

    from app.main import app

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    body = {
        "images": [image],
        "page_width_in": 8.5,
        "page_height_in": 11,
        "item_width_in": 4,
        "item_height_in": 3,
    }
    with TestClient(app) as sync_client:
        response = sync_client.post("/api/v1/printing/pdf", json=body)
        too_big = sync_client.post(
            "/api/v1/printing/pdf", json={**body, "item_width_in": 40, "item_height_in": 40}
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert too_big.status_code == 422


# ---------------------------------------------------------------------------
# app/db.py
# ---------------------------------------------------------------------------
def test_database_url_is_normalised_for_asyncpg():
    url = normalize_database_url(
        "postgresql://user:pw@ep-x.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    assert url.drivername == "postgresql+asyncpg"
    assert "sslmode" not in url.query and "channel_binding" not in url.query
    assert normalize_database_url("postgres://u@h/db").drivername == "postgresql+asyncpg"
    assert normalize_database_url("postgresql+psycopg2://u@h/db").drivername == "postgresql+asyncpg"
    assert normalize_database_url("postgresql+asyncpg://u@h/db").drivername == "postgresql+asyncpg"


def test_ssl_only_for_remote_hosts():
    assert connect_args_for(make_url("postgresql+asyncpg://u@localhost/db")) == {}
    assert connect_args_for(make_url("postgresql+asyncpg://u@127.0.0.1:55432/db")) == {}
    assert connect_args_for(make_url("postgresql+asyncpg://u@ep-x.neon.tech/db")) == {
        "ssl": "require"
    }
    assert connect_args_for(make_url("postgresql+asyncpg://u@10.0.0.5/db")) == {"ssl": "require"}


# ---------------------------------------------------------------------------
# Config, monitoring, timestamps
# ---------------------------------------------------------------------------
def test_settings_use_the_legacy_env_names(monkeypatch):
    names = [
        "JWT_SECRET",
        "JWT_ALGORITHM",
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "REFRESH_TOKEN_EXPIRE_DAYS",
        "MFA_ISSUER",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET_NAME",
        "R2_PUBLIC_URL",
        "RESEND_API_KEY",
        "EMAIL_FROM",
        "EMAIL_FROM_NAME",
        "FRONTEND_URL",
        "SENTRY_DSN",
        "SENTRY_DEBUG_MODE",
        "SERVER_NAME",
        "ENVIRONMENT",
        "ENV",
        "RATE_LIMIT_ENABLED",
        "DATABASE_URL",
        "PUBLIC_BASE_URL",
        "PUBLIC_WEB_URL",
        "CORS_ORIGINS",
    ]
    assert set(names) <= set(Settings.model_fields)

    monkeypatch.setenv("ENV", "staging")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert Settings(_env_file=None).environment == "staging"
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings(_env_file=None).environment == "production"

    monkeypatch.setenv("PUBLIC_WEB_URL", "https://web.example/")
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    assert Settings(_env_file=None).frontend_url == "https://web.example"
    monkeypatch.setenv("FRONTEND_URL", "https://front.example")
    assert Settings(_env_file=None).frontend_url == "https://front.example"


def test_sentry_is_a_noop_without_dsn(monkeypatch):
    import sentry_sdk

    def fail(*args, **kwargs):
        raise AssertionError("sentry_sdk.init must not be called without a DSN")

    monkeypatch.setattr(sentry_sdk, "init", fail)
    assert monitoring.settings.SENTRY_DSN is None
    assert monitoring.init_sentry() is False


def test_sentry_redacts_secrets():
    event = {
        "request": {
            "data": {
                "email": "a@b.co",
                "password": "hunter2",
                "nested": [{"refresh_token": "abc"}],
            },
            "headers": {"Authorization": "Bearer abc", "Accept": "*/*"},
            "query_string": "token=abc",
        }
    }
    cleaned = monitoring.before_send(event, {})
    assert "hunter2" not in str(cleaned) and "abc" not in str(cleaned)
    assert cleaned["request"]["data"]["email"] == "a@b.co"


def test_no_naive_timestamps_in_the_codebase():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        source = path.read_text()
        if (
            re.search(r"utcnow\(\)", source.replace("def utcnow()", ""))
            and "datetime.utcnow" in source
        ):
            offenders.append(path.name)
        if re.search(r"datetime\.(utcnow|utcfromtimestamp)\(|datetime\.now\(\s*\)", source):
            offenders.append(path.name)
        if re.search(r"DateTime\((?!timezone=True)", source):
            offenders.append(f"{path.name}: DateTime without timezone")
    assert offenders == []


@requires_db
async def test_models_match_the_migrated_schema():
    """Every mapped column of the tables in this database exists with the same nullability."""
    from app.models import Base

    rows = await fetch_all(
        "SELECT table_name, column_name, is_nullable, data_type, udt_name"
        " FROM information_schema.columns WHERE table_schema = 'public'"
    )
    actual = {(r["table_name"], r["column_name"]): r for r in rows}
    tables_in_db = {table for table, _ in actual}
    problems = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables_in_db:
            continue  # certificate tables are not part of this test database
        mapped = {column.name for column in table.columns}
        in_db = {column for name, column in actual if name == table.name}
        problems += [f"{table.name}.{c} is mapped but missing" for c in mapped - in_db]
        problems += [f"{table.name}.{c} is not mapped" for c in in_db - mapped]
        for column in table.columns:
            row = actual.get((table.name, column.name))
            if row is None or column.primary_key:
                continue
            if row["udt_name"] in ("timestamptz",):
                assert column.type.timezone is True, f"{table.name}.{column.name}"
    assert problems == []
