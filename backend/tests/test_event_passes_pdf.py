"""Pases imprimibles (SPEC_RED_LOCAL §1): `GET /events/{id}/passes.pdf`.

What is proved here:
  * who prints what: coordination every registered club (or the ones it names), a director only
    their own club, judges / strangers 403, anonymous 401;
  * printing NEVER rotates a pass: the PDF carries the token in force (text and QR link), and the
    stored hash/ciphertext are the same before and after;
  * a pass from before 028 (only hashed) is generated once, while the event is not closed;
  * the PDF is a valid US Letter document: two passes per sheet (or one with `per_page=1`), the
    club names, the event, the dates, the grouped code and the instructions in its text;
  * the logo is optional and a failing logo never breaks the PDF;
  * a typed code with the printed spaces resolves (`event_pass.token_from`).
"""
import io
import uuid
from datetime import date

import pytest
import pytest_asyncio
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import text

from app.db import SessionLocal
from app.services import event_pass
from app.services import event_passes_pdf as passes
from tests.conftest import RUN, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("passpdf")
API = "/api/v1/events"
LETTER = (612, 792)


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def _pdf(response) -> tuple[int, str, PdfReader]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(response.content))
    for page in reader.pages:
        assert (round(float(page.mediabox.width)), round(float(page.mediabox.height))) == LETTER
    words = " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages)
    return len(reader.pages), words, reader


def _links(reader: PdfReader) -> list[str]:
    uris = []
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            action = annot.get_object().get("/A")
            if action and action.get("/URI"):
                uris.append(str(action["/URI"]))
    return uris


@pytest_asyncio.fixture(scope="module")
async def w(factory):
    assoc = await factory.org("assoc", "association")
    church = await factory.org("church", "church", assoc)
    clubs = {key: await factory.org(f"club-{key}", "club", church) for key in ("a", "b", "c", "d")}
    # the factory's prefix stays at the front of the name (its cleanup deletes by prefix)
    names = {key: f"{factory.name(f'club-{key}')} {label}" for key, label in (
        ("a", "Alfa Estrellitas"), ("b", "Beta Exploradores"), ("c", "Gama Corderitos"), ("d", "Delta Retirados"))}
    for key, club in clubs.items():
        await _exec("UPDATE organizations SET name = :name, city = :city,"
                    " ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers') WHERE id = :id",
                    name=names[key], city="Monterrey" if key == "a" else None, id=uuid.UUID(club["id"]))
        await _exec("INSERT INTO organization_ministries (organization_id, ministry_id)"
                    " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
                    id=uuid.UUID(club["id"]))
    world = {
        "assoc": assoc, "clubs": clubs, "names": names,
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"]),
        "coordinator": await factory.user("coordinator", "INSTRUCTOR"),
        "judge": await factory.user("judge", "STUDENT"),
        "director_a": await factory.user("director-a", "CLUB_DIRECTOR", clubs["a"]["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", clubs["b"]["id"]),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    yield world
    ids = [uuid.UUID(assoc["id"])]
    await _exec("DELETE FROM audit_log WHERE entity_type = 'EVENT' AND entity_id IN"
                " (SELECT id::text FROM events WHERE organization_id = ANY(:ids))", ids=ids)
    await _exec("DELETE FROM events WHERE organization_id = ANY(:ids)", ids=ids)


async def _event(client, w, name: str) -> dict:
    h = w["admin"]["headers"]
    event = await _ok(await client.post(API, json={
        "organization_id": w["assoc"]["id"], "ministry": "adventurers", "name": name,
        "venue": "Campamento El Cielo", "city": "Santiago",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h), 201)
    url = f"{API}/{event['id']}"
    await _ok(await client.patch(url, json={"brand_color": "#0E6B4F", "brand_accent": "#F2B705"}, headers=h))
    await _ok(await client.post(f"{url}/staff", json={"user_id": w["coordinator"]["id"], "role": "COORDINATOR"},
                                headers=h), 201)
    await _ok(await client.post(f"{url}/staff", json={"user_id": w["judge"]["id"], "role": "JUDGE"}, headers=h), 201)
    regs, tokens = {}, {}
    for key, club in w["clubs"].items():
        reg = await _ok(await client.post(f"{url}/registrations", json={"club_id": club["id"]}, headers=h), 201)
        regs[key], tokens[key] = reg["id"], reg["pass_token"]
    await _ok(await client.post(f"{url}/registrations/{regs['d']}/withdraw", headers=h))
    return {"id": event["id"], "url": url, "regs": regs, "tokens": tokens}


@pytest_asyncio.fixture(scope="module")
async def ev(client, w):
    return await _event(client, w, f"{RUN} Camporee Universo")


async def _stored(reg_id: str):
    return await fetch_one("SELECT pass_token_hash, pass_token_enc, updated_at FROM event_registrations"
                           " WHERE id = :id", id=uuid.UUID(reg_id))


# ----------------------------------------------------------------------------
# Quién imprime qué
# ----------------------------------------------------------------------------
async def test_coordination_prints_every_registered_club_two_per_sheet(client, w, ev):
    before = {key: await _stored(ev["regs"][key]) for key in ("a", "b", "c")}
    for who in ("coordinator", "admin"):
        response = await client.get(f"{ev['url']}/passes.pdf", headers=w[who]["headers"])
        pages, words, reader = _pdf(response)
        assert pages == 2, who                          # 3 registered clubs, 2 per sheet
        for key in ("a", "b", "c"):
            assert w["names"][key] in words
        assert w["names"]["d"] not in words             # withdrawn: no pass
        assert f"{RUN} Camporee Universo" in words and "20–22 de noviembre de 2026" in words
        assert "Campamento El Cielo, Santiago" in words and "Monterrey" in words
        assert "Muestra este pase en la mesa de registro y en cada actividad." in words
        assert "Cortar por la línea" in words
        assert "X-Pass-Regenerated" not in response.headers
        assert response.headers["content-disposition"] == f'attachment; filename="pases-{RUN.lower()}-camporee-universo.pdf"'
        # The QR links are exactly the passes in force, in club-name order.
        assert _links(reader) == [event_pass.pass_url(ev["tokens"][key]) for key in ("a", "b", "c")]
    # Printing never rotates a pass.
    for key in ("a", "b", "c"):
        after = await _stored(ev["regs"][key])
        assert (after["pass_token_hash"], after["pass_token_enc"]) == \
            (before[key]["pass_token_hash"], before[key]["pass_token_enc"])
        shown = await _ok(await client.get(f"{ev['url']}/registrations/{ev['regs'][key]}/pass",
                                           headers=w["coordinator"]["headers"]))
        assert shown["pass_token"] == ev["tokens"][key]


async def test_the_printed_code_is_grouped_and_typable(client, w, ev):
    response = await client.get(f"{ev['url']}/passes.pdf", params={"registration_id": ev["regs"]["a"]},
                                headers=w["coordinator"]["headers"])
    pages, words, _ = _pdf(response)
    assert pages == 1
    token = ev["tokens"]["a"]
    printed = " ".join(passes.code_lines(token))
    assert " ".join(printed.split()) in words
    # what a person types from the paper resolves to the club
    typed = await client.post(f"{ev['url']}/resolve-pass", json={"token": f"  {printed}  "},
                              headers=w["judge"]["headers"])
    assert (await _ok(typed))["id"] == ev["regs"]["a"]


async def test_coordination_prints_a_selection_in_club_order_and_one_per_sheet(client, w, ev):
    url, h = f"{ev['url']}/passes.pdf", w["coordinator"]["headers"]
    response = await client.get(url, params=[("registration_id", ev["regs"]["c"]), ("registration_id", ev["regs"]["a"]),
                                             ("registration_id", ev["regs"]["a"])], headers=h)
    pages, words, reader = _pdf(response)
    assert pages == 1 and w["names"]["a"] in words and w["names"]["c"] in words and w["names"]["b"] not in words
    assert _links(reader) == [event_pass.pass_url(ev["tokens"]["a"]), event_pass.pass_url(ev["tokens"]["c"])]
    large = await client.get(url, params={"per_page": 1}, headers=h)
    pages, words, _ = _pdf(large)
    assert pages == 3
    one = await client.get(url, params={"per_page": 1, "registration_id": ev["regs"]["b"]}, headers=h)
    assert _pdf(one)[0] == 1
    assert one.headers["content-disposition"].startswith(f'attachment; filename="pase-{RUN.lower()}-passpdf')
    for bad in (0, 3, "x"):
        assert (await client.get(url, params={"per_page": bad}, headers=h)).status_code == 422
    # an id of no registration of this event: 404; a withdrawn club named explicitly: 409
    assert (await client.get(url, params={"registration_id": str(uuid.uuid4())}, headers=h)).status_code == 404
    assert (await client.get(url, params={"registration_id": "nope"}, headers=h)).status_code == 422
    withdrawn = await client.get(url, params={"registration_id": ev["regs"]["d"]}, headers=h)
    assert withdrawn.status_code == 409


async def test_a_director_prints_only_their_own_club(client, w, ev):
    url = f"{ev['url']}/passes.pdf"
    response = await client.get(url, headers=w["director_a"]["headers"])
    pages, words, reader = _pdf(response)
    assert pages == 1 and w["names"]["a"] in words
    assert w["names"]["b"] not in words and w["names"]["c"] not in words
    assert _links(reader) == [event_pass.pass_url(ev["tokens"]["a"])]
    mine = await client.get(url, params={"registration_id": ev["regs"]["a"], "per_page": 1},
                            headers=w["director_a"]["headers"])
    assert _pdf(mine)[0] == 1
    other = await client.get(url, params={"registration_id": ev["regs"]["b"]}, headers=w["director_a"]["headers"])
    assert other.status_code == 403
    mixed = await client.get(url, params=[("registration_id", ev["regs"]["a"]), ("registration_id", ev["regs"]["c"])],
                             headers=w["director_a"]["headers"])
    assert mixed.status_code == 403
    for who in ("judge", "stranger"):
        assert (await client.get(url, headers=w[who]["headers"])).status_code == 403, who
    assert (await client.get(url)).status_code == 401
    assert (await client.get(f"{API}/{uuid.uuid4()}/passes.pdf", headers=w["admin"]["headers"])).status_code == 404


async def test_a_legacy_pass_is_generated_once_and_never_after_closing(client, w):
    ev = await _event(client, w, f"{RUN} Legado")
    url = f"{ev['url']}/passes.pdf"
    old = ev["tokens"]["b"]
    await _exec("UPDATE event_registrations SET pass_token_enc = NULL WHERE id = :id", id=uuid.UUID(ev["regs"]["b"]))
    first = await client.get(url, headers=w["director_b"]["headers"])
    _, _, reader = _pdf(first)
    assert first.headers.get("X-Pass-Regenerated") == "1"
    [link] = _links(reader)
    fresh = event_pass.token_from(link)
    assert fresh != old
    # the old pass is dead, the printed one is the one in force, and printing again keeps it
    assert (await client.post(f"{ev['url']}/resolve-pass", json={"token": old},
                              headers=w["judge"]["headers"])).status_code == 404
    shown = await _ok(await client.get(f"{ev['url']}/registrations/{ev['regs']['b']}/pass",
                                       headers=w["director_b"]["headers"]))
    assert shown["pass_token"] == fresh
    again = await client.get(url, headers=w["coordinator"]["headers"])
    assert "X-Pass-Regenerated" not in again.headers
    assert event_pass.pass_url(fresh) in _links(_pdf(again)[2])
    audit = await fetch_one("SELECT metadata_json FROM audit_log WHERE action = 'EVENT_PASS_REGENERATE'"
                            " AND entity_id = :id ORDER BY created_at DESC LIMIT 1", id=ev["regs"]["b"])
    assert audit["metadata_json"]["reason"] == "legacy"
    # Closed event: a legacy pass can no longer be generated (409); readable ones still print.
    h = w["admin"]["headers"]
    for step in ("OPEN", "IN_PROGRESS", "CLOSED"):
        await _ok(await client.post(f"{ev['url']}/status", json={"status": step}, headers=h))
    await _exec("UPDATE event_registrations SET pass_token_enc = NULL WHERE id = :id", id=uuid.UUID(ev["regs"]["c"]))
    assert (await client.get(url, headers=w["coordinator"]["headers"])).status_code == 409
    readable = await client.get(url, params={"registration_id": ev["regs"]["a"]}, headers=w["coordinator"]["headers"])
    assert _pdf(readable)[0] == 1


# ----------------------------------------------------------------------------
# Renderer (pure)
# ----------------------------------------------------------------------------
BRAND = passes.EventBrand(name="Camporee", starts_on=date(2026, 11, 20), ends_on=date(2026, 11, 22),
                          color="#FFE600", accent=None, logo_url="https://evil.example/logo.png")
ITEMS = [passes.ClubPass(club_name=f"Club {n}", club_city=None, token="evp_" + "x" * 32,
                         url="https://events.adventist.club/p/evp_" + "x" * 32) for n in range(5)]


def test_date_range_in_spanish():
    assert passes.date_range(date(2026, 11, 20), date(2026, 11, 20)) == "20 de noviembre de 2026"
    assert passes.date_range(date(2026, 11, 20), date(2026, 11, 22)) == "20–22 de noviembre de 2026"
    assert passes.date_range(date(2026, 11, 30), date(2026, 12, 2)) == "30 de noviembre – 2 de diciembre de 2026"
    assert passes.date_range(date(2026, 12, 30), date(2027, 1, 2)) == "30 de diciembre de 2026 – 2 de enero de 2027"


def test_place_line_does_not_repeat_the_city():
    assert passes.place_line("La Morita", "La Morita, N.L.") == "La Morita, N.L."
    assert passes.place_line("la morita", "La Morita") == "la morita"
    assert passes.place_line("Río Bravo", "rio bravo, Tamps.") == "Río Bravo, Tamps."
    assert passes.place_line("Campamento Río Bravo", "Monterrey, N.L.") == "Campamento Río Bravo, Monterrey, N.L."
    assert passes.place_line(None, "Reynosa") == "Reynosa"
    assert passes.place_line("", None) == ""


def test_the_pass_header_prints_the_place_once():
    brand = passes.EventBrand(name="Camporee", starts_on=date(2026, 10, 24), ends_on=date(2026, 10, 26),
                              venue="La Morita", city="La Morita, N.L.")
    item = passes.ClubPass(club_name="Estrellitas", club_city=None, token="evp_" + "A" * 40,
                           url="https://events.adventist.club/p/evp_" + "A" * 40)
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(passes.render_passes_pdf(brand, [item]))).pages)
    assert "La Morita, N.L." in text
    assert "La Morita · La Morita" not in text and text.count("La Morita") == 1


def test_code_lines_group_the_token():
    token = "evp_AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    lines = passes.code_lines(token)
    assert lines == ["evp_ AbCd EfGh IjKl MnOp", "     QrSt UvWx Yz01 2345"]
    assert "".join("".join(lines).split()) == token
    assert event_pass.token_from(" ".join(lines)) == token
    assert event_pass.token_from("\n".join(lines)) == token


def test_pages_per_sheet_and_a_failing_logo_never_breaks_the_pdf(monkeypatch):
    calls = []
    monkeypatch.setattr(passes, "load_patch", lambda url: calls.append(url))   # → None: no logo
    for per_page, pages in ((2, 3), (1, 5)):
        reader = PdfReader(io.BytesIO(passes.render_passes_pdf(BRAND, ITEMS, per_page=per_page)))
        assert len(reader.pages) == pages
    assert calls == [BRAND.logo_url] * 2
    with pytest.raises(ValueError):
        passes.render_passes_pdf(BRAND, ITEMS, per_page=3)
    # the real loader refuses any host but our bucket, without raising
    assert passes.load_patch("https://evil.example/logo.png") is None
    # garbage bytes as a logo: printed without it
    body = passes.render_passes_pdf(BRAND, ITEMS[:1], logo_png=b"not an image")
    assert PdfReader(io.BytesIO(body)).pages[0].extract_text()


def test_the_logo_is_drawn_when_available():
    image = Image.new("RGBA", (64, 64), (200, 30, 30, 255))
    raw = io.BytesIO()
    image.save(raw, "PNG")
    with_logo = PdfReader(io.BytesIO(passes.render_passes_pdf(BRAND, ITEMS[:2], logo_png=raw.getvalue())))
    without = PdfReader(io.BytesIO(passes.render_passes_pdf(BRAND, ITEMS[:2], fetch_logo=False)))
    assert len(with_logo.pages[0].images) == 1 and len(without.pages[0].images) == 0


def test_text_on_the_brand_band_stays_readable():
    from reportlab.lib.colors import HexColor, white

    assert passes.on_color(HexColor("#FFE600")) == passes.INK      # yellow band → ink text
    assert passes.on_color(HexColor("#0E6B4F")) == white           # dark green → white text
    assert passes.contrast(passes.readable_on_white(HexColor("#FFE600")), white) >= 4.5
