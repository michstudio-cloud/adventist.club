"""MapKit JS tokens: 503 without the Apple settings; ES256 with kid/iss/exp; origin binding."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import settings

URL = "/api/v1/maps/apple-token"


@pytest.fixture
def apple_key(monkeypatch):
    """Apple Maps "configured" with a throwaway P-256 key; returns its public key for verification."""
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(settings, "APPLE_MAPKIT_TEAM_ID", "TEAM123456")
    monkeypatch.setattr(settings, "APPLE_MAPKIT_KEY_ID", "KEYID12345")
    # As a hosting dashboard would flatten it: one line, literal "\n".
    monkeypatch.setattr(settings, "APPLE_MAPKIT_PRIVATE_KEY", pem.replace("\n", "\\n"))
    monkeypatch.setattr(settings, "APPLE_MAPKIT_TOKEN_MINUTES", 30)
    return private.public_key()


async def test_503_without_apple_settings(client, monkeypatch):
    monkeypatch.setattr(settings, "APPLE_MAPKIT_TEAM_ID", None)
    monkeypatch.setattr(settings, "APPLE_MAPKIT_KEY_ID", None)
    monkeypatch.setattr(settings, "APPLE_MAPKIT_PRIVATE_KEY", None)
    response = await client.get(URL)
    assert response.status_code == 503
    assert response.json() == {"detail": "apple_maps_not_configured"}


async def test_token_is_es256_signed_with_kid_and_bound_to_an_allowed_origin(client, apple_key):
    response = await client.get(URL, params={"origin": "https://www.conquistadores.app"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["expires_in"] == 30 * 60

    assert jwt.get_unverified_header(body["token"]) == {"alg": "ES256", "typ": "JWT", "kid": "KEYID12345"}
    claims = jwt.decode(body["token"], apple_key, algorithms=["ES256"], issuer="TEAM123456")
    assert claims["origin"] == "https://www.conquistadores.app"
    assert claims["exp"] - claims["iat"] == 30 * 60
    assert abs(claims["iat"] - time.time()) < 30


@pytest.mark.parametrize(
    "origin",
    [None, "https://evil.example", "conquistadores.app", "javascript:alert(1)", "https://conquistadores.app.evil.example"],
)
async def test_unknown_origin_gets_an_unbound_token(client, apple_key, origin):
    params = {"origin": origin} if origin is not None else None
    response = await client.get(URL, params=params)
    assert response.status_code == 200
    claims = jwt.decode(response.json()["token"], apple_key, algorithms=["ES256"], issuer="TEAM123456")
    assert "origin" not in claims


async def test_origin_match_ignores_case_path_and_trailing_slash(client, apple_key):
    response = await client.get(URL, params={"origin": "HTTPS://Conquistadores.app/clubs?x=1"})
    claims = jwt.decode(response.json()["token"], apple_key, algorithms=["ES256"], issuer="TEAM123456")
    assert claims["origin"] == "https://conquistadores.app"


async def test_unreadable_key_answers_503_not_500(client, apple_key, monkeypatch):
    monkeypatch.setattr(settings, "APPLE_MAPKIT_PRIVATE_KEY", "not a pem")
    response = await client.get(URL)
    assert response.status_code == 503
    assert response.json() == {"detail": "apple_maps_not_configured"}
