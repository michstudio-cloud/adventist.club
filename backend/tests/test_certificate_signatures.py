"""021 — The handwritten signatures stay on the issued certificate (docs/CERTIFICADOS_V4.md «Firmas»).

Issuing with an account keeps an immutable copy (`certificates/signatures/<id>/`); downloading the
folio again prints it whoever asks; a URL that is not the issuer's own saved signature is refused;
the open tool without a session keeps nothing; revoking or forgetting keeps the copies.
The public media bucket is always a fake: no test reaches the network.
"""
import base64
import io
import re
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import storage
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db
from tests.test_portfolio import ENROLLMENTS, _complete_all, _enroll, _honor

pytestmark = requires_db
factory = module_factory("certsig")

RENDER = "/api/v1/certificates/render"
BATCH = "/api/v1/certificates/prototype-batch"
SIGNATURE = "/api/v1/users/me/signature"
TEMPLATE = "especialidad-editorial-rojo"          # has both signature slots
MEDIA = "https://media.test"
COPY_URL = re.compile(rf"^{re.escape(MEDIA)}/certificates/signatures/([0-9a-f-]{{36}})/[0-9a-f]{{32}}\.png$")


def _png(width=600, height=160, shade=0) -> bytes:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = image.load()
    for x in range(20, width - 20):
        y = int(height / 2 + (height / 3) * ((x % 90) - 45) / 45)
        for dy in range(-3, 4):
            pixels[x, max(0, min(height - 1, y + dy))] = (shade, shade, shade, 255)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _data_url(data: bytes, mime="image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class FakeR2:
    """The public media bucket: put, get and delete, with every call recorded."""

    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.puts: list[str] = []
        self.deleted: list[str] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs["Key"])
        self.objects[kwargs["Key"]] = kwargs

    def get_object(self, Bucket, Key):
        stored = self.objects[Key]                     # KeyError = missing object
        return {"Body": io.BytesIO(stored["Body"]), "ContentType": stored["ContentType"]}

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs["Key"])
        self.objects.pop(kwargs["Key"], None)

    def body(self, url: str) -> bytes:
        return self.objects[url.removeprefix(f"{MEDIA}/")]["Body"]


@pytest.fixture
def r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", f"{MEDIA}/")
    monkeypatch.setattr(storage, "get_client", lambda: fake)
    return fake


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    issuer = await factory.org("issuer", "association")
    issuer_code = f"{factory.prefix}-ISS"
    people = {
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "master": await factory.user("master", "MASTER_GC"),
    }
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET code = :code WHERE id = :id"),
                         {"code": issuer_code, "id": uuid.UUID(issuer["id"])})
        await db.commit()
    return {**people, "club": club, "issuer_code": issuer_code}


@pytest.fixture
def issuer(world, monkeypatch):
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


async def _ready_enrollment(client, factory, world, label: str) -> str:
    """A member of the club with every requirement of a fresh honor completed (READY)."""
    member = await factory.user(f"member-{label}", "STUDENT", world["club"]["id"])
    honor = await _honor(factory, f"honor-{label}")
    enrollment = await _enroll(client, member, honor)
    await _complete_all(client, member, world["director"], enrollment)
    return enrollment["id"]


async def _issue(client, user, enrollment_id, **signatures):
    body = {"template": TEMPLATE, "issued_date": "2026-09-24", "instructor_name": "Instructora Uno", **signatures}
    return await client.post(f"{ENROLLMENTS}/{enrollment_id}/certificate", json=body, headers=user["headers"])


async def _row(certificate_no: str):
    return await fetch_one(
        "SELECT id::text AS id, status, signature_director_url, signature_instructor_url FROM certificates"
        " WHERE certificate_no = :no", no=certificate_no)


async def _render(client, certificate_no: str | None, images: dict | None = None, data: dict | None = None):
    body = {"template": TEMPLATE, "format": "svg", "images": images or {},
            "data": data or {"recipient_name": "Ana", "honor_name": "Nudos", "issued_date": "2026-09-24"}}
    if certificate_no:
        body["certificate_no"] = certificate_no
    response = await client.post(RENDER, json=body)
    assert response.status_code == 200, response.text
    return response.text


def _in_svg(svg: str, png: bytes) -> bool:
    return base64.b64encode(png).decode()[:400] in svg


@requires_db
async def test_a_drawn_signature_is_kept_and_printed_again_from_the_folio(client, factory, world, issuer, r2):
    enrollment_id = await _ready_enrollment(client, factory, world, "drawn")
    issued = await _issue(client, world["director"], enrollment_id,
                          signature_director=_data_url(_png()), signature_instructor=_data_url(_png(500, 140, 40), "image/png"))
    assert issued.status_code == 201, issued.text
    certificate = issued.json()
    assert certificate["signed"] == ["signature_director", "signature_instructor"]

    row = await _row(certificate["certificate_no"])
    for column in ("signature_director_url", "signature_instructor_url"):
        match = COPY_URL.match(row[column])
        assert match and match.group(1) == row["id"]                          # the certificate's own folder
        assert r2.body(row[column]).startswith(b"\x89PNG")                    # normalized PNG
    assert row["signature_director_url"] != row["signature_instructor_url"]
    audit = await fetch_one("SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'CERTIFICATE_ISSUE'",
                            id=certificate["id"])
    assert audit["metadata_json"]["signed"] == ["signature_director", "signature_instructor"]

    # Downloaded again (portfolio, /verify): nothing sent, both signatures printed from the record.
    director_png, instructor_png = r2.body(row["signature_director_url"]), r2.body(row["signature_instructor_url"])
    svg = await _render(client, certificate["certificate_no"])
    assert _in_svg(svg, director_png) and _in_svg(svg, instructor_png)
    # ...and whatever a caller sends does not replace them.
    other = _png(300, 90, 90)
    svg = await _render(client, certificate["certificate_no"], {"signature_director": _data_url(other)})
    assert _in_svg(svg, director_png) and "signature_director" in svg
    assert base64.b64encode(other).decode()[:200] not in svg


@requires_db
async def test_the_saved_signature_is_copied_and_survives_being_forgotten(client, factory, world, issuer, r2):
    director = world["director"]
    saved = await client.post(SIGNATURE, files={"file": ("f.png", _png(), "image/png")}, headers=director["headers"])
    assert saved.status_code == 200, saved.text
    saved_url = saved.json()["signature_url"]
    enrollment_id = await _ready_enrollment(client, factory, world, "saved")
    issued = await _issue(client, director, enrollment_id, signature_director=saved_url)
    assert issued.status_code == 201, issued.text
    row = await _row(issued.json()["certificate_no"])
    assert COPY_URL.match(row["signature_director_url"]) and row["signature_director_url"] != saved_url
    assert row["signature_instructor_url"] is None and issued.json()["signed"] == ["signature_director"]
    copy = r2.body(row["signature_director_url"])
    assert copy == r2.body(saved_url)                         # same picture, its own object

    # The owner forgets their saved signature: the certificate keeps its copy.
    assert (await client.delete(SIGNATURE, headers=director["headers"])).status_code == 200
    assert saved_url.removeprefix(f"{MEDIA}/") in r2.deleted
    assert not any(key.startswith("certificates/") for key in r2.deleted)
    svg = await _render(client, issued.json()["certificate_no"])
    assert _in_svg(svg, copy)


@requires_db
async def test_only_a_picture_or_the_issuers_own_saved_signature_is_accepted(client, factory, world, issuer, r2):
    instructor = world["instructor"]
    other = await client.post(SIGNATURE, files={"file": ("f.png", _png(), "image/png")}, headers=instructor["headers"])
    assert other.status_code == 200
    enrollment_id = await _ready_enrollment(client, factory, world, "refused")
    before = len(r2.puts)
    svg = _data_url(b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml")
    for value in (
        other.json()["signature_url"],                        # somebody else's saved signature
        "https://evil.example/firma.png",                     # another host
        f"{MEDIA}/patches/{uuid.uuid4().hex}.png",            # our bucket, but not a saved signature
        svg,                                                   # never SVG
        "firma",
    ):
        refused = await _issue(client, world["director"], enrollment_id, signature_director=value)
        assert refused.status_code == 422, (value, refused.text)
        assert "signature_director" in refused.json()["detail"]
    assert len(r2.puts) == before                              # nothing uploaded
    state = await client.get(f"{ENROLLMENTS}/{enrollment_id}", headers=world["director"]["headers"])
    assert state.json()["status"] == "READY"                   # and nothing issued


@requires_db
async def test_without_a_bucket_a_signed_certificate_is_not_issued(client, factory, world, issuer):
    enrollment_id = await _ready_enrollment(client, factory, world, "no-r2")
    refused = await _issue(client, world["director"], enrollment_id, signature_director=_data_url(_png()))
    assert refused.status_code == 503
    state = await client.get(f"{ENROLLMENTS}/{enrollment_id}", headers=world["director"]["headers"])
    assert state.json()["status"] == "READY"
    unsigned = await _issue(client, world["director"], enrollment_id)       # without a signature, as before
    assert unsigned.status_code == 201 and unsigned.json()["signed"] == []


@requires_db
async def test_an_official_certificate_without_signature_prints_none_from_the_caller(client, factory, world, issuer, r2):
    enrollment_id = await _ready_enrollment(client, factory, world, "unsigned")
    issued = await _issue(client, world["director"], enrollment_id)
    assert issued.status_code == 201
    stranger = _png(400, 100, 70)
    svg = await _render(client, issued.json()["certificate_no"], {"signature_director": _data_url(stranger)})
    assert base64.b64encode(stranger).decode()[:200] not in svg
    assert re.search(r'<image[^>]*id="signature_director"[^>]*opacity="0"', svg)


@requires_db
async def test_revoking_keeps_the_signatures_but_prints_none(client, factory, world, issuer, r2):
    enrollment_id = await _ready_enrollment(client, factory, world, "revoked")
    issued = await _issue(client, world["director"], enrollment_id, signature_director=_data_url(_png()))
    certificate = issued.json()
    revoked = await client.post(f"/api/v1/portfolio/certificates/{certificate['id']}/revoke",
                                json={"reason": "Emitido por error"}, headers=world["master"]["headers"])
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["signed"] == ["signature_director"]
    row = await _row(certificate["certificate_no"])
    assert row["status"] == "revoked" and COPY_URL.match(row["signature_director_url"])
    assert r2.deleted == [] and row["signature_director_url"].removeprefix(f"{MEDIA}/") in r2.objects
    svg = await _render(client, certificate["certificate_no"], {"signature_director": _data_url(_png())})
    assert re.search(r'<image[^>]*id="signature_director"[^>]*opacity="0"', svg)


def _batch(factory, names: list[str], **extra) -> dict:
    return {"recipient_names": names, "honor_name": factory.name("Lote"), "club_name": factory.name("club-lote"),
            "issued_date": "2026-09-24", "instructor_name": "I", "director_name": "D", "width_in": 11, "height_in": 8.5,
            **extra}


@requires_db
async def test_the_open_tool_without_a_session_keeps_nothing(client, factory, world, issuer, r2):
    signature = _data_url(_png())
    response = await client.post(BATCH, json=_batch(factory, [factory.name("Anon")], signature_director=signature))
    assert response.status_code == 201, response.text
    number = response.json()[0]["certificate_no"]
    row = await _row(number)
    assert row["signature_director_url"] is None and row["signature_instructor_url"] is None
    assert r2.puts == []
    # the signature lives only in each render, as before 021
    sent = _png(420, 110, 30)
    svg = await _render(client, number, {"signature_director": _data_url(sent)})
    assert _in_svg(svg, sent)


@requires_db
async def test_the_assistant_with_a_session_keeps_one_copy_for_the_batch(client, factory, world, issuer, r2):
    names = [factory.name("Uno"), factory.name("Dos"), factory.name("Tres")]
    response = await client.post(BATCH, json=_batch(factory, names, signature_instructor=_data_url(_png())),
                                 headers=world["instructor"]["headers"])
    assert response.status_code == 201, response.text
    rows = [await _row(item["certificate_no"]) for item in response.json()]
    urls = {row["signature_instructor_url"] for row in rows}
    assert len(urls) == 1 and COPY_URL.match(urls.pop()).group(1) == rows[0]["id"]
    assert all(row["signature_director_url"] is None for row in rows)
    assert len([key for key in r2.puts if key.startswith("certificates/")]) == 1
    events = await fetch_all("SELECT metadata_json FROM certificate_events WHERE certificate_id = :id",
                             id=uuid.UUID(rows[1]["id"]))
    assert events[0]["metadata_json"]["signed_by"] == world["instructor"]["id"]
    # the record decides: its copy is printed, and the line it left unsigned stays unsigned
    copy = r2.body(rows[2]["signature_instructor_url"])
    svg = await _render(client, response.json()[2]["certificate_no"], {"signature_director": _data_url(_png(300, 80, 60))})
    assert _in_svg(svg, copy)
    assert re.search(r'<image[^>]*id="signature_director"[^>]*opacity="0"', svg)
    # an invalid signature is a 422 before anything is issued
    refused = await client.post(BATCH, json=_batch(factory, [factory.name("Cuatro")], signature_director="https://evil.example/s.png"),
                                headers=world["instructor"]["headers"])
    assert refused.status_code == 422
    assert await fetch_one("SELECT 1 AS x FROM certificates WHERE recipient_name = :n", n=factory.name("Cuatro")) is None


@requires_db
async def test_the_client_upload_cannot_write_into_internal_folders(client, factory, world, r2):
    """`POST /media/upload` resolves the folder BEFORE building the key: `signatures` or a
    certificate's signature folder fall back to `general`."""
    for folder in ("signatures", f"certificates/signatures/{uuid.uuid4()}"):
        response = await client.post("/api/v1/media/upload", data={"folder": folder},
                                     files={"file": ("x.png", _png(), "image/png")}, headers=world["master"]["headers"])
        assert response.status_code == 201, response.text
        assert response.json()["key"].startswith("general/")


def test_behind_the_web_proxy_the_batch_limit_counts_per_account():
    """The assistant with a session reaches prototype-batch through the proxy (one address for
    everybody): a valid access token is its own bucket, anything else counts per address."""
    from starlette.requests import Request

    from app.rate_limit import account_or_ip
    from tests.conftest import auth_headers

    def request(headers: dict) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw, "client": ("10.0.0.1", 1)})

    proxy = {"X-Forwarded-For": "203.0.113.9"}
    one, two = uuid.uuid4(), uuid.uuid4()
    assert account_or_ip(request({**proxy, **auth_headers(one)})) == f"account:{one}"
    assert account_or_ip(request({**proxy, **auth_headers(two)})) == f"account:{two}"
    assert account_or_ip(request({**proxy, "Authorization": "Bearer nope"})) == "203.0.113.9"
    assert account_or_ip(request(proxy)) == "203.0.113.9"
