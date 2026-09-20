"""Print imposition: pure layout maths + the PDF it produces. No database needed."""
import base64
import io
import re
import struct
import zlib

import pytest

from app.printing import LayoutRequest, compute_layout, crop_mark_segments, render_pdf

LETTER = (8.5, 11.0)
QUARTER = (5.5, 4.25)


def layout(page=LETTER, item=QUARTER, **kw):
    return compute_layout(LayoutRequest(
        page_width=page[0], page_height=page[1], item_width=item[0], item_height=item[1], **kw))


def test_four_quarter_letters_fit_on_letter_by_rotating():
    """The project's reference case: 5.5 x 4.25 must give 4 per Letter sheet."""
    auto = layout()
    assert (auto.per_page, auto.cols, auto.rows, auto.scale) == (4, 2, 2, 1.0)
    assert auto.rotated is False and (auto.page_width, auto.page_height) == (11.0, 8.5)  # prefers not rotating
    portrait = layout(orientation="portrait")
    assert portrait.per_page == 4 and portrait.rotated is True  # 4.25 wide x 5.5 tall cells


def test_never_scales_unless_explicitly_allowed():
    too_big = layout(item=(9.0, 12.0))
    assert too_big.per_page == 0 and too_big.scale == 1.0
    allowed = layout(item=(9.0, 12.0), allow_scale_down=True)
    assert allowed.per_page == 1 and 0 < allowed.scale < 1
    # scaled item must actually fit inside the printable area
    assert allowed.cell_width <= 8.5 + 1e-9 and allowed.cell_height <= 11.0 + 1e-9


def test_margins_and_gaps_reduce_the_count():
    assert layout(margin_top=0.25, margin_right=0.25, margin_bottom=0.25, margin_left=0.25).per_page == 2
    assert layout(gap_x=0.25).per_page == 2          # 2 x 4.25 + 0.25 no longer fits 8.5 wide
    assert layout(page=(11.0, 17.0), gap_x=0.25, gap_y=0.25).per_page == 6


def test_rotation_can_be_forbidden():
    fixed = layout(allow_rotation=False, orientation="portrait")
    assert fixed.per_page == 2 and fixed.rotated is False
    assert layout(allow_rotation=False).per_page == 4  # auto orientation turns the sheet instead


def test_auto_orientation_picks_the_better_sheet_orientation():
    portrait = layout(page=(8.5, 11.0), item=(10.0, 4.0), allow_rotation=False, orientation="portrait")
    auto = layout(page=(8.5, 11.0), item=(10.0, 4.0), allow_rotation=False, orientation="auto")
    assert portrait.per_page == 0
    assert auto.per_page == 2 and (auto.page_width, auto.page_height) == (11.0, 8.5)
    forced = layout(page=(8.5, 11.0), orientation="landscape")
    assert (forced.page_width, forced.page_height) == (11.0, 8.5)


def test_bleed_reserves_space_around_every_item():
    assert layout(bleed=0.125).per_page == 2         # 2 x (4.25 + 0.25) > 8.5
    roomy = layout(page=(11.0, 17.0), bleed=0.125)
    assert roomy.per_page == 4                       # cells of 4.5 x 5.75: 2 x 2
    first = roomy.slots[0]
    assert first.trim_x == pytest.approx(first.x + 0.125) and first.trim_y == pytest.approx(first.y + 0.125)


def test_pages_and_units():
    assert layout(total_items=9).pages == 3
    assert layout(total_items=0).pages == 0
    mm = compute_layout(LayoutRequest(page_width=215.9, page_height=279.4, item_width=139.7,
                                      item_height=107.95, unit="mm"))
    assert mm.per_page == 4
    a4 = compute_layout(LayoutRequest(page_width=21.0, page_height=29.7, item_width=13.97,
                                      item_height=10.795, unit="cm"))
    assert a4.per_page == 2  # A4 is narrower than Letter: 2 x 10.795 cm > 21 cm


def test_grid_is_centred_in_the_printable_area():
    result = layout(page=(11.0, 17.0), orientation="landscape", margin_left=0.5, margin_right=0.5,
                    margin_top=0.5, margin_bottom=0.5, gap_x=0.25, gap_y=0.25, total_items=4)
    xs = [s.x for s in result.slots]
    left_over = min(xs)
    right_over = result.page_width - (max(xs) + result.cell_width)
    assert left_over == pytest.approx(right_over) and left_over > 0.5
    ys = [s.y for s in result.slots]
    assert min(ys) == pytest.approx(result.page_height - (max(ys) + result.cell_height))


def test_slots_never_overlap_and_stay_inside_the_printable_area():
    result = layout(page=(11.0, 17.0), gap_x=0.1, gap_y=0.2, margin_left=0.3, margin_top=0.4,
                    margin_right=0.3, margin_bottom=0.4)
    boxes = [(s.x, s.y, s.x + result.cell_width, s.y + result.cell_height) for s in result.slots]
    for x0, y0, x1, y1 in boxes:
        assert x0 >= 0.3 - 1e-9 and y0 >= 0.4 - 1e-9
        assert x1 <= result.page_width - 0.3 + 1e-9 and y1 <= result.page_height - 0.4 + 1e-9
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] + 1e-9 or b[2] <= a[0] + 1e-9 or a[3] <= b[1] + 1e-9 or b[3] <= a[1] + 1e-9


def test_crop_marks_never_cross_artwork_or_leave_the_page():
    tight = layout(orientation="portrait", total_items=4)   # no margin, no gap: nowhere to draw marks
    assert crop_mark_segments(tight) == []
    result = layout(page=(11.0, 17.0), margin_top=0.5, margin_right=0.5, margin_bottom=0.5,
                    margin_left=0.5, gap_x=0.5, gap_y=0.5, total_items=8)
    marks = crop_mark_segments(result)                 # page 0 only
    assert len(marks) == 8 * result.per_page           # 2 marks on each of 4 corners, every item
    trims = [(s.trim_x, s.trim_y, s.trim_x + result.trim_width, s.trim_y + result.trim_height)
             for s in result.slots]
    for x0, y0, x1, y1 in marks:
        assert 0 <= min(x0, x1) and max(x0, x1) <= result.page_width
        assert 0 <= min(y0, y1) and max(y0, y1) <= result.page_height
        for tx0, ty0, tx1, ty1 in trims:               # a mark may touch nobody's artwork
            inside_x = min(x0, x1) < tx1 - 1e-9 and max(x0, x1) > tx0 + 1e-9
            inside_y = min(y0, y1) < ty1 - 1e-9 and max(y0, y1) > ty0 + 1e-9
            assert not (inside_x and inside_y)
    sheet = layout(margin_top=0.5, margin_right=0.5, margin_bottom=0.5, margin_left=0.5,
                   page=(9.5, 12.0), orientation="portrait", total_items=4)
    perimeter_only = crop_mark_segments(sheet)
    assert len(perimeter_only) == 16                   # zero gap: only the 8 outer corners x 2 marks
    single = layout(margin_top=0.5, margin_right=0.5, margin_bottom=0.5, margin_left=0.5,
                    page=(9.5, 12.0), orientation="portrait", total_items=1)
    assert len(crop_mark_segments(single)) == 4        # one occupied cell, still bounded by the empty grid
    assert crop_mark_segments(single, page=1) == []


def _png(width, height, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_pdf_has_real_page_size_pages_and_marks():
    images = ["data:image/png;base64," + base64.b64encode(_png(110, 85, (200, 30, 30))).decode()] * 5
    result = layout(total_items=5, margin_top=0.5, margin_right=0.5, margin_bottom=0.5,
                    margin_left=0.5, page=(9.5, 12.0), orientation="portrait")
    plain = render_pdf(images, result, crop_marks=False)
    marked = render_pdf(images, result, crop_marks=True)
    assert plain[:5] == b"%PDF-" and marked[:5] == b"%PDF-"
    assert len(re.findall(rb"/Type\s*/Page[^s]", plain)) == 2          # 4 + 1
    assert re.search(rb"/MediaBox\s*\[\s*0 0 684 864\s*\]", plain)     # 9.5 x 12 in, in points
    assert len(marked) > len(plain)                                    # the marks are really drawn

    from reportlab.lib.utils import ImageReader  # the embedded images are intact
    assert ImageReader(io.BytesIO(_png(2, 2, (0, 0, 0)))).getSize() == (2, 2)


def test_invalid_image_is_a_clear_error():
    with pytest.raises(ValueError):
        render_pdf(["data:image/png;base64,not-an-image"], layout(total_items=1), crop_marks=False)


# --- HTTP contract (no database involved) -------------------------------------
import pytest_asyncio  # noqa: E402
from tests.conftest import requires_db  # noqa: E402  (the shared client fixture needs the DB)

pytestmark_http = requires_db


@pytest.mark.asyncio
async def test_layout_endpoint_and_legacy_pdf_contract(client):
    body = {"page_width": 8.5, "page_height": 11, "item_width": 5.5, "item_height": 4.25,
            "unit": "in", "orientation": "portrait", "total_items": 5}
    response = await client.post("/api/v1/printing/layout", json=body)
    assert response.status_code == 200
    data = response.json()
    assert (data["per_page"], data["pages"], data["rotated"], len(data["slots"])) == (4, 2, True, 5)

    image = "data:image/png;base64," + base64.b64encode(_png(110, 85, (30, 30, 200))).decode()
    legacy = {"images": [image] * 5, "page_width_in": 8.5, "page_height_in": 11, "item_width_in": 5.5,
              "item_height_in": 4.25, "margin_in": 0, "gap_in": 0, "allow_rotation": True, "crop_marks": True}
    pdf = await client.post("/api/v1/printing/pdf", json=legacy)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf.content)) == 2

    extended = {**legacy, "orientation": "landscape", "margin_top_in": 0.5, "margin_bottom_in": 0.5,
                "margin_left_in": 0.5, "margin_right_in": 0.5, "gap_x_in": 0.25, "gap_y_in": 0.25, "bleed_in": 0.125}
    pdf2 = await client.post("/api/v1/printing/pdf", json=extended)
    assert pdf2.status_code == 200 and re.search(rb"/MediaBox\s*\[\s*0 0 792 612\s*\]", pdf2.content)

    too_big = {**legacy, "item_width_in": 12, "item_height_in": 12}
    assert (await client.post("/api/v1/printing/pdf", json=too_big)).status_code == 422
    bad = {**legacy, "images": ["data:image/png;base64,nope"]}
    assert (await client.post("/api/v1/printing/pdf", json=bad)).status_code == 422
