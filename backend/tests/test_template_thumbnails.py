"""GET /certificates/templates/{slug}/thumbnail.png: the picker's sample, rendered once and cached."""
import asyncio
import re

import pytest

from app.config import settings
from app.routers import template_thumbnails as thumbs
from app.services import sheet_cache, storage
from tests.test_sheet_cache import FakeR2

URL = "/api/v1/certificates/templates/{slug}/thumbnail.png"
KEY = re.compile(r"thumbs/especialidad-color/es-288-[0-9a-f]{12}\.png")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    thumbs.forget_all()
    sheet_cache.forget_all()
    monkeypatch.setattr(thumbs, "fonts_installed", lambda: True)
    yield
    thumbs.forget_all()


@pytest.fixture
def renders(monkeypatch):
    """Counts the real renders (and still renders, so the PNG is real)."""
    calls: list[tuple[str, str, float]] = []
    real = thumbs.render_certificate

    def counting(slug, data, images, **kwargs):
        calls.append((slug, kwargs["locale"], kwargs["dpi"]))
        assert "qr" not in images and not any(k.startswith("signature_") for k in images)
        return real(slug, data, images, **kwargs)

    monkeypatch.setattr(thumbs, "render_certificate", counting)
    return calls


@pytest.fixture
def r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "https://media.test/")
    monkeypatch.setattr(storage, "get_client", lambda: fake)
    yield fake


def test_sample_data_follows_the_template_and_language():
    data = thumbs.sample_data(["recipient_name", "honor_name", "instructor_name"], "en")
    assert data["recipient_name"] == "María López" and data["club_name"] == "Club Orión"
    assert data["honor_name"] == "Camping Skills I"
    assert data["issued_date"] == "September 21, 2026"
    assert data["instructor_name"] == "Luis Gómez" and "coordinator_name" not in data
    split = thumbs.sample_data(["coordinator_name", "issued_day"], "es")
    assert split["coordinator_name"] == "Luis Gómez"
    assert (split["issued_day"], split["issued_month"], split["issued_year"]) == ("21", "09", "2026")
    assert "issued_date" not in split
    assert thumbs._sample_images()["honor_patch"].startswith("data:image/webp;base64,")


def test_etag_changes_with_locale_and_width():
    tags = {thumbs.thumb_etag("especialidad-color", locale, w) for locale in ("es", "en") for w in thumbs.THUMB_WIDTHS}
    assert len(tags) == 6
    assert thumbs.thumb_etag("especialidad-color", "es", 288) == thumbs.thumb_etag("especialidad-color", "es", 288)
    assert KEY.fullmatch(thumbs.thumb_key("especialidad-color", "es", 288, thumbs.thumb_etag("especialidad-color", "es", 288)))


@pytest.mark.asyncio
async def test_thumbnail_ok_not_found_and_invalid_width(client, renders):
    ok = await client.get(URL.format(slug="especialidad-color"), params={"locale": "es", "w": 288})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"
    assert ok.content.startswith(b"\x89PNG")
    assert int.from_bytes(ok.content[16:20], "big") == 288          # IHDR width: exactly w px
    assert ok.headers["cache-control"] == "public, max-age=86400"
    assert ok.headers["etag"]

    default = await client.get(URL.format(slug="especialidad-color"))          # es, 576 px
    assert default.status_code == 200 and int.from_bytes(default.content[16:20], "big") == 576

    assert (await client.get(URL.format(slug="no-existe"))).status_code == 404
    # investidura-clase has es and en only: another language is not a thumbnail that exists
    assert (await client.get(URL.format(slug="investidura-clase"), params={"locale": "fr"})).status_code == 404
    for bad in ("300", "0", "abc", "2304"):
        assert (await client.get(URL.format(slug="especialidad-color"), params={"w": bad})).status_code == 422
    assert (await client.get(URL.format(slug="especialidad-color"), params={"locale": "../x"})).status_code == 422
    assert len(renders) == 2


@pytest.mark.asyncio
async def test_second_call_is_not_rendered_again(client, renders):
    first = await client.get(URL.format(slug="especialidad-color"), params={"w": 288})
    second = await client.get(URL.format(slug="especialidad-color"), params={"w": 288})
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert len(renders) == 1

    # Conditional request: no body, no render
    same = await client.get(URL.format(slug="especialidad-color"), params={"w": 288},
                            headers={"If-None-Match": first.headers["etag"]})
    assert same.status_code == 304 and len(renders) == 1

    # Another width or language is another picture
    await client.get(URL.format(slug="especialidad-color"), params={"w": 576, "locale": "pt"})
    assert len(renders) == 2 and renders[-1][1] == "pt"


@pytest.mark.asyncio
async def test_simultaneous_first_requests_render_once(client, renders):
    answers = await asyncio.gather(*[client.get(URL.format(slug="especialidad-dorada"), params={"w": 288})
                                     for _ in range(4)])
    assert [a.status_code for a in answers] == [200] * 4
    assert len({a.content for a in answers}) == 1
    assert len(renders) == 1


@pytest.mark.asyncio
async def test_stored_in_r2_then_redirected(client, renders, r2):
    first = await client.get(URL.format(slug="especialidad-color"), params={"w": 288})
    assert first.status_code == 200 and first.content.startswith(b"\x89PNG")
    [key] = list(r2.objects)                                  # uploaded after the response
    assert KEY.fullmatch(key)
    assert r2.objects[key]["ContentType"] == "image/png"
    assert r2.objects[key]["Body"] == first.content

    second = await client.get(URL.format(slug="especialidad-color"), params={"w": 288}, follow_redirects=False)
    assert second.status_code == 302
    assert second.headers["location"] == f"https://media.test/{key}"
    assert second.headers["cache-control"] == "public, max-age=86400"
    assert len(renders) == 1

    # A new version of the same variant replaces the old object (purge is per variant)
    old = key.replace(key[-16:-4], "0" * 12)
    r2.objects[old] = {"Body": b"old"}
    other_variant = "thumbs/especialidad-color/en-288-" + "1" * 12 + ".png"
    r2.objects[other_variant] = {"Body": b"en"}
    r2.objects.pop(key)
    sheet_cache.forget_all()
    thumbs.forget_all()
    await client.get(URL.format(slug="especialidad-color"), params={"w": 288})
    assert key in r2.objects and old not in r2.objects and other_variant in r2.objects


@pytest.mark.asyncio
async def test_r2_down_still_answers(client, renders, r2):
    r2.broken = True
    answer = await client.get(URL.format(slug="especialidad-color"), params={"w": 288})
    assert answer.status_code == 200 and answer.content.startswith(b"\x89PNG")
    again = await client.get(URL.format(slug="especialidad-color"), params={"w": 288})
    assert again.status_code == 200 and len(renders) == 1             # the in-process copy
