"""The five award templates made only for the Adventurer Club (aventureros-*), written as engine templates.

    python tools/build_adventurer_templates.py [--out DIR]     # default: templates/certificates/

Each design is code here (shapes, slots, fit contract), not a design package: change it here and run the
script; `template.svg`, `strings.<locale>.json` and `meta.json` are its output and are not edited by hand
(a test rebuilds them and requires identical files). Design units are 1100 x 850 inside
`<g transform="scale(0.72)">` (Letter landscape, viewBox 792 x 612 pt), the convention of the v4 element
templates, so the engine's fit contract (data-fit="shrink-wrap", docs/CERTIFICADOS_V4.md) applies unchanged.
Colours: Adventurer Brand Book 2016 (docs/AVENTUREROS_MARCA.md). Docs: docs/CERTIFICADOS_V4.md «Aventureros».
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "templates" / "certificates"

# Adventurer Brand Book 2016, «dibujado» colours (docs/AVENTUREROS_MARCA.md) + neutrals
BLUE, SCARLET, GREEN, SKY, OCHRE = "#2363AF", "#7F1416", "#40AA48", "#66CBE0", "#D09833"
FOREST, OLIVE, LIME = "#0C6735", "#558A3E", "#BDD630"
NAVY, INK, MUTED = "#173F73", "#1F2A37", "#56606E"
SUN, WARM_RED, ORANGE = "#F5C640", "#DE4B3A", "#EE9638"
QR_BOX = ("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA3MCA3MCI+"
          "PHJlY3Qgd2lkdGg9IjcwIiBoZWlnaHQ9IjcwIiBmaWxsPSIjZTZlOGU1Ii8+PC9zdmc+")

LOCALES = ("es", "en", "pt", "fr")
STRINGS = {
    "title": {"es": "Reconocimiento de Aventureros", "en": "Adventurer Award",
              "pt": "Reconhecimento de Aventureiros", "fr": "Distinction des Aventuriers"},
    "title_top": {"es": "Reconocimiento de", "en": "Adventurer", "pt": "Reconhecimento de", "fr": "Distinction des"},
    "title_main": {"es": "Aventureros", "en": "Award", "pt": "Aventureiros", "fr": "Aventuriers"},
    "brand": {"es": "Club de Aventureros", "en": "Adventurer Club", "pt": "Clube de Aventureiros",
              "fr": "Club des Aventuriers"},
    "awarded": {"es": "Se otorga a", "en": "Awarded to", "pt": "Concedido a", "fr": "Décernée à"},
    "completion": {"es": "por completar los requisitos de", "en": "for completing the requirements of",
                   "pt": "por completar os requisitos de", "fr": "pour avoir rempli les conditions de"},
    "director": {"es": "Director(a) del club", "en": "Club Director", "pt": "Diretor(a) do clube",
                 "fr": "Direction du club"},
    "instructor": {"es": "Instructor(a)", "en": "Instructor", "pt": "Instrutor(a)", "fr": "Instructeur"},
    "qr": {loc: "QR" for loc in LOCALES},
    "church_name": {"es": "Iglesia Adventista del Séptimo Día", "en": "Seventh-day Adventist Church",
                    "pt": "Igreja Adventista do Sétimo Dia", "fr": "Église adventiste du septième jour"},
}


def text(el_id, x, y, size, *, w, mn, lines=1, lh=1.2, family="Lato", weight=400, fill=INK, anchor="start",
         string=None, required=False, fmt=None, fallback=None, upper=False, balanced=False, hide_if=None, sample=""):
    attrs = [f'id="{el_id}"', f'x="{x:g}"', f'y="{y:g}"', f'font-family="{family}"', f'font-weight="{weight}"',
             f'font-size="{size:g}"', f'fill="{fill}"']
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    attrs += ['data-fit="shrink-wrap"', f'data-max-width="{w:g}"', f'data-min-size="{mn:g}"',
              f'data-max-lines="{lines}"', f'data-line-height="{lh:g}"']
    if balanced:
        attrs.append('data-wrap="balanced"')
    if upper:
        attrs.append('data-text-transform="uppercase"')
    if string:
        attrs.append(f'data-string="{string}"')
        sample = STRINGS[string]["es"]
    if fallback:
        attrs.append(f'data-fallback-string="{fallback}"')
    if fmt:
        attrs.append(f'data-format="{fmt}"')
    if required:
        attrs.append('data-required="true"')
    if hide_if:
        attrs.append(f'data-hide-if-image="{hide_if}"')
    shown = sample.upper() if upper else sample
    return f"<text {' '.join(attrs)}>{escape(shown)}</text>"


def image(el_id, x, y, w, h, par="xMidYMid meet", placeholder=None):
    extra = f' data-placeholder-href="{placeholder}"' if placeholder else ""
    return f'<image id="{el_id}" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" preserveAspectRatio="{par}"{extra} href=""/>'


def signatures(cx_dir, cx_ins, line_y, half=125, *, name_size=17, label_size=13, line_color=INK, name_fill=INK,
               label_fill=MUTED, label_family="Lato", label_weight=400, upper_label=False):
    out = []
    for cx, who, role in ((cx_dir, "director", "director"), (cx_ins, "instructor", "instructor")):
        out.append(f'<path d="M{cx - half:g} {line_y:g}h{2 * half:g}" stroke="{line_color}" stroke-width="1.4"/>')
        out.append(image(f"signature_{who}", cx - half, line_y - 54, 2 * half, 50, "xMidYMax meet"))
        out.append(text(f"{who}_name", cx, line_y + 26, name_size, w=2 * half, mn=11, fill=name_fill, anchor="middle"))
        out.append(text(f"t_{role}_label", cx, line_y + 26 + label_size + 9, label_size, w=2 * half, mn=10,
                        fill=label_fill, anchor="middle", string=role, family=label_family, weight=label_weight,
                        upper=upper_label))
    return out


def qr(x, y, *, folio_anchor="middle", folio_x=None, folio_fill=MUTED, size=76):
    return [
        image("qr", x, y, size, size, placeholder=QR_BOX),
        text("t_qr_placeholder_label", x + size / 2, y + size / 2 + 4, 12, w=40, mn=12, fill="#65706b",
             anchor="middle", string="qr", hide_if="qr"),
        text("certificate_no", folio_x if folio_x is not None else x + size / 2, y + size + 18, 10, w=150, mn=8,
             fill=folio_fill, anchor=folio_anchor),
    ]


def star(cx, cy, r, fill, points=5, inner=0.45, rot=-90):
    pts = []
    for i in range(points * 2):
        rad = r if i % 2 == 0 else r * inner
        a = math.radians(rot + i * 180 / points)
        pts.append(f"{cx + rad * math.cos(a):.1f} {cy + rad * math.sin(a):.1f}")
    return f'<path d="M{"L".join(pts)}Z" fill="{fill}"/>'


def sparkle(cx, cy, r, fill):
    """Four-point sparkle with concave sides."""
    k = r * 0.18
    return (f'<path d="M{cx:g} {cy - r:g}Q{cx + k:g} {cy - k:g} {cx + r:g} {cy:g}Q{cx + k:g} {cy + k:g} {cx:g} {cy + r:g}'
            f'Q{cx - k:g} {cy + k:g} {cx - r:g} {cy:g}Q{cx - k:g} {cy - k:g} {cx:g} {cy - r:g}Z" fill="{fill}"/>')


def cloud(cx, cy, s, fill="#FFFFFF", stroke="#CFE8F1"):
    parts = [(-0.62, 0.12, 0.38), (-0.2, -0.18, 0.5), (0.32, -0.05, 0.42), (0.7, 0.18, 0.3)]
    circles = "".join(f'<circle cx="{cx + dx * s:.1f}" cy="{cy + dy * s:.1f}" r="{r * s:.1f}"/>' for dx, dy, r in parts)
    base = f'<rect x="{cx - 0.95 * s:.1f}" y="{cy + 0.05 * s:.1f}" width="{1.9 * s:.1f}" height="{0.45 * s:.1f}" rx="{0.22 * s:.1f}"/>'
    return (f'<g fill="{fill}" stroke="{stroke}" stroke-width="10" stroke-linejoin="round">{circles}{base}</g>'
            f'<g fill="{fill}">{circles}{base}</g>')


def rainbow_bar(x, y, seg, h, colors):
    return "".join(f'<rect x="{x + i * seg:g}" y="{y:g}" width="{seg:g}" height="{h:g}" fill="{c}"/>'
                   for i, c in enumerate(colors))


RAINBOW = (WARM_RED, ORANGE, SUN, GREEN, SKY, BLUE)


# --------------------------------------------------------------------------------------------- designs

def arcoiris():
    body = ['<rect width="1100" height="850" fill="#FFFDF7"/>',
            '<rect x="22" y="22" width="1056" height="806" rx="30" fill="none" stroke="#D8EEF4" stroke-width="4"/>']
    # rainbow of the promise, top-left corner
    for i, c in enumerate(RAINBOW):
        r = 300 - i * 22
        body.append(f'<path d="M{r} 0A{r} {r} 0 0 1 0 {r}" fill="none" stroke="{c}" stroke-width="22.5"/>')
    body.append(cloud(236, 40, 64))
    body.append(cloud(26, 236, 62))
    # small rainbow, bottom-right corner
    for i, c in enumerate(RAINBOW):
        r = 150 - i * 13
        body.append(f'<path d="M{1100 - r} 850A{r} {r} 0 0 1 1100 {850 - r}" fill="none" stroke="{c}" stroke-width="13.5"/>')
    for cx, cy, r, c in ((905, 92, 16, SUN), (968, 150, 10, SKY), (1016, 86, 12, ORANGE), (850, 160, 8, GREEN),
                         (118, 520, 11, SUN), (80, 600, 7, SKY), (1012, 380, 9, SUN)):
        body.append(sparkle(cx, cy, r, c))
    body.append(image("emblem", 466, 40, 168, 156))
    body.append(text("church_name", 550, 224, 15, w=520, mn=11, fill=MUTED, anchor="middle", fallback="church_name",
                     sample="Iglesia Adventista del Séptimo Día"))
    body.append(text("t_title", 550, 272, 40, w=720, mn=24, family="Poppins", weight=900, fill=BLUE,
                     anchor="middle", string="title", upper=True))
    body.append(text("association_name", 550, 304, 15, w=640, mn=10, fill=MUTED, anchor="middle", upper=True))
    body.append(text("t_awarded", 550, 358, 21, w=640, mn=14, fill=INK, anchor="middle", string="awarded"))
    body.append(text("recipient_name", 550, 426, 58, w=820, mn=26, family="Poppins", weight=700, fill=NAVY,
                     anchor="middle", required=True))
    body.append(rainbow_bar(430, 450, 40, 7, RAINBOW))
    body.append(text("t_completion", 550, 497, 19, w=660, mn=13, lines=2, lh=1.25, fill=INK, anchor="middle",
                     string="completion"))
    body.append(image("honor_patch", 878, 445, 150, 150))
    body.append(text("honor_name", 550, 550, 34, w=600, mn=20, lines=2, lh=1.15, family="Poppins", weight=700,
                     fill=BLUE, anchor="middle", balanced=True, required=True))
    body.append(text("issued_date", 550, 640, 17, w=420, mn=12, fill=MUTED, anchor="middle", fmt="date-long",
                     required=True))
    body += signatures(290, 810, 728)
    body += qr(512, 674)
    return body


def paisaje():
    body = ['<defs><linearGradient id="cielo" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#FFFFFF"/><stop offset="0.45" stop-color="#FFFFFF"/>'
            '<stop offset="1" stop-color="#DDF0F7"/></linearGradient></defs>',
            '<rect width="1100" height="850" fill="url(#cielo)"/>']
    # sun
    body.append(f'<circle cx="968" cy="118" r="54" fill="{SUN}"/>')
    for i in range(12):
        a = math.radians(i * 30 + 15)
        x1, y1 = 968 + 70 * math.cos(a), 118 + 70 * math.sin(a)
        x2, y2 = 968 + 92 * math.cos(a), 118 + 92 * math.sin(a)
        body.append(f'<path d="M{x1:.1f} {y1:.1f}L{x2:.1f} {y2:.1f}" stroke="{SUN}" stroke-width="9" stroke-linecap="round"/>')
    body.append(cloud(812, 92, 46, stroke="#E2F1F6"))
    # hills, back to front
    body.append(f'<path d="M0 772C170 742 330 748 500 766S860 734 1100 726V850H0Z" fill="{LIME}"/>')
    body.append(f'<path d="M0 806C200 778 380 784 560 808S900 786 1100 776V850H0Z" fill="{GREEN}"/>')
    body.append(f'<path d="M0 828C140 812 300 818 430 850H0Z" fill="{FOREST}"/>')
    body.append(f'<path d="M760 850C860 816 980 806 1100 812V850Z" fill="{OLIVE}"/>')
    # winding path into the hills
    body.append(f'<path d="M520 850C580 828 662 818 640 800C622 786 652 776 700 768L712 771C676 780 660 788 676 802'
                f'C700 824 660 838 676 850Z" fill="{OCHRE}"/>')
    # pines and a round tree, clear of the signature block
    for x, base, h, c in ((40, 822, 56, FOREST), (776, 800, 64, FOREST), (818, 806, 46, OLIVE)):
        body.append(f'<path d="M{x} {base - h}L{x + h * 0.36:.1f} {base}H{x - h * 0.36:.1f}Z" fill="{c}"/>')
    body.append(f'<path d="M884 772v32" stroke="{SCARLET}" stroke-width="6" stroke-linecap="round"/>'
                f'<ellipse cx="884" cy="752" rx="22" ry="28" fill="{GREEN}"/>')
    body.append(image("emblem", 68, 50, 172, 160))
    body.append(text("t_brand", 268, 116, 28, w=500, mn=18, family="Poppins", weight=700, fill=FOREST, string="brand"))
    body.append(text("church_name", 268, 148, 16, w=500, mn=11, fill=MUTED, fallback="church_name",
                     sample="Iglesia Adventista del Séptimo Día"))
    body.append(text("association_name", 268, 175, 14, w=500, mn=10, fill=MUTED, upper=True))
    body.append(text("t_title", 80, 290, 40, w=760, mn=24, family="Poppins", weight=900, fill=BLUE,
                     string="title", upper=True))
    body.append(text("t_awarded", 80, 344, 21, w=740, mn=14, fill=INK, string="awarded"))
    body.append(text("recipient_name", 80, 410, 56, w=770, mn=26, family="Poppins", weight=700, fill=NAVY,
                     required=True))
    body.append(f'<path d="M82 434H500" stroke="{OCHRE}" stroke-width="5" stroke-linecap="round" stroke-dasharray="0 14"/>')
    body.append(text("t_completion", 80, 478, 19, w=760, mn=13, lines=2, lh=1.25, fill=INK, string="completion"))
    body.append(image("honor_patch", 872, 262, 160, 160))
    body.append(text("honor_name", 80, 530, 34, w=740, mn=20, lines=2, lh=1.15, family="Poppins", weight=700,
                     fill=FOREST, balanced=True, required=True))
    body.append(text("issued_date", 80, 612, 17, w=500, mn=12, fill=MUTED, fmt="date-long", required=True))
    body += signatures(205, 530, 668)
    body.append('<rect x="928" y="604" width="112" height="136" rx="14" fill="#FFFFFF" stroke="#CFE3EA" stroke-width="2"/>')
    body += qr(946, 618)
    return body


def estrellas():
    night = "#1B3A6B"
    body = ['<rect width="1100" height="850" fill="#FFFDF7"/>',
            f'<rect width="1100" height="250" fill="{night}"/>',
            f'<rect y="834" width="1100" height="16" fill="{night}"/>']
    # crescent moon
    body.append(f'<circle cx="118" cy="118" r="46" fill="{SUN}"/><circle cx="140" cy="102" r="40" fill="{night}"/>')
    for cx, cy, r, c in ((236, 60, 9, SUN), (300, 168, 6, "#FFFFFF"), (196, 200, 7, SUN), (870, 54, 8, "#FFFFFF"),
                         (944, 120, 13, SUN), (1030, 66, 7, SUN), (1012, 196, 9, "#FFFFFF"), (842, 196, 6, SUN),
                         (60, 214, 6, "#FFFFFF"), (1062, 140, 5, "#FFFFFF")):
        body.append(star(cx, cy, r, c))
    for cx, cy in ((360, 40), (400, 214), (690, 30), (760, 222), (330, 110), (1078, 30), (40, 60), (900, 230), (770, 120)):
        body.append(f'<circle cx="{cx}" cy="{cy}" r="2.6" fill="#FFFFFF" opacity="0.85"/>')
    for cx, cy, r, c in ((70, 420, 10, SUN), (110, 760, 8, SKY), (1030, 430, 9, SKY), (1050, 720, 11, SUN),
                         (60, 600, 6, SKY), (1000, 560, 6, SUN)):
        body.append(star(cx, cy, r, c))
    # medallion for the emblem (white, so the full-colour logo sits on a neutral ground)
    body.append(f'<circle cx="550" cy="256" r="108" fill="{SUN}"/><circle cx="550" cy="256" r="101" fill="#FFFFFF"/>')
    body.append(image("emblem", 478, 189, 144, 134))
    body.append(text("church_name", 550, 50, 14, w=560, mn=10, fill="#CFDDF2", anchor="middle",
                     fallback="church_name", sample="Iglesia Adventista del Séptimo Día"))
    body.append(text("t_title", 550, 100, 40, w=700, mn=24, family="Poppins", weight=900, fill="#FFFFFF",
                     anchor="middle", string="title", upper=True))
    body.append(text("association_name", 550, 134, 15, w=560, mn=10, fill="#CFDDF2", anchor="middle", upper=True))
    body.append(text("t_awarded", 550, 400, 21, w=640, mn=14, fill=INK, anchor="middle", string="awarded"))
    body.append(text("recipient_name", 550, 466, 56, w=860, mn=26, family="Poppins", weight=700, fill=NAVY,
                     anchor="middle", required=True))
    for dx, r in ((-34, 7), (0, 10), (34, 7)):
        body.append(star(550 + dx, 494, r, "#E8B92F"))
    body.append(text("t_completion", 550, 536, 19, w=640, mn=13, lines=2, lh=1.25, fill=INK, anchor="middle",
                     string="completion"))
    body.append(image("honor_patch", 104, 500, 150, 150))
    body.append(text("honor_name", 550, 586, 34, w=560, mn=20, lines=2, lh=1.15, family="Poppins", weight=700,
                     fill=BLUE, anchor="middle", balanced=True, required=True))
    body.append(text("issued_date", 550, 672, 17, w=420, mn=12, fill=MUTED, anchor="middle", fmt="date-long",
                     required=True))
    body += signatures(300, 800, 748, half=120)
    body += qr(934, 540)
    return body


def flower(cx, cy, r, petal, center):
    petals = "".join(
        f'<circle cx="{cx + r * math.cos(math.radians(a)):.1f}" cy="{cy + r * math.sin(math.radians(a)):.1f}" r="{r * 0.72:.1f}"/>'
        for a in range(0, 360, 60))
    return f'<g fill="{petal}">{petals}</g><circle cx="{cx}" cy="{cy}" r="{r * 0.7:.1f}" fill="{center}"/>'


def leaf(cx, cy, rx, ry, angle, fill=GREEN):
    return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" transform="rotate({angle} {cx} {cy})" fill="{fill}"/>'


def jardin():
    body = ['<rect width="1100" height="850" fill="#FFFFFF"/>',
            '<rect width="330" height="850" fill="#EEF6DC"/>']
    # sun peeking from the top-right corner
    body.append(f'<circle cx="1100" cy="0" r="104" fill="{SUN}"/>')
    for i in range(7):
        a = math.radians(95 + i * 13.3)
        x1, y1 = 1100 + 122 * math.cos(a), 0 + 122 * math.sin(a)
        x2, y2 = 1100 + 150 * math.cos(a), 0 + 150 * math.sin(a)
        body.append(f'<path d="M{x1:.1f} {y1:.1f}L{x2:.1f} {y2:.1f}" stroke="{SUN}" stroke-width="10" stroke-linecap="round"/>')
    # garden growing at the foot of the panel
    stems = ((60, 690, WARM_RED, FOREST), (120, 640, SKY, SCARLET), (182, 700, SUN, SCARLET), (246, 655, ORANGE, FOREST),
             (300, 720, BLUE, SUN))
    for x, top, petal, center in stems:
        body.append(f'<path d="M{x} 850V{top}" stroke="{OLIVE}" stroke-width="6" stroke-linecap="round"/>')
        mid = (850 + top) / 2
        body.append(leaf(x - 16, mid + 10, 18, 8, -30))
        body.append(leaf(x + 16, mid - 14, 18, 8, 30, OLIVE))
        body.append(flower(x, top, 17, petal, center))
    body.append(f'<path d="M0 850V826C40 812 70 830 110 818S180 812 220 824 290 812 330 820V850Z" fill="{LIME}"/>')
    # emblem card
    body.append('<rect x="56" y="54" width="218" height="204" rx="26" fill="#FFFFFF"/>')
    body.append(image("emblem", 79, 76, 172, 160))
    body.append(text("t_brand", 165, 304, 22, w=250, mn=15, family="Poppins", weight=700, fill=FOREST,
                     anchor="middle", string="brand"))
    body.append(text("church_name", 165, 334, 14, w=250, mn=10, lines=2, lh=1.25, fill=MUTED, anchor="middle",
                     fallback="church_name", sample="Iglesia Adventista del Séptimo Día"))
    body.append(text("association_name", 165, 390, 13, w=250, mn=9, lines=2, lh=1.25, fill=MUTED, anchor="middle",
                     upper=True))
    body.append(text("t_title_top", 392, 128, 24, w=560, mn=16, family="Poppins", weight=700, fill="#A86E16",
                     string="title_top", upper=True))
    body.append(text("t_title_main", 388, 204, 70, w=560, mn=40, family="Poppins", weight=900, fill=BLUE,
                     string="title_main"))
    body.append(text("t_awarded", 392, 282, 21, w=640, mn=14, fill=INK, string="awarded"))
    body.append(text("recipient_name", 390, 350, 54, w=650, mn=24, family="Poppins", weight=700, fill=NAVY,
                     required=True))
    body.append(f'<path d="M392 376H640" stroke="{GREEN}" stroke-width="3" stroke-linecap="round"/>')
    body.append(leaf(656, 370, 14, 6, -25) + leaf(656, 384, 14, 6, 25, OLIVE))
    body.append(text("t_completion", 392, 422, 19, w=640, mn=13, lines=2, lh=1.25, fill=INK, string="completion"))
    body.append(image("honor_patch", 878, 400, 156, 156))
    body.append(text("honor_name", 390, 474, 34, w=470, mn=20, lines=2, lh=1.15, family="Poppins", weight=700,
                     fill=FOREST, balanced=True, required=True))
    body.append(text("issued_date", 392, 596, 17, w=460, mn=12, fill=MUTED, fmt="date-long", required=True))
    body += signatures(505, 785, 712, half=115)
    body += qr(958, 672)
    return body


def geometrico():
    body = ['<rect width="1100" height="850" fill="#FFFFFF"/>']
    m = 90
    # top-right quilt of shapes (3 x 2 modules)
    x0 = 830
    body += [
        f'<path d="M{x0} 0H{x0 + m}A{m / 2} {m / 2} 0 0 1 {x0} 0Z" fill="{OCHRE}"/>',
        f'<rect x="{x0 + m}" y="0" width="{m}" height="{m}" fill="{SKY}"/><circle cx="{x0 + 1.5 * m}" cy="{m / 2}" r="26" fill="{BLUE}"/>',
        f'<path d="M{x0 + 2 * m} 0H1100V{m}A{m} {m} 0 0 1 {x0 + 2 * m} 0Z" fill="{GREEN}"/>',
        f'<path d="M{x0 + m} {2 * m}L{x0 + 1.5 * m} {m}L{x0 + 2 * m} {2 * m}Z" fill="{LIME}"/>',
        f'<circle cx="{x0 + 2.5 * m}" cy="{1.5 * m}" r="{m / 2}" fill="{WARM_RED}"/>',
        f'<path d="M{x0 + 2 * m} {2 * m}H1100V{3 * m}A{m} {m} 0 0 1 {x0 + 2 * m} {2 * m}Z" fill="{BLUE}"/>',
    ]
    for i in range(3):
        for j in range(3):
            body.append(f'<circle cx="{x0 + 22 + j * 22}" cy="{m + 24 + i * 22}" r="4.5" fill="{BLUE}"/>')
    # bottom-left quilt (3 x 2 modules)
    y0 = 850 - 2 * m
    body += [
        f'<rect x="0" y="{y0}" width="{m}" height="{m}" fill="{SUN}"/><path d="M0 {y0 + m}A{m} {m} 0 0 1 {m} {y0}V{y0 + m}Z" fill="{ORANGE}"/>',
        f'<path d="M0 {y0 + m}H{m}V850A{m / 2} {m / 2} 0 0 1 0 850Z" fill="#FFFFFF"/>',
        f'<circle cx="{m / 2}" cy="{y0 + 1.5 * m}" r="{m / 2}" fill="{BLUE}"/>',
        f'<rect x="{m}" y="{y0 + m}" width="{m}" height="{m}" fill="{GREEN}"/><path d="M{m} 850L{1.5 * m} {y0 + m + 10}L{2 * m} 850Z" fill="{LIME}"/>',
        f'<path d="M{m} {y0 + m}A{m / 2} {m / 2} 0 0 1 {2 * m} {y0 + m}Z" fill="{SKY}"/>',
        f'<path d="M{2 * m} 850V{y0 + m}A{m} {m} 0 0 1 {3 * m} 850Z" fill="{WARM_RED}"/>',
    ]
    body.append(f'<path d="M{2.25 * m} {y0 + 0.75 * m}l14 -14l14 14l14 -14l14 14l14 -14" fill="none" stroke="{OCHRE}" '
                f'stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>')
    body.append(image("emblem", 66, 48, 184, 171))
    body.append(text("t_brand", 282, 118, 26, w=520, mn=17, family="Poppins", weight=700, fill=SCARLET, string="brand",
                     upper=True))
    body.append(text("church_name", 282, 150, 16, w=520, mn=11, fill=MUTED, fallback="church_name",
                     sample="Iglesia Adventista del Séptimo Día"))
    body.append(text("association_name", 282, 177, 14, w=520, mn=10, fill=MUTED, upper=True))
    body.append(text("t_title", 80, 300, 46, w=740, mn=26, family="Poppins", weight=900, fill=BLUE, string="title",
                     upper=True))
    body.append(rainbow_bar(82, 322, 36, 8, (WARM_RED, SUN, GREEN, SKY, BLUE)))
    body.append(text("t_awarded", 80, 380, 21, w=740, mn=14, fill=INK, string="awarded"))
    body.append(text("recipient_name", 80, 448, 58, w=760, mn=26, family="Poppins", weight=700, fill=NAVY,
                     required=True))
    body.append(text("t_completion", 80, 506, 19, w=740, mn=13, lines=2, lh=1.25, fill=INK, string="completion"))
    body.append(image("honor_patch", 862, 392, 168, 168))
    body.append(text("honor_name", 80, 558, 34, w=720, mn=20, lines=2, lh=1.15, family="Poppins", weight=700,
                     fill=FOREST, balanced=True, required=True))
    body.append(text("issued_date", 80, 648, 17, w=460, mn=12, fill=MUTED, fmt="date-long", required=True))
    body += signatures(430, 712, 742, half=115)
    body += qr(950, 690)
    return body


DESIGNS = {
    "aventureros-arcoiris": (arcoiris, {"es": "Aventureros · Arcoíris de la promesa", "en": "Adventurers · Rainbow promise"},
                             "Arcoíris en dos esquinas, nubes y destellos; composición centrada."),
    "aventureros-paisaje": (paisaje, {"es": "Aventureros · Colinas y camino", "en": "Adventurers · Hills and trail"},
                            "Sol, colinas y un camino que recuerdan el paisaje del emblema; texto a la izquierda."),
    "aventureros-estrellas": (estrellas, {"es": "Aventureros · Cielo de estrellas", "en": "Adventurers · Starry sky"},
                              "Franja de cielo nocturno con luna y estrellas; emblema en un medallón blanco."),
    "aventureros-jardin": (jardin, {"es": "Aventureros · Jardín", "en": "Adventurers · Garden"},
                           "Columna de jardín con flores y el emblema en tarjeta blanca; sol en la esquina."),
    "aventureros-geometrico": (geometrico, {"es": "Aventureros · Formas alegres", "en": "Adventurers · Happy shapes"},
                               "Mosaico de formas planas (círculos, triángulos, arcos) en dos esquinas."),
}

EDITABLE = ["awarded", "completion"]


def build(slug: str, out: Path = OUT) -> Path:
    fn, titles, concept = DESIGNS[slug]
    body = fn()
    svg = "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 792 612" '
        f'width="11in" height="8.5in" data-template="{slug}" data-version="1.0">',
        f"<!-- {escape(titles['es'])}: reconocimiento del Club de Aventureros. Unidades del diseño 1100 x 850 dentro del "
        "grupo escalado (Carta horizontal). Contrato de ajuste: docs/CERTIFICADOS_V4.md. -->",
        '<g transform="scale(0.72)">', *body, "</g>", "</svg>", ""])
    folder = out / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "template.svg").write_text(svg, encoding="utf-8")
    used = sorted({k for k in STRINGS if f'data-string="{k}"' in svg or f'data-fallback-string="{k}"' in svg})
    for loc in LOCALES:
        strings = {k: STRINGS[k][loc] for k in used}
        (folder / f"strings.{loc}.json").write_text(json.dumps(strings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = {"title": titles["es"], "kinds": ["honor"], "ministries": ["adventurers"], "engine": "elements",
            "source": f"tools/build_adventurer_templates.py:{fn.__name__}", "locales": list(LOCALES), "editable_strings": EDITABLE}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "README.md").write_text(
        f"«{titles['es']}» ({titles['en']}): reconocimiento del Club de Aventureros, sólo ministerio `adventurers`.\n"
        f"{concept}\n\n"
        f"Generada por `tools/build_adventurer_templates.py` (función `{fn.__name__}`): no editar template.svg ni\n"
        "strings.*.json a mano; cambiar el diseño allí y volver a ejecutar el script.\n"
        "Contrato y datos: `docs/CERTIFICADOS_V4.md` (sección «Reconocimientos de Aventureros»).\n", encoding="utf-8")
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=OUT, help="carpeta de plantillas (por defecto templates/certificates)")
    args = parser.parse_args()
    for slug in DESIGNS:
        print("ok", build(slug, args.out))


if __name__ == "__main__":
    main()
