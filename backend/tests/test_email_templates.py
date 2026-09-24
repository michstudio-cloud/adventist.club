"""Every transactional e-mail renders as one well-formed message in the product's look.

These tests do not judge the design; they pin what a future edit must not break:
structure, the logo, the call to action, escaping, and the absence of leftovers
(`None`, unformatted `{placeholders}`).
"""
import re

import pytest

from app.config import settings
from app.services import email as mail

HOSTILE = '<script>alert("x")</script>'
LINK = "https://app.example.test/path?t=abc&x=1"

# (id, html factory, CTA url expected in the body or None, code expected or None)
CASES = [
    ("verification", lambda: mail.verification_email_html(HOSTILE, "482913", LINK), LINK, "482913"),
    ("password_reset", lambda: mail.password_reset_email_html(HOSTILE, "730511", LINK), LINK, "730511"),
    *[
        (f"welcome_{role}", (lambda r=role: mail.welcome_email_html(HOSTILE, r)), None, None)
        for role in (*mail.ROLE_NAMES, "SOMETHING_NEW")
    ],
    ("club_approved", lambda: mail.club_decision_email_html(HOSTILE, HOSTILE, True, None), None, None),
    ("club_rejected", lambda: mail.club_decision_email_html(HOSTILE, HOSTILE, False, HOSTILE), None, None),
    ("club_rejected_no_reason", lambda: mail.club_decision_email_html("Ana", "Orión", False, None), None, None),
    ("mfa_reset", lambda: mail.mfa_reset_email_html(HOSTILE, HOSTILE), None, None),
    ("recovery_code_used", lambda: mail.recovery_code_used_email_html(HOSTILE), None, None),
    ("club_invitation", lambda: mail.club_invitation_email_html(HOSTILE, "INSTRUCTOR", LINK, HOSTILE), LINK, None),
    ("consent_request", lambda: mail.consent_request_email_html(HOSTILE, HOSTILE, LINK), LINK, None),
    ("pending_one", lambda: mail.pending_requests_email_html(HOSTILE, HOSTILE, 1, LINK), LINK, None),
    ("pending_many", lambda: mail.pending_requests_email_html(HOSTILE, HOSTILE, 7, LINK), LINK, None),
    ("membership_approved", lambda: mail.membership_decision_email_html(HOSTILE, HOSTILE, True, None), None, None),
    ("membership_rejected", lambda: mail.membership_decision_email_html(HOSTILE, HOSTILE, False, HOSTILE), None, None),
    ("letter_submitted", lambda: mail.letter_submitted_email_html(HOSTILE, HOSTILE, "CLUB_DIRECTOR", LINK), LINK, None),
    ("letter_authorized", lambda: mail.letter_decision_email_html(HOSTILE, "AUTHORIZED", "31/12/2027", None), None, None),
    ("letter_authorized_no_date", lambda: mail.letter_decision_email_html("Ana", "AUTHORIZED", None, None), None, None),
    ("letter_revoked", lambda: mail.letter_decision_email_html(HOSTILE, "REVOKED", None, HOSTILE), None, None),
    ("letter_rejected", lambda: mail.letter_decision_email_html(HOSTILE, "REJECTED", None, HOSTILE), None, None),
    ("letter_expiring", lambda: mail.letter_expiring_email_html(HOSTILE, HOSTILE, 30), None, None),
    *[
        (
            f"progress_{kind}",
            (lambda k=kind: mail.progress_email_html(HOSTILE, k, HOSTILE, HOSTILE, LINK)),
            LINK,
            None,
        )
        for kind in (*mail.PROGRESS_SUBJECTS, "SOMETHING_NEW")
    ],
    *[
        (
            f"progress_program_{kind}",
            (lambda k=kind: mail.progress_email_html(HOSTILE, k, HOSTILE, HOSTILE, LINK, "program")),
            LINK,
            None,
        )
        for kind in mail.PROGRESS_SUBJECTS_PROGRAM
    ],
    # «Avisos»: the reviewers' digest, and the member's hours and points (progress notices,
    # switched off by the same preference, so they carry its footnote).
    ("pending_reviews_one", lambda: mail.pending_reviews_email_html(HOSTILE, HOSTILE, 1, LINK), LINK, None),
    ("pending_reviews_many", lambda: mail.pending_reviews_email_html(HOSTILE, HOSTILE, 12, LINK), LINK, None),
    ("progress_hours", lambda: mail.hours_approved_email_html(HOSTILE, 2.5, 1, LINK), LINK, None),
    ("progress_hours_service_only", lambda: mail.hours_approved_email_html(HOSTILE, 3, 0, LINK), LINK, None),
    ("progress_hours_rejected", lambda: mail.hours_rejected_email_html(HOSTILE, 2.5, 0, HOSTILE, LINK), LINK, None),
    ("progress_hours_rejected_no_note", lambda: mail.hours_rejected_email_html("Ana", 0, 1, None, LINK), LINK, None),
    ("course_pending_reviews", lambda: mail.course_pending_reviews_email_html(HOSTILE, HOSTILE, 3, LINK), LINK, None),
    ("progress_xp", lambda: mail.xp_awarded_email_html(HOSTILE, 10, HOSTILE, HOSTILE, LINK), LINK, None),
    ("certificate_revoked", lambda: mail.certificate_revoked_email_html(HOSTILE, HOSTILE, HOSTILE), None, None),
    ("certificate_revoked_no_honor", lambda: mail.certificate_revoked_email_html("Ana", "F-1", None), None, None),
]


def _without_style(html: str) -> str:
    return re.sub(r"<style>.*?</style>", "", html, flags=re.S)


def _hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]*)"', html)


NO_HOSTILE_INPUT = {"progress_hours_rejected_no_note", "club_rejected_no_reason", "letter_authorized_no_date", "certificate_revoked_no_honor"}


@pytest.mark.parametrize("case_id,factory,cta,code", CASES, ids=[c[0] for c in CASES])
def test_every_template_renders_one_well_formed_message(case_id, factory, cta, code):
    html = factory()
    body = _without_style(html)

    assert html.count("<html") == 1 and html.count("</html>") == 1
    assert '<html lang="es"' in html
    assert '<meta name="color-scheme" content="light dark">' in html
    logo = f"{settings.R2_PUBLIC_URL.rstrip('/')}/adventist-club-fav.png"
    assert f'src="{logo}"' in html and 'alt="Adventist.Club"' in html
    assert html.count("<img") == 1  # the logo, and never an image that carries text
    assert mail.FOOTER_TAGLINE in html
    assert "Recibes este" in html or "aviso de seguridad" in html  # why this mail arrives
    assert "display:none" in html  # preheader

    # A primary button in every message, and the same URL printed for copy/paste.
    assert 'class="ac-btn-link"' in html
    assert "¿El botón no funciona?" in html
    if cta is not None:
        escaped = cta.replace("&", "&amp;")
        assert body.count(f'href="{escaped}"') >= 2  # the button and the plain link
        assert f">{escaped}</a>" in body
    for href in _hrefs(body):
        assert href.startswith(("https://", "http://")), href

    if code is not None:
        assert f">{code}</p>" in html

    # No leftovers from the templating.
    assert "None" not in body
    assert "{" not in body and "}" not in body

    # Whatever a person typed is escaped.
    assert "<script>" not in html
    if case_id not in NO_HOSTILE_INPUT:
        assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html


def test_progress_unsubscribe_note_only_in_progress_mails():
    progress = mail.progress_email_html("Ana", "PROGRESS_READY", "Nudos", None, LINK)
    assert mail.PROGRESS_FOOTNOTE in progress
    for _id, factory, _cta, _code in CASES:
        if not _id.startswith("progress_"):
            assert mail.PROGRESS_FOOTNOTE not in factory(), _id


def test_links_that_are_not_http_never_reach_the_mail():
    html = mail.club_invitation_email_html("Orión", "STUDENT", "javascript:alert(1)", "Ana")
    assert "javascript:" not in html
    assert f'href="{settings.frontend_url}"' in html


def test_welcome_copy_follows_the_product():
    instructor = mail.welcome_email_html("Luis", "INSTRUCTOR")
    assert "crear cursos a partir de las especialidades oficiales" in instructor
    assert "para crear especialidades" not in instructor
    assert "Instructor(a)" in instructor and ">INSTRUCTOR<" not in instructor

    member = mail.welcome_email_html("Ana", "STUDENT")
    assert "Miembro del club" in member
    assert f'href="{settings.frontend_url}/auth/login"' in member
    assert mail.MEMBER_PRODUCT in member

    for role in ("MASTER_GC", "ADMIN_ASSOCIATION", "COORDINATOR_ZONE"):
        staff = mail.welcome_email_html("Dan", role)
        assert role in mail.ROLE_WELCOME_MESSAGES
        assert f'href="{settings.admin_url}/admin"' in staff
        assert mail.STAFF_PRODUCT in staff
    assert "verificación en dos pasos" in mail.welcome_email_html("Dan", "MASTER_GC")


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_palette_keeps_text_contrast_above_aa():
    surfaces = [mail.BG, mail.CARD, *mail.HERO_TONES.values()]
    for ink in (mail.TEXT, mail.TEXT_2, mail.MUTED, mail.EYEBROW, mail.LINK):
        for surface in surfaces:
            assert _contrast(ink, surface) >= 4.5, (ink, surface)
    assert _contrast(mail.BUTTON_TEXT, mail.BUTTON_BG) >= 4.5
    for accent, _glyph in mail.NOTICE_KINDS.values():
        assert _contrast(mail.BUTTON_TEXT, accent) >= 4.5, accent


def test_the_avisos_mails_carry_amounts_and_never_names_of_the_queue():
    digest = mail.pending_reviews_email_html("Ana", "Orión", 3, LINK)
    assert "3 requisitos enviados" in digest and "Orión" in digest
    single = mail.pending_reviews_email_html("Ana", "Orión", 1, LINK)
    assert "un requisito enviado" in single
    hours = mail.hours_approved_email_html("Ana", 2.5, 2, LINK)
    assert "2,5" in hours and "2 asistencias" in hours
    points = mail.xp_awarded_email_html("Ana", 15, "Orión", "participacion", LINK)
    assert "15 XP" in points and "participación" in points
    investiture = mail.progress_email_html("Ana", "PROGRESS_CERTIFIED", "Amigo", None, LINK, "program")
    assert "investidura" in investiture.lower() and "Especialidad" not in investiture
    for html in (digest, hours, points, investiture):
        assert "Club Digital" not in html and "Adventist.Club" in html
