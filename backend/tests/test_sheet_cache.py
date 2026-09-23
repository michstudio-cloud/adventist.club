"""Honor PDFs stored lazily in the public R2 bucket: rendered once, then served by the CDN."""
import io
import re
import uuid

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.routers import honor_sheets
from app.services import sheet_cache, storage
from tests.conftest import module_factory, requires_db
from tests.test_honor_sheet import _honor

factory = module_factory("hcache")
HONORS = "/api/v1/honors"
KEY = re.compile(r"sheets/[0-9a-f-]{36}/(hoja|ficha)-[a-z0-9-]+-(a4|letter)-[0-9a-f]{12}\.pdf")


def _not_found(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"},
                        "ResponseMetadata": {"HTTPStatusCode": 404}}, operation)


class FakeR2:
    """In-memory public bucket. `broken = True` makes every call fail like a network error."""

    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.broken = False

    def _call(self, name: str, key: str) -> None:
        self.calls.append((name, key))
        if self.broken:
            raise ConnectionError("R2 unreachable")

    def count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)

    def put_object(self, **kwargs):
        self._call("put", kwargs["Key"])
        self.objects[kwargs["Key"]] = kwargs

    def head_object(self, Bucket, Key):
        self._call("head", Key)
        if Key not in self.objects:
            raise _not_found("HeadObject")
        return {"ContentLength": len(self.objects[Key]["Body"]), "ContentType": "application/pdf"}

    def get_object(self, Bucket, Key):
        self._call("get", Key)
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                              "GetObject")
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        self._call("list", Prefix)
        return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)],
                "IsTruncated": False}

    def delete_object(self, Bucket, Key):
        self._call("delete", Key)
        self.objects.pop(Key, None)


@pytest.fixture
def r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "https://media.test/")
    monkeypatch.setattr(storage, "get_client", lambda: fake)
    sheet_cache.forget_all()
    yield fake
    sheet_cache.forget_all()


@pytest.fixture
def renders(monkeypatch):
    """Counts the renders the endpoint performs (the real renderer still runs)."""
    calls = []
    real = honor_sheets.render_sheet_pdf

    def counting(*args, **kwargs):
        calls.append(kwargs.get("mode"))
        return real(*args, **kwargs)

    monkeypatch.setattr(honor_sheets, "render_sheet_pdf", counting)
    return calls


REQUIREMENTS = [("es", "Explicar qué es la arcilla.", True, None)]


# ------------------------------------------------------------------ pure helpers


def test_key_format_uses_the_first_12_hex_of_the_etag():
    honor_id = uuid.uuid4()
    etag = '"' + "ab12cd34ef56" + "0" * 52 + '"'
    key = sheet_cache.sheet_key(honor_id, "hoja", "en-US", "letter", etag)
    assert key == f"sheets/{honor_id}/hoja-en-us-letter-ab12cd34ef56.pdf"
    assert KEY.fullmatch(key)


def test_sheets_folder_is_not_a_client_upload_folder():
    assert sheet_cache.SHEETS_FOLDER == "sheets"
    assert storage.resolve_folder("sheets") == storage.DEFAULT_FOLDER


# ------------------------------------------------------------------ endpoint


@requires_db
async def test_first_request_renders_and_uploads_then_redirects_to_the_cdn(client, factory, r2, renders):
    honor = await _honor(factory, "lazy", requirements=REQUIREMENTS)
    url = f"{HONORS}/{honor['id']}/sheet.pdf"

    first = await client.get(url)
    assert first.status_code == 200 and first.content.startswith(b"%PDF")
    assert first.headers["content-type"] == "application/pdf"
    assert first.headers["content-disposition"] == f'inline; filename="{honor["slug"]}.pdf"'
    assert renders == ["hoja"]

    assert len(r2.objects) == 1
    key, stored = next(iter(r2.objects.items()))
    etag12 = first.headers["etag"].strip('"')[:12]
    assert key == f"sheets/{honor['id']}/hoja-es-a4-{etag12}.pdf" and KEY.fullmatch(key)
    assert stored["Bucket"] == "test-bucket" and stored["Body"] == first.content
    assert stored["ContentType"] == "application/pdf"
    assert stored["CacheControl"] == "public, max-age=31536000, immutable"
    assert stored["ContentDisposition"] == f'inline; filename="{honor["slug"]}.pdf"'

    second = await client.get(url)
    assert second.status_code == 302
    assert second.headers["location"] == f"https://media.test/{key}"
    assert second.headers["cache-control"] == "public, max-age=3600"
    assert second.headers["etag"] == first.headers["etag"]
    assert renders == ["hoja"]                                         # no second render

    # The HEAD answer is remembered in-process: a third request does not touch R2 at all.
    calls = len(r2.calls)
    third = await client.get(url)
    assert third.status_code == 302 and len(r2.calls) == calls

    # A conditional request is still answered before any storage work.
    assert (await client.get(url, headers={"If-None-Match": first.headers["etag"]})).status_code == 304
    assert len(r2.calls) == calls


@requires_db
async def test_object_already_in_the_bucket_is_found_with_head(client, factory, r2, renders):
    """Another instance (or the manual warm script) uploaded it: HEAD finds it, no render."""
    honor = await _honor(factory, "otra-instancia", requirements=REQUIREMENTS)
    url = f"{HONORS}/{honor['id']}/sheet.pdf"
    await client.get(url)
    sheet_cache.forget_all()                                           # a fresh process
    renders.clear()

    response = await client.get(url)
    assert response.status_code == 302 and renders == []
    assert r2.count("head") >= 1


@requires_db
async def test_content_change_uploads_a_new_key_and_purges_the_stale_one(client, factory, r2):
    honor = await _honor(factory, "cambia", requirements=[("es", "Texto original.", True, None)])
    other = await _honor(factory, "vecina", requirements=REQUIREMENTS)
    url = f"{HONORS}/{honor['id']}/sheet.pdf"

    await client.get(url)
    await client.get(url, params={"modo": "ficha"})
    await client.get(f"{HONORS}/{other['id']}/sheet.pdf")
    old_hoja = next(k for k in r2.objects if k.startswith(f"sheets/{honor['id']}/hoja-"))
    ficha = next(k for k in r2.objects if k.startswith(f"sheets/{honor['id']}/ficha-"))
    neighbour = next(k for k in r2.objects if k.startswith(f"sheets/{other['id']}/"))

    async with SessionLocal() as db:
        await db.execute(text("UPDATE honor_requirements SET description = 'Texto corregido.' WHERE honor_id = :id"),
                         {"id": uuid.UUID(honor["id"])})
        await db.commit()

    edited = await client.get(url)
    assert edited.status_code == 200 and edited.content.startswith(b"%PDF")   # new content: rendered
    new_hoja = f"sheets/{honor['id']}/hoja-es-a4-{edited.headers['etag'].strip(chr(34))[:12]}.pdf"
    assert new_hoja != old_hoja and new_hoja in r2.objects
    assert old_hoja not in r2.objects                                  # only the current version stays
    assert ficha in r2.objects and neighbour in r2.objects             # other variants/honors untouched

    again = await client.get(url)
    assert again.status_code == 302 and again.headers["location"] == f"https://media.test/{new_hoja}"


@requires_db
async def test_storage_failures_never_break_the_response(client, factory, r2, renders):
    honor = await _honor(factory, "r2-caido", requirements=REQUIREMENTS)
    url = f"{HONORS}/{honor['id']}/sheet.pdf"
    r2.broken = True

    for params in ({}, {"download": 1}):
        response = await client.get(url, params=params)
        assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert r2.objects == {} and len(renders) == 2

    # Upload succeeded but the purge listing fails: still fine, and the object is kept.
    r2.broken = False
    real_list = r2.list_objects_v2

    def failing_list(**kwargs):
        raise ConnectionError("listing failed")

    r2.list_objects_v2 = failing_list
    assert (await client.get(url, params={"modo": "ficha"})).status_code == 200
    assert any(k.startswith(f"sheets/{honor['id']}/ficha-") for k in r2.objects)
    r2.list_objects_v2 = real_list


@requires_db
async def test_drafts_are_never_uploaded(client, factory, r2):
    author = await factory.user("autor", "INSTRUCTOR")
    draft = await _honor(factory, "borrador", status="DRAFT", created_by=author["id"], requirements=REQUIREMENTS)
    url = f"{HONORS}/{draft['id']}/sheet.pdf"
    for params in ({}, {"download": 1}, {}):
        response = await client.get(url, params=params, headers=author["headers"])
        assert response.status_code == 200 and response.content.startswith(b"%PDF")
        assert response.headers["cache-control"] == "private, no-store"
    assert r2.objects == {} and r2.calls == []


@requires_db
async def test_download_is_served_from_the_stored_copy_as_an_attachment(client, factory, r2, renders):
    """The public custom domain ignores `response-content-disposition`, so downloads are the one case
    the API streams the stored bytes itself (no render) with its own attachment header."""
    honor = await _honor(factory, "descarga", requirements=REQUIREMENTS)
    url = f"{HONORS}/{honor['id']}/sheet.pdf"

    first = await client.get(url, params={"download": 1})
    assert first.status_code == 200
    assert first.headers["content-disposition"] == f'attachment; filename="{honor["slug"]}.pdf"'
    assert len(r2.objects) == 1 and renders == ["hoja"]

    second = await client.get(url, params={"download": 1})
    assert second.status_code == 200 and second.content == first.content
    assert second.headers["content-disposition"] == f'attachment; filename="{honor["slug"]}.pdf"'
    assert second.headers["content-type"] == "application/pdf"
    assert second.headers["cache-control"] == "public, max-age=3600"
    assert renders == ["hoja"] and r2.count("get") == 2                # miss on the first, hit on the second

    # The same stored object also serves the inline view through the CDN.
    view = await client.get(url)
    assert view.status_code == 302 and renders == ["hoja"]


@requires_db
async def test_without_r2_nothing_changes(client, factory, renders, monkeypatch):
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", None)
    honor = await _honor(factory, "sin-r2", requirements=REQUIREMENTS)
    url = f"{HONORS}/{honor['id']}/sheet.pdf"
    for _ in range(2):
        response = await client.get(url)
        assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert len(renders) == 2


# ------------------------------------------------------------------ manual warm tool


@requires_db
async def test_warm_sheet_uploads_missing_and_skips_stored(factory, r2):
    from app.models import Honor

    created = await _honor(factory, "precalentar", requirements=REQUIREMENTS)
    async with SessionLocal() as db:
        honor = await db.get(Honor, uuid.UUID(created["id"]))
        assert await sheet_cache.warm_sheet(db, honor, mode="hoja") == "uploaded"
        assert await sheet_cache.warm_sheet(db, honor, mode="hoja") == "cached"
        assert await sheet_cache.warm_sheet(db, honor, mode="ficha") == "uploaded"
    assert sorted(k.split("/")[-1].split("-")[0] for k in r2.objects) == ["ficha", "hoja"]
    assert all(KEY.fullmatch(k) for k in r2.objects)


def _script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "render_honor_sheets.py"
    spec = importlib.util.spec_from_file_location("render_honor_sheets_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_db
async def test_warm_script_is_a_dry_run_unless_apply(factory, r2, capsys):
    from app.models import Honor

    script = _script()
    created = await _honor(factory, "script", requirements=REQUIREMENTS)
    async with SessionLocal() as db:
        honor = await db.get(Honor, uuid.UUID(created["id"]))
        assert await script.warm(db, [honor], ("hoja", "ficha"), "es", "a4", apply=False) == {"dry-run": 2}
        assert r2.calls == [] and "dry-run" in capsys.readouterr().out
        assert await script.warm(db, [honor], ("hoja",), "es", "a4", apply=True) == {"uploaded": 1}
    assert len(r2.objects) == 1
