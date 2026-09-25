"""023 — «Frases editables» (docs/CERTIFICADOS_V4.md): «Se otorga el presente certificado a:» and
«por haber cumplido satisfactoriamente los requisitos de…» can be reworded ONLY with an account.

Each template declares which of its strings are editable (meta.json `editable_strings`); the render
takes `strings` for those keys only, fits them in the same box (shrink → wrap → 422); the batch with
a session, the portfolio and the investiture keep them in `certificates.text_overrides`, and every
later render by folio prints the record's, whatever the caller sends.
"""
import json
import re
import shutil
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.certificates.render import (
    MAX_OVERRIDE_LENGTH, TEMPLATES_DIR, OverrideError, TemplateError, _load, check_overrides_fit, clean_text_overrides,
    fill_svg, load_template,
)
from app.config import settings
from app.db import SessionLocal
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db
from tests.test_portfolio import ENROLLMENTS, _complete_all, _enroll, _honor

TEMPLATES = "/api/v1/certificates/templates"
RENDER = "/api/v1/certificates/render"
BATCH = "/api/v1/certificates/prototype-batch"
EDITABLE = {
    "especialidad-color": ["awarded", "completion"],
    "especialidad-dorada": ["awarded", "completion"],
    "especialidad-editorial-rojo": ["awarded", "completion"],
    "especialidad-modular-azul": ["awarded", "completion"],  # replica azul 2026-09-24: «otorga el presente certificado a:»
    "especialidad-reticula-verde": ["awarded", "completion"],
    "especialidad-academico": ["awarded", "completion"],
    "investidura-clase": ["t_awarded_to", "t_for_completing"],
}
DATA = {"recipient_name": "Ana Ruiz", "honor_name": "Nudos", "issued_date": "2026-09-24",
        "director_name": "Juan Pérez", "instructor_name": "Luz Díaz"}
AWARDED = "Con gratitud reconocemos a"
COMPLETION = "por su constancia y servicio al completar"


def _texts(svg: str) -> str:
    """Every drawn line, unescaped, joined: what a reader of the certificate sees."""
    lines = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg) + re.findall(r"<text[^>]*>([^<]+)</text>", svg)
    return " | ".join(line.replace("&amp;", "&") for line in lines)


# --- The templates -------------------------------------------------------------------------------
@pytest.mark.parametrize("slug", EDITABLE)
def test_each_template_declares_its_two_award_phrases(slug):
    template = load_template(slug)
    items = template.editable_strings
    assert [item.key for item in items] == EDITABLE[slug]
    assert [item.role for item in items] == ["awarded", "completion"]
    for item in items:
        assert set(template.locales) <= set(item.defaults)            # the text in every language
        assert len(item.defaults["es"]) <= item.max_length <= MAX_OVERRIDE_LENGTH
    spanish = {item.role: item.defaults["es"].lower() for item in items}
    assert "otorga el presente certificado a" in spanish["awarded"]
    assert "satisfactoriamente los requisitos de" in spanish["completion"]


def test_a_retired_template_offers_none():
    assert load_template("especialidad-basica").editable_strings == []


def test_a_declared_phrase_that_is_not_in_the_svg_is_an_error(tmp_path):
    shutil.copytree(TEMPLATES_DIR / "especialidad-reticula-verde", tmp_path / "frase-rota")
    meta_path = tmp_path / "frase-rota" / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta_path.write_text(json.dumps({**meta, "editable_strings": ["awarded", "nope"]}))
    with pytest.raises(TemplateError, match="nope"):
        load_template("frase-rota", tmp_path).editable_strings
    meta_path.write_text(json.dumps({**meta, "editable_strings": [{"key": "awarded", "role": "title"}]}))
    _load.cache_clear()
    with pytest.raises(TemplateError, match="mal declarado"):
        load_template("frase-rota", tmp_path).editable_strings
    _load.cache_clear()


def test_cleaning_keeps_only_real_changes_and_refuses_the_rest():
    template = load_template("especialidad-color")
    assert clean_text_overrides(template, {"awarded": "  Con   gratitud a  ", "completion": ""}) == {"awarded": "Con gratitud a"}
    # the template's own text in the certificate's language is not a change
    same = template.editable("awarded").defaults["en"]
    assert clean_text_overrides(template, {"awarded": same}, "en") == {}
    assert clean_text_overrides(template, {"awarded": same}, "es") == {"awarded": same}
    for strings, code in (({"title_0": "X"}, "string_not_editable"),
                          ({"recipient_name": "X"}, "string_not_editable"),
                          ({"awarded": "dos\nlíneas"}, "string_invalid"),
                          ({"awarded": "tab\there"}, "string_invalid"),
                          ({"awarded": "x" * (template.editable("awarded").max_length + 1)}, "string_too_long")):
        with pytest.raises(OverrideError) as refused:
            clean_text_overrides(template, strings)
        assert refused.value.code == code


def test_the_reworded_phrase_is_drawn_in_the_same_box():
    template = load_template("especialidad-reticula-verde")
    svg = fill_svg(template, DATA, {}, "es", overrides={"awarded": AWARDED, "completion": COMPLETION})
    assert AWARDED in _texts(svg) and COMPLETION in _texts(svg)
    assert "Se otorga el presente certificado a" not in _texts(svg)
    assert re.search(r'<text[^>]*id="t_awarded"[^>]*font-size="15"', svg)       # fits at its design size
    # longer than one line at the design size: shrinks, then wraps in its two lines (completion)
    long = ("por haber demostrado constancia, alegría y un espíritu de servicio ejemplar durante todo el año "
            "al cumplir con esmero cada uno de los requisitos de")
    svg = fill_svg(template, DATA, {}, "es", overrides={"completion": long})
    lines = re.search(r'<text[^>]*id="t_completion"[^>]*>(.*?)</text>', svg).group(1)
    assert len(re.findall("<tspan", lines)) == 2
    # a text that fits nowhere is refused before issuing and when rendering
    with pytest.raises(OverrideError) as refused:
        check_overrides_fit(template, {"awarded": "W" * 120})
    assert refused.value.code == "string_does_not_fit"
    with pytest.raises(TemplateError, match="campo 'awarded' no cabe"):
        fill_svg(template, DATA, {}, "es", overrides={"awarded": "W" * 120})


def test_the_investiture_template_rewords_its_older_t_texts():
    template = load_template("investidura-clase")
    svg = fill_svg(template, DATA, {}, "en", overrides={"t_awarded_to": "We proudly invest:"})
    assert "We proudly invest:" in _texts(svg)
    assert "For having satisfactorily completed" in _texts(svg)          # the other one stays translated
    # without overrides nothing changes: no fitting on the fixed texts
    plain = fill_svg(template, DATA, {}, "es")
    assert re.search(r'<text id="t_awarded_to"[^>]*font-size="8"[^>]*>Se otorga el presente certificado a:</text>', plain)


# --- HTTP -------------------------------------------------------------------------------------------
factory = module_factory("frases")


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    club = await factory.org("club", "club", await factory.org("assoc", "association"))
    issuer = await factory.org("issuer", "association")
    code = f"{factory.prefix}-ISS"
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET code = :code WHERE id = :id"),
                         {"code": code, "id": uuid.UUID(issuer["id"])})
        await db.commit()
    return {"club": club, "issuer_code": code,
            "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
            "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"])}


@pytest.fixture
def issuer(world, monkeypatch):
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


def _batch(factory, names, **extra) -> dict:
    return {"recipient_names": names, "honor_name": factory.name("Frases"), "club_name": factory.name("club-frases"),
            "issued_date": "2026-09-24", "instructor_name": "I", "director_name": "D", "width_in": 11, "height_in": 8.5,
            "template": "especialidad-color", **extra}


async def _svg(client, certificate_no=None, strings=None, template="especialidad-color", locale=None):
    body = {"template": template, "format": "svg", "data": DATA, "strings": strings or {}}
    if certificate_no:
        body["certificate_no"] = certificate_no
    if locale:
        body["locale"] = locale
    response = await client.post(RENDER, json=body)
    assert response.status_code == 200, response.text
    return _texts(response.text)


@pytest.mark.asyncio
async def test_the_template_list_offers_the_editable_phrases(client):
    listed = {t["slug"]: t for t in (await client.get(TEMPLATES)).json()}
    for slug in ("especialidad-color", "especialidad-academico"):
        phrases = listed[slug]["editable_strings"]
        assert [(p["key"], p["role"]) for p in phrases] == [("awarded", "awarded"), ("completion", "completion")]
        assert set(phrases[0]["defaults"]) == {"es", "en", "pt", "fr"}
        assert 0 < phrases[0]["max_length"] <= MAX_OVERRIDE_LENGTH
    investiture = {t["slug"]: t for t in (await client.get(TEMPLATES, params={"kind": "program"})).json()}
    assert [p["key"] for p in investiture["investidura-clase"]["editable_strings"]] == ["t_awarded_to", "t_for_completing"]


@pytest.mark.asyncio
async def test_the_render_takes_editable_phrases_only(client):
    texts = await _svg(client, strings={"awarded": AWARDED, "completion": COMPLETION})
    assert AWARDED in texts and COMPLETION in texts
    for strings, code in (({"title": "Hola"}, "string_not_editable"), ({"awarded": "a\r\nb"}, "string_invalid"),
                          ({"awarded": "x" * 161}, "string_too_long")):
        response = await client.post(RENDER, json={"template": "especialidad-color", "format": "svg",
                                                   "data": DATA, "strings": strings})
        assert response.status_code == 422, strings
        assert response.json()["detail"]["code"] == code
    too_wide = await client.post(RENDER, json={"template": "especialidad-color", "format": "svg",
                                               "data": DATA, "strings": {"awarded": "W" * 150}})
    assert too_wide.status_code == 422 and "campo 'awarded' no cabe" in too_wide.json()["detail"]


@requires_db
async def test_without_an_account_the_phrases_are_the_templates(client, factory, world, issuer):
    refused = await client.post(BATCH, json=_batch(factory, [factory.name("Anon")], strings={"awarded": AWARDED}))
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "strings_require_account"
    assert await fetch_one("SELECT 1 AS x FROM certificates WHERE recipient_name = :n", n=factory.name("Anon")) is None
    # blank phrases are the template's: nothing to refuse
    issued = await client.post(BATCH, json=_batch(factory, [factory.name("Anon")], strings={"awarded": " "}))
    assert issued.status_code == 201, issued.text
    number = issued.json()[0]["certificate_no"]
    assert (await fetch_one("SELECT text_overrides FROM certificates WHERE certificate_no = :n", n=number))["text_overrides"] is None
    # an issued folio prints its record: the caller cannot reword it afterwards
    texts = await _svg(client, number, {"awarded": AWARDED})
    assert AWARDED not in texts and "se otorga el presente certificado a" in texts.lower()   # the template's own phrase (color prints it in capitals)
    assert (await client.get(f"/api/v1/certificates/verify/{number}")).json()["text_overrides"] is None


@requires_db
async def test_the_assistant_with_a_session_keeps_the_phrases_of_the_batch(client, factory, world, issuer):
    names = [factory.name("Uno"), factory.name("Dos")]
    same_as_template = load_template("especialidad-color").editable("completion").defaults["en"]
    response = await client.post(BATCH, json=_batch(factory, names, locale="en",
                                                     strings={"awarded": AWARDED, "completion": same_as_template}),
                                 headers=world["instructor"]["headers"])
    assert response.status_code == 201, response.text
    issued = response.json()
    assert "text_overrides" not in issued[0]                                       # the batch's contract is unchanged
    rows = await fetch_all("SELECT id, text_overrides FROM certificates WHERE certificate_no = ANY(:n)",
                           n=[item["certificate_no"] for item in issued])
    assert [row["text_overrides"] for row in rows] == [{"awarded": AWARDED}] * 2      # only what changed
    event = await fetch_one("SELECT metadata_json FROM certificate_events WHERE certificate_id = :id AND event_type = 'issued'",
                            id=rows[0]["id"])
    assert event["metadata_json"]["text_overrides"] == ["awarded"]                  # the keys, never the text
    assert AWARDED not in json.dumps(event["metadata_json"])

    number = issued[1]["certificate_no"]
    verified = (await client.get(f"/api/v1/certificates/verify/{number}")).json()
    assert verified["valid"] and verified["text_overrides"] == {"awarded": AWARDED}
    # downloaded again by folio (/verify, portfolio): nothing sent, the record's phrase is printed ...
    texts = await _svg(client, number)
    assert AWARDED in texts and same_as_template in texts
    # ... and whatever the caller sends does not replace it
    texts = await _svg(client, number, {"awarded": "Otra cosa", "completion": COMPLETION})
    assert AWARDED in texts and "Otra cosa" not in texts and COMPLETION not in texts

    for strings, code in (({"title_0": "X"}, "string_not_editable"), ({"awarded": "W" * 150}, "string_does_not_fit")):
        refused = await client.post(BATCH, json=_batch(factory, [factory.name("Tres")], strings=strings),
                                    headers=world["instructor"]["headers"])
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == code, refused.text
    assert await fetch_one("SELECT 1 AS x FROM certificates WHERE recipient_name = :n", n=factory.name("Tres")) is None


@requires_db
async def test_the_portfolio_keeps_the_phrases_of_its_certificate(client, factory, world, issuer):
    member = await factory.user("member", "STUDENT", world["club"]["id"])
    honor = await _honor(factory, "honor-frases")
    enrollment = await _enroll(client, member, honor)
    await _complete_all(client, member, world["director"], enrollment)
    url = f"{ENROLLMENTS}/{enrollment['id']}/certificate"
    body = {"template": "especialidad-academico", "locale": "pt", "issued_date": "2026-09-24"}
    refused = await client.post(url, json={**body, "strings": {"church_name": "X"}}, headers=world["director"]["headers"])
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "string_not_editable"
    response = await client.post(url, json={**body, "strings": {"completion": COMPLETION}}, headers=world["director"]["headers"])
    assert response.status_code == 201, response.text
    certificate = response.json()
    assert certificate["text_overrides"] == {"completion": COMPLETION}
    audit = await fetch_one("SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'CERTIFICATE_ISSUE'",
                            id=certificate["id"])
    assert audit["metadata_json"]["text_overrides"] == ["completion"]
    mine = (await client.get("/api/v1/portfolio/me", headers=member["headers"])).json()
    assert any(c["text_overrides"] == {"completion": COMPLETION} for c in mine["certificates"])
    texts = await _svg(client, certificate["certificate_no"], template="especialidad-academico")
    assert COMPLETION in texts
    assert load_template("especialidad-academico").editable("awarded").defaults["pt"] in texts   # issued in Portuguese
