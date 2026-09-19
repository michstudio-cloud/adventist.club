"""Media upload: role gate, 503 without R2, MIME/size/SVG validation."""

import re
import threading

import pytest

from app.config import settings
from app.services import storage
from tests.conftest import module_factory, requires_db

pytestmark = requires_db
factory = module_factory("media")

UPLOAD = "/api/v1/media/upload"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>'


class FakeR2:
    def __init__(self):
        self.objects = []
        self.threads = []

    def put_object(self, **kwargs):
        self.threads.append(threading.current_thread())
        self.objects.append(kwargs)


@pytest.fixture
def r2(monkeypatch):
    """R2 "configured", with the boto3 client replaced by an in-memory fake."""
    fake = FakeR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "https://media.test/")
    monkeypatch.setattr(storage, "get_client", lambda: fake)
    return fake


async def _upload(client, user, data, content_type, folder=None, filename="file.bin"):
    return await client.post(
        UPLOAD,
        files={"file": (filename, data, content_type)},
        data={"folder": folder} if folder else None,
        headers=user["headers"] if user else None,
    )


async def test_503_when_r2_is_not_configured(client, factory):
    instructor = await factory.user("no-r2", role="INSTRUCTOR")
    response = await _upload(client, instructor, PNG, "image/png")
    assert response.status_code == 503
    assert response.json() == {"detail": "Almacenamiento no configurado"}


async def test_role_gate(client, factory, r2):
    assert (await _upload(client, None, PNG, "image/png")).status_code == 401
    for role in ("STUDENT", "PARENT_GUARDIAN", "CLUB_DIRECTOR"):
        user = await factory.user(f"gate-{role.lower()}", role=role)
        assert (await _upload(client, user, PNG, "image/png")).status_code == 403, role
    assert r2.objects == []


async def test_upload_returns_url_and_key(client, factory, r2):
    instructor = await factory.user("uploader", role="INSTRUCTOR")
    response = await _upload(
        client, instructor, PNG, "image/png", folder="patches", filename="../../evil.php"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert re.fullmatch(r"patches/[0-9a-f]{32}\.png", body["key"])
    assert body["url"] == f"https://media.test/{body['key']}"
    assert body["content_type"] == "image/png" and body["size_bytes"] == len(PNG)

    stored = r2.objects[0]
    assert stored["Bucket"] == "test-bucket" and stored["Key"] == body["key"]
    assert stored["Body"] == PNG and stored["ContentType"] == "image/png"
    # The blocking SDK call ran off the event loop thread.
    assert r2.threads[0] is not threading.main_thread()


async def test_folder_whitelist(client, factory, r2):
    instructor = await factory.user("folders", role="INSTRUCTOR")
    for folder in ("specialties", "patches", "general", "resources", "avatars", "logos"):
        response = await _upload(client, instructor, PNG, "image/png", folder=folder)
        assert response.json()["key"].startswith(f"{folder}/"), folder
    for folder in ("../secrets", "private", "patches/../../x", "/etc"):
        response = await _upload(client, instructor, PNG, "image/png", folder=folder)
        assert response.status_code == 201
        assert re.fullmatch(r"general/[0-9a-f]{32}\.png", response.json()["key"]), folder
    default = await _upload(client, instructor, PDF, "application/pdf")
    assert re.fullmatch(r"general/[0-9a-f]{32}\.pdf", default.json()["key"])


async def test_mime_whitelist_and_magic_numbers(client, factory, r2):
    instructor = await factory.user("mime", role="INSTRUCTOR")
    for content_type in ("text/html", "application/zip", "image/bmp", "application/javascript"):
        response = await _upload(client, instructor, b"<html></html>", content_type)
        assert response.status_code == 415, content_type
    # The declared type must match the bytes.
    assert (await _upload(client, instructor, b"<html></html>", "image/png")).status_code == 415
    assert (await _upload(client, instructor, PNG, "application/pdf")).status_code == 415
    assert (await _upload(client, instructor, b"", "image/png")).status_code == 400
    assert r2.objects == []
    for data, content_type in ((JPEG, "image/jpeg"), (PDF, "application/pdf")):
        assert (await _upload(client, instructor, data, content_type)).status_code == 201


async def test_size_limits(client, factory, r2):
    instructor = await factory.user("size", role="INSTRUCTOR")
    exactly = PNG + b"\x00" * (storage.MAX_IMAGE_SIZE_BYTES - len(PNG))
    assert (await _upload(client, instructor, exactly, "image/png")).status_code == 201
    assert (await _upload(client, instructor, exactly + b"\x00", "image/png")).status_code == 413

    assert storage.MAX_DOCUMENT_SIZE_BYTES == 50 * 1024 * 1024
    # A PDF may be bigger than an image ...
    medium = PDF + b"\x00" * storage.MAX_IMAGE_SIZE_BYTES
    assert (await _upload(client, instructor, medium, "application/pdf")).status_code == 201
    # ... but not bigger than 50 MB.
    huge = PDF + b"\x00" * storage.MAX_DOCUMENT_SIZE_BYTES
    assert (await _upload(client, instructor, huge, "application/pdf")).status_code == 413
    assert len(r2.objects) == 2


async def test_svg_only_for_master_gc_in_patches_or_logos(client, factory, r2):
    master = await factory.user("svg-master", role="MASTER_GC")
    admin = await factory.user("svg-admin", role="ADMIN_DIVISION")

    assert (await _upload(client, admin, SVG, "image/svg+xml", folder="logos")).status_code == 403
    for folder in (None, "general", "avatars", "specialties", "nope"):
        response = await _upload(client, master, SVG, "image/svg+xml", folder=folder)
        assert response.status_code == 400, folder
    assert r2.objects == []

    for folder in ("patches", "logos"):
        response = await _upload(client, master, SVG, "image/svg+xml", folder=folder)
        assert response.status_code == 201, response.text
        assert re.fullmatch(rf"{folder}/[0-9a-f]{{32}}\.svg", response.json()["key"])
    assert r2.objects[-1]["ContentType"] == "image/svg+xml"
    assert (await _upload(client, master, PNG, "image/svg+xml", folder="logos")).status_code == 415


@pytest.mark.parametrize(
    "payload",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><SCRIPT href="x.js"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect OnClick = "x()"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)">x</a></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href=" JaVaScRiPt:alert(1)">x</a></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="&#106;avascript:alert(1)">x</a></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="java&#10;script:alert(1)">x</a></svg>',
        "<svg xmlns='http://www.w3.org/2000/svg' onload='x()'/>".encode("utf-16"),
    ],
)
async def test_unsafe_svg_is_rejected(client, factory, r2, payload):
    master = await factory.user(f"svg-{abs(hash(payload))}", role="MASTER_GC")
    response = await _upload(client, master, payload, "image/svg+xml", folder="logos")
    assert response.status_code in (415, 422), response.text
    assert r2.objects == []


def test_svg_sanitiser_accepts_ordinary_markup():
    assert storage.svg_is_safe(SVG)
    assert storage.svg_is_safe(
        b'<svg xmlns="http://www.w3.org/2000/svg"><text font-family="Lato">Conquistadores</text></svg>'
    )


def test_client_is_cached_and_needs_configuration():
    storage.get_client.cache_clear()
    with pytest.raises(storage.StorageNotConfigured):
        storage.get_client()


def test_boto3_client_is_created_once(monkeypatch):
    import boto3

    calls = []

    def fake_client(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(boto3, "client", fake_client)
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    storage.get_client.cache_clear()
    try:
        assert storage.get_client() is storage.get_client()
        assert len(calls) == 1
        assert calls[0][1]["endpoint_url"] == "https://acct.r2.cloudflarestorage.com"
    finally:
        storage.get_client.cache_clear()
