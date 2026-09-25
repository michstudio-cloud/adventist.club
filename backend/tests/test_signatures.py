"""Handwritten signatures on certificates (020_signatures.sql, docs/CERTIFICADOS_V4.md «Firmas»):
the slots of the templates, the checks of POST /render and the signature saved to an account."""
import base64
import io
import os
import re
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

from app.certificates.render import fill_svg, load_template, render_certificate
from app.certificates.signatures import (
    MAX_OUTPUT_PX, MAX_SIGNATURE_BYTES, SignatureError, normalize_signature, normalize_signature_bytes,
)
from app.config import settings
from app.services import storage
from tests.conftest import module_factory, requires_db

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from compile_element_template import signature_align, signature_box  # noqa: E402

V4 = ("especialidad-editorial-rojo", "especialidad-reticula-verde", "especialidad-academico")  # modular-azul: réplica 2026-09-24, otra geometría (test_certificate_azul)
SLOTS = ("signature_director", "signature_instructor")
RENDER = "/api/v1/certificates/render"
SIGNATURE = "/api/v1/users/me/signature"
factory = module_factory("signatures")


def _signature_png(width=600, height=160) -> bytes:
    """A black scribble on a transparent background, like the assistant's canvas exports."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = image.load()
    for x in range(20, width - 20):
        y = int(height / 2 + (height / 3) * ((x % 90) - 45) / 45)
        for dy in range(-3, 4):
            pixels[x, max(0, min(height - 1, y + dy))] = (0, 0, 0, 255)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _data_url(data: bytes, mime="image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _noise_png(side=420) -> bytes:
    """Incompressible: comfortably above 400 KB as a PNG."""
    image = Image.frombytes("RGBA", (side, side), os.urandom(side * side * 4))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _box(svg: str, slot: str) -> dict[str, float]:
    tag = re.search(rf'<image[^>]*id="{slot}"[^>]*/>', svg).group(0)
    box = {k: float(re.search(rf'\b{k}="([\d.]+)"', tag).group(1)) for k in ("x", "y", "width", "height")}
    box["align"] = re.search(r'preserveAspectRatio="([^"]+)"', tag).group(1)
    return box


@pytest.mark.parametrize("slug", V4)
def test_v4_templates_have_a_slot_over_each_signature_line(slug):
    svg = load_template(slug).svg
    for slot, name in zip(SLOTS, ("director_name", "instructor_name")):
        box = _box(svg, slot)
        name_tag = re.search(rf'<text[^>]*id="{name}"[^>]*>', svg).group(0)
        x, baseline = (float(re.search(rf'\b{k}="([\d.]+)"', name_tag).group(1)) for k in ("x", "y"))
        # as wide as the line (235 units in every v4 design), 2.2 x the 14-unit name tall
        assert (box["x"], box["width"]) == (x, 235.0)
        assert box["height"] == pytest.approx(30.8)
        # contained, never cropped; from the left edge of the (left-aligned) name, resting on it
        assert box["align"] == "xMinYMax meet"
        # it ends above the capitals of the printed name: the name never moves nor is covered
        assert box["y"] + box["height"] <= baseline - 0.7 * 14
    # drawn before the names, so a long descender of the signature stays under the text
    assert svg.index('id="signature_director"') < svg.index('id="director_name"')


def test_the_investiture_template_has_the_slots_on_its_lines():
    svg = load_template("investidura-clase").svg
    director, instructor = _box(svg, "signature_director"), _box(svg, "signature_instructor")
    assert director["y"] + director["height"] == pytest.approx(269.3)      # sits on the line
    assert (director["x"], director["width"]) == (75.2, 87.2)
    assert (instructor["x"], instructor["width"]) == (233.6, 87.2)
    assert director["align"] == instructor["align"] == "xMidYMax meet"          # centred names


def test_the_compiler_derives_the_box_from_the_name_and_its_line():
    name = {"x": 100, "baseline_y": 500, "font_size": 10, "max_width": 200, "anchor": "start"}
    under = signature_box(name, [(90, 515, 220), (0, 100, 50)])            # line under the name
    assert under == pytest.approx({"x": 90, "y": 500 - 7.5 - 22, "width": 220, "height": 22})
    over = signature_box({**name, "baseline_y": 520}, [(90, 505, 220)])    # name printed under the line
    assert over["y"] + over["height"] == 505
    alone = signature_box({**name, "anchor": "middle"}, [])                 # no line: the name's own box
    assert (alone["x"], alone["width"]) == (0, 200)
    assert signature_align({"anchor": "middle"}) == "xMidYMax meet"
    assert signature_align({"align": "right"}) == "xMaxYMax meet"


def test_without_a_signature_nothing_is_drawn_and_with_one_it_is():
    template = load_template("especialidad-editorial-rojo")
    data = {"recipient_name": "Ana", "honor_name": "Nudos", "issued_date": "2026-09-21", "director_name": "Juan Pérez"}
    empty = fill_svg(template, data, {})
    assert re.search(r'<image[^>]*id="signature_director"[^>]*opacity="0"', empty)
    signature = _data_url(_signature_png())
    signed = fill_svg(template, data, {"signature_director": signature})
    assert signature[:60] in signed and ">Juan Pérez<" in signed

    def ink(png: bytes) -> int:           # dark pixels over the director's line
        page = Image.open(io.BytesIO(png)).convert("L")
        scale = page.width / 1100
        region = page.crop((int(210 * scale), int(667 * scale), int(445 * scale), int(698 * scale)))
        return sum(1 for value in region.getdata() if value < 90)

    plain, _ = render_certificate("especialidad-editorial-rojo", data, {}, dpi=100)
    inked, _ = render_certificate("especialidad-editorial-rojo", data, {"signature_director": signature}, dpi=100)
    assert ink(plain) == 0 and ink(inked) > 50


def test_signature_rules():
    png = normalize_signature(_data_url(_signature_png()))
    assert png.startswith("data:image/png;base64,")
    with pytest.raises(SignatureError, match="SVG"):
        normalize_signature("data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode())
    with pytest.raises(SignatureError, match="400 KB"):
        normalize_signature(_data_url(_noise_png()))
    with pytest.raises(SignatureError, match="máximo"):                     # tiny file, absurd canvas
        normalize_signature(_data_url(_signature_png(4100, 20)))
    with pytest.raises(SignatureError, match="no corresponde"):
        normalize_signature(_data_url(_signature_png(), "image/jpeg"))
    with pytest.raises(SignatureError):
        normalize_signature_bytes(b"not an image", "image/png")
    # what reaches resvg is small whatever was uploaded: render time does not depend on it
    wide = Image.open(io.BytesIO(normalize_signature_bytes(_signature_png(3600, 900), "image/png")))
    assert max(wide.size) == MAX_OUTPUT_PX and wide.mode == "RGBA"


@pytest.mark.asyncio
async def test_render_endpoint_accepts_a_signature_and_refuses_the_rest(client, monkeypatch):
    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)
    body = {"template": "especialidad-editorial-rojo", "format": "svg",
            "data": {"recipient_name": "Ana", "honor_name": "Nudos", "issued_date": "2026-09-21"}}
    signature = _data_url(_signature_png())
    ok = await client.post(RENDER, json={**body, "images": {"signature_director": signature, "signature_instructor": signature}})
    assert ok.status_code == 200, ok.text
    assert ok.text.count("data:image/png;base64,") >= 2
    png = await client.post(RENDER, json={**body, "format": "png", "dpi": 72, "images": {"signature_instructor": signature}})
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"

    svg = "data:image/svg+xml;base64," + base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"/>').decode()
    refused = await client.post(RENDER, json={**body, "images": {"signature_director": svg}})
    assert refused.status_code == 422 and "SVG" in refused.json()["detail"]
    heavy = await client.post(RENDER, json={**body, "images": {"signature_director": _data_url(_noise_png())}})
    assert heavy.status_code == 422 and "400 KB" in heavy.json()["detail"]
    foreign = await client.post(RENDER, json={**body, "images": {"signature_director": "https://evil.example/s.png"}})
    assert foreign.status_code == 422

    # a signature saved to an account travels as its media URL; the endpoint fetches it
    monkeypatch.setattr("app.routers.render._fetch_media_image", lambda url: signature)
    saved = await client.post(RENDER, json={**body, "images": {"signature_director": "https://media.adventist.club/signatures/a.png"}})
    assert saved.status_code == 200 and "signature_director" in saved.text


# --- the signature saved to an account ------------------------------------------------------


class FakeR2:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.deleted: list[str] = []
        self.threads = []

    def put_object(self, **kwargs):
        self.threads.append(threading.current_thread())
        self.objects[kwargs["Key"]] = kwargs

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs["Key"])


@pytest.fixture
def r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "https://media.test/")
    monkeypatch.setattr(storage, "get_client", lambda: fake)
    return fake


async def _save(client, user, data: bytes, content_type="image/png"):
    return await client.post(SIGNATURE, files={"file": ("firma.png", data, content_type)},
                             headers=user["headers"] if user else None)


@requires_db
async def test_saving_needs_an_account_and_storage(client, factory):
    assert (await _save(client, None, _signature_png())).status_code == 401
    person = await factory.user("no-r2", role="CLUB_DIRECTOR")
    response = await _save(client, person, _signature_png())
    assert response.status_code == 503


@requires_db
async def test_save_show_replace_and_forget_my_signature(client, factory, r2):
    director = await factory.user("director", role="CLUB_DIRECTOR")
    me = await client.get("/api/v1/auth/me", headers=director["headers"])
    assert me.status_code == 200 and me.json()["signature_url"] is None

    first = await _save(client, director, _signature_png())
    assert first.status_code == 200, first.text
    url = first.json()["signature_url"]
    assert re.fullmatch(r"https://media\.test/signatures/[0-9a-f]{32}\.png", url)
    stored = r2.objects[url.removeprefix("https://media.test/")]
    assert stored["ContentType"] == "image/png" and stored["Body"].startswith(b"\x89PNG")
    assert r2.threads[0] is not threading.main_thread()          # the SDK call never blocks the loop
    assert (await client.get("/api/v1/auth/me", headers=director["headers"])).json()["signature_url"] == url
    assert (await client.get("/api/v1/users/me", headers=director["headers"])).json()["signature_url"] == url

    # nobody else ever sees it, not even who may read the whole record
    master = await factory.user("master", role="MASTER_GC")
    other_view = await client.get(f"/api/v1/users/{director['id']}", headers=master["headers"])
    assert other_view.status_code == 200 and other_view.json()["signature_url"] is None

    second = await _save(client, director, _signature_png(400, 120))
    assert second.status_code == 200 and second.json()["signature_url"] != url
    assert r2.deleted == [url.removeprefix("https://media.test/")]           # the old one is gone

    forgotten = await client.delete(SIGNATURE, headers=director["headers"])
    assert forgotten.status_code == 200 and forgotten.json()["signature_url"] is None
    assert len(r2.deleted) == 2
    assert (await client.delete(SIGNATURE, headers=director["headers"])).status_code == 200    # idempotent


@requires_db
async def test_what_cannot_be_saved(client, factory, r2):
    instructor = await factory.user("instructor", role="INSTRUCTOR")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h10"/></svg>'
    assert (await _save(client, instructor, svg, "image/svg+xml")).status_code == 415
    assert (await _save(client, instructor, _noise_png())).status_code == 413
    assert len(_noise_png()) > MAX_SIGNATURE_BYTES
    lying = await _save(client, instructor, b"GIF89a" + b"\x00" * 64, "image/png")
    assert lying.status_code == 422
    minor = await factory.user("minor", is_minor=True)
    assert (await _save(client, minor, _signature_png())).status_code == 403
    assert r2.objects == {}
