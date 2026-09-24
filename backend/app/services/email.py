"""Transactional email through Resend. Sending never raises: a mail outage
must not fail a registration or a password reset.

The templates follow the product's look (conquistadores.app / admin.adventist.club,
`app/globals.css` of the frontend): the dark app surface, a hero card with a tinted
background where the app has its colour bloom, an uppercase tracked eyebrow, a big
title, muted copy, a white pill button and soft secondary cards with an icon.

E-mail engineering rules the helpers below keep for every message:
- tables for layout, 600px wide at most, every style inline; the only `<style>` block
  carries the `color-scheme` declaration and the dark-mode / Outlook.com tweaks;
- the system font stack, no web fonts, no external CSS or JS;
- one image (the app icon, absolute PNG URL) and never an image that carries text;
- bulletproof pill buttons (a padded link in a table cell, with a VML shape for the
  desktop Outlook) and the plain URL printed below for copy and paste;
- every string that comes from a person (names, clubs, reasons, notes) goes through
  `escape`; links are accepted only as http(s).
"""
import logging
from html import escape
from urllib.parse import urlsplit

import anyio

from app.config import settings
from app.security import ADMIN_ROLES

logger = logging.getLogger(__name__)

VERIFICATION_EXPIRES_HOURS = 24
PASSWORD_RESET_EXPIRES_HOURS = 1

# ----------------------------------------------------------------------------
# Design tokens — the dark theme of the app (`.dark` in globals.css)
# ----------------------------------------------------------------------------
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "'SFMono-Regular', Menlo, Consolas, 'Liberation Mono', 'Courier New', monospace"

BG = "#0a0b0d"  # page
CARD = "#15171c"  # soft cards, code box
LINE = "#262a32"  # 1px borders
TEXT = "#f7f8fb"  # --foreground
TEXT_2 = "#c9ced7"  # --text-2: body copy
MUTED = "#9aa1ae"  # --muted-foreground, lifted to stay above 4.5:1 on every surface
EYEBROW = "#dce0e8"  # --eyebrow
LINK = "#8bc2ff"  # --link
BUTTON_BG = "#ffffff"  # .cq-button--primary on dark
BUTTON_TEXT = "#11151c"

# The four stripes of the app icon.
BRAND_STRIPES = ("#0062f8", "#04d191", "#ffba0e", "#fe3c2e")

# Hero backgrounds. The app paints a radial colour bloom in a corner of the hero
# (`.cq-hero`, `--blue`, `--green`, `--red`); mail clients do not render blurs or
# radial gradients reliably, so each tone is the flat colour that bloom averages to.
HERO_TONES = {
    "warm": "#1c1517",  # default hero: orange + red bloom
    "blue": "#121a26",
    "green": "#11201a",
    "red": "#221419",
}

# Secondary cards: accent colour of the round icon and its glyph.
NOTICE_KINDS = {
    "info": ("#0a84ff", "i"),
    "warning": ("#ff9f0a", "!"),
    "success": ("#30d158", "&#10003;"),
    "danger": ("#ff375f", "!"),
}

FOOTER_TAGLINE = "Adventist.Club · Ecosistema Digital del Ministerio Joven Adventista"
MEMBER_PRODUCT = "conquistadores.app"
STAFF_PRODUCT = "admin.adventist.club"

# Only for clients that read <style>. Everything that matters is inline.
_HEAD_STYLE = f"""
:root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
body {{ margin: 0 !important; padding: 0 !important; width: 100% !important; }}
a[x-apple-data-detectors] {{ color: inherit !important; text-decoration: none !important; }}
@media (prefers-color-scheme: dark) {{
  .ac-page {{ background-color: {BG} !important; }}
  .ac-btn {{ background-color: {BUTTON_BG} !important; }}
  .ac-btn-link {{ color: {BUTTON_TEXT} !important; }}
  .ac-text {{ color: {TEXT} !important; }}
}}
[data-ogsb] .ac-btn {{ background-color: {BUTTON_BG} !important; }}
[data-ogsc] .ac-btn-link {{ color: {BUTTON_TEXT} !important; }}
[data-ogsc] .ac-text {{ color: {TEXT} !important; }}
"""

ROLE_WELCOME_MESSAGES = {
    "STUDENT": (
        "Ya puedes unirte a tu club, inscribirte en especialidades y llevar tu avance "
        "en tu portafolio."
    ),
    "PARENT_GUARDIAN": (
        "Ya puedes autorizar a los menores a tu cargo y seguir su avance en el club."
    ),
    "COUNSELOR": "Ya puedes acompañar el avance de los miembros de tu unidad.",
    "INSTRUCTOR": (
        "Ya puedes crear cursos a partir de las especialidades oficiales. Para dictaminar "
        "requisitos necesitas el curso de protección infantil y la carta de tu iglesia vigente."
    ),
    "CLUB_SECRETARY": "Ya puedes ayudar a la dirección con los registros de tu club.",
    "CLUB_DIRECTOR": "Ya puedes comenzar a gestionar tu club y registrar miembros.",
    "COORDINATOR_ZONE": "Ya puedes supervisar los clubes de tu zona.",
    "ADMIN_ASSOCIATION": "Ya puedes administrar tu asociación.",
    "ADMIN_UNION": "Ya puedes administrar tu unión y acompañar a sus asociaciones.",
    "ADMIN_DIVISION": "Ya puedes administrar tu división y acompañar a sus uniones.",
    "MASTER_GC": (
        "Tienes acceso completo a la administración de la plataforma. Configura la "
        "verificación en dos pasos: tu rol la exige."
    ),
}

ROLE_NAMES = {
    "MASTER_GC": "Administración general",
    "ADMIN_DIVISION": "Administración de división",
    "ADMIN_UNION": "Administración de unión",
    "ADMIN_ASSOCIATION": "Administración de asociación",
    "COORDINATOR_ZONE": "Coordinación de zona",
    "CLUB_DIRECTOR": "Director(a) de club",
    "CLUB_SECRETARY": "Secretario(a) de club",
    "INSTRUCTOR": "Instructor(a)",
    "COUNSELOR": "Consejero(a) de unidad",
    "STUDENT": "Miembro del club",
    "PARENT_GUARDIAN": "Madre, padre o tutor(a)",
}

ROLE_WELCOME_STEPS = {
    "INSTRUCTOR": (
        "Presenta la carta de tu iglesia desde tu panel",
        "Completa el curso de protección infantil",
        "Crea tu primer curso a partir de una especialidad oficial",
    ),
    "CLUB_DIRECTOR": (
        "Completa los datos de tu club",
        "Invita a tu equipo y a los miembros",
        "Revisa las solicitudes de ingreso",
    ),
    "PARENT_GUARDIAN": (
        "Completa tu perfil",
        "Revisa las autorizaciones pendientes",
        "Sigue el avance de los menores a tu cargo",
    ),
}
DEFAULT_MEMBER_STEPS = (
    "Completa tu perfil",
    "Explora las especialidades disponibles",
    "Únete a tu club",
)
DEFAULT_STAFF_STEPS = (
    "Revisa tu perfil y tu alcance",
    "Atiende las colas pendientes de tu región",
)


# ----------------------------------------------------------------------------
# URLs
# ----------------------------------------------------------------------------
def _admin_url() -> str:
    return settings.admin_url


def _safe_url(url: str | None, fallback: str | None = None) -> str:
    """Only http(s) links reach an e-mail; anything else falls back to the app."""
    candidate = (url or "").strip()
    if urlsplit(candidate).scheme.lower() not in ("http", "https"):
        candidate = fallback or settings.frontend_url
    return candidate


# ----------------------------------------------------------------------------
# Building blocks. They take HTML: callers escape what comes from a person.
# ----------------------------------------------------------------------------
def _brand_bar() -> str:
    cells = "".join(
        f'<td width="22" height="4" bgcolor="{color}" '
        f'style="width:22px;height:4px;background-color:{color};border-radius:2px;'
        f'font-size:0;line-height:0;">&nbsp;</td>'
        f'<td width="4" style="width:4px;font-size:0;line-height:0;">&nbsp;</td>'
        for color in BRAND_STRIPES
    )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:separate;margin:0 0 20px 0;"><tr>{cells}</tr></table>'
    )


def _hero(eyebrow: str, heading: str, lead: str = "", tone: str = "warm") -> str:
    bg = HERO_TONES.get(tone, HERO_TONES["warm"])
    lead_html = (
        f'<p style="margin:14px 0 0 0;font-family:{FONT};font-size:16px;line-height:1.5;'
        f'color:{TEXT_2};">{lead}</p>'
        if lead
        else ""
    )
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="border-collapse:separate;">
  <tr>
    <td bgcolor="{bg}" style="background-color:{bg};border:1px solid {LINE};border-radius:36px;padding:32px 30px 34px 30px;">
      {_brand_bar()}
      <p style="margin:0;font-family:{FONT};font-size:12px;line-height:1;letter-spacing:0.16em;text-transform:uppercase;font-weight:800;color:{EYEBROW};">{eyebrow}</p>
      <h1 class="ac-text" style="margin:12px 0 0 0;font-family:{FONT};font-size:30px;line-height:1.1;letter-spacing:-0.02em;font-weight:700;color:{TEXT};">{heading}</h1>
      {lead_html}
    </td>
  </tr>
</table>"""


def _p(html: str) -> str:
    return (
        f'<p style="margin:0 0 16px 0;font-family:{FONT};font-size:16px;line-height:1.5;'
        f'color:{TEXT_2};">{html}</p>'
    )


def _strong(html: str) -> str:
    return f'<strong style="color:{TEXT};font-weight:700;">{html}</strong>'


def _button(label: str, url: str) -> str:
    """Full-width white pill (`.cq-button--primary` on dark) + the plain URL below."""
    href = escape(url, quote=True)
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 0 0;">
  <tr>
    <td align="center">
      <!--[if mso]>
      <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word"
        href="{href}" style="height:52px;v-text-anchor:middle;width:560px;" arcsize="50%" stroke="f" fillcolor="{BUTTON_BG}">
        <w:anchorlock/>
        <center style="color:{BUTTON_TEXT};font-family:Arial,sans-serif;font-size:16px;font-weight:bold;">{label}</center>
      </v:roundrect>
      <![endif]-->
      <!--[if !mso]><!-->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;">
        <tr>
          <td class="ac-btn" align="center" bgcolor="{BUTTON_BG}" style="background-color:{BUTTON_BG};border-radius:999px;">
            <a class="ac-btn-link" href="{href}" target="_blank"
               style="display:block;padding:16px 24px;font-family:{FONT};font-size:16px;line-height:20px;font-weight:800;color:{BUTTON_TEXT};text-decoration:none;border-radius:999px;">{label}</a>
          </td>
        </tr>
      </table>
      <!--<![endif]-->
    </td>
  </tr>
  <tr>
    <td style="padding:14px 8px 0 8px;font-family:{FONT};font-size:14px;line-height:1.5;color:{MUTED};text-align:center;">
      ¿El botón no funciona? Copia y pega este enlace en tu navegador:<br>
      <a href="{href}" target="_blank" style="color:{LINK};text-decoration:underline;word-break:break-all;">{escape(url)}</a>
    </td>
  </tr>
</table>"""


def _code(code: str, caption: str) -> str:
    """Large, tracked, monospace digits in a rounded high-contrast box."""
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;margin:0 0 20px 0;">
  <tr>
    <td align="center" bgcolor="{CARD}" style="background-color:{CARD};border:1px solid {LINE};border-radius:24px;padding:22px 16px 20px 16px;">
      <p style="margin:0 0 12px 0;font-family:{FONT};font-size:12px;line-height:1;letter-spacing:0.16em;text-transform:uppercase;font-weight:800;color:{EYEBROW};">Tu código</p>
      <p class="ac-text" style="margin:0;font-family:{MONO};font-size:36px;line-height:1.2;letter-spacing:10px;font-weight:700;color:{TEXT};">{escape(code)}</p>
      <p style="margin:12px 0 0 0;font-family:{FONT};font-size:14px;line-height:1.5;color:{MUTED};">{caption}</p>
    </td>
  </tr>
</table>"""


def _notice(kind: str, title: str, body: str) -> str:
    """Soft secondary card with a round icon, like the login screen's register card."""
    accent, glyph = NOTICE_KINDS.get(kind, NOTICE_KINDS["info"])
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;margin:0 0 16px 0;">
  <tr>
    <td bgcolor="{CARD}" style="background-color:{CARD};border:1px solid {LINE};border-radius:24px;padding:16px 18px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td width="32" valign="top" style="width:32px;padding:2px 14px 0 0;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;">
              <tr>
                <td width="32" height="32" align="center" valign="middle" bgcolor="{accent}" aria-hidden="true"
                    style="width:32px;height:32px;border-radius:16px;background-color:{accent};font-family:{FONT};font-size:16px;line-height:32px;font-weight:800;color:{BUTTON_TEXT};">{glyph}</td>
              </tr>
            </table>
          </td>
          <td valign="top" style="font-family:{FONT};font-size:16px;line-height:1.5;color:{TEXT_2};">
            <strong class="ac-text" style="display:block;color:{TEXT};font-weight:700;">{title}</strong>
            {body}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def _list(items) -> str:
    rows = "".join(
        f'<tr><td width="18" valign="top" style="width:18px;font-family:{FONT};font-size:16px;'
        f'line-height:1.5;color:{MUTED};">&bull;</td>'
        f'<td style="font-family:{FONT};font-size:16px;line-height:1.5;color:{TEXT_2};'
        f'padding:0 0 6px 0;">{item}</td></tr>'
        for item in items
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 20px 0;">{rows}</table>'
    )


def _eyebrow_label(text: str) -> str:
    return (
        f'<p style="margin:8px 0 12px 0;font-family:{FONT};font-size:12px;line-height:1;'
        f'letter-spacing:0.16em;text-transform:uppercase;font-weight:800;color:{EYEBROW};">{text}</p>'
    )


def base_template(
    content: str,
    title: str,
    *,
    preheader: str = "",
    reason: str = "",
    footnote: str = "",
    audience: str = "member",
) -> str:
    """The page around every message: logo, `content`, footer.

    `content` is HTML (normally `_hero(...)` + a body). `preheader`, `reason` and
    `footnote` are HTML too: whoever builds them escapes what comes from a person.
    `audience` picks the product the footer links to: conquistadores.app for members,
    admin.adventist.club for staff.
    """
    logo = f"{settings.R2_PUBLIC_URL.rstrip('/')}/adventist-club-fav.png"
    if audience == "staff":
        product_url, product_name = _admin_url(), STAFF_PRODUCT
    else:
        product_url, product_name = settings.frontend_url, MEMBER_PRODUCT
    spacer = "&#8199;&#65279;&#847; " * 40
    preheader_html = (
        f'<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;'
        f'opacity:0;overflow:hidden;mso-hide:all;color:{BG};">{preheader}{spacer}</div>'
        if preheader
        else ""
    )
    reason_html = (
        f'<p style="margin:0 0 10px 0;font-family:{FONT};font-size:13px;line-height:1.5;'
        f'color:{MUTED};">{reason}</p>'
        if reason
        else ""
    )
    footnote_html = (
        f'<p style="margin:0 0 10px 0;font-family:{FONT};font-size:13px;line-height:1.5;'
        f'color:{MUTED};">{footnote}</p>'
        if footnote
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no, date=no, address=no, email=no">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{escape(title)}</title>
<!--[if mso]>
<noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>
<![endif]-->
<style>{_HEAD_STYLE}</style>
</head>
<body class="ac-page" style="margin:0;padding:0;background-color:{BG};">
{preheader_html}
<table role="presentation" class="ac-page" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{BG}" style="background-color:{BG};">
  <tr>
    <td align="center" style="padding:28px 12px 40px 12px;">
      <!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;">
        <tr>
          <td align="center" style="padding:0 0 18px 0;">
            <a href="{escape(product_url, quote=True)}" target="_blank" style="text-decoration:none;">
              <img src="{escape(logo, quote=True)}" width="48" height="48" alt="Adventist.Club"
                   style="display:block;width:48px;height:48px;border:0;outline:none;text-decoration:none;font-family:{FONT};font-size:14px;color:{TEXT};">
            </a>
          </td>
        </tr>
        <tr>
          <td style="padding:0 8px;">
{content}
          </td>
        </tr>
        <tr>
          <td align="center" style="padding:36px 20px 0 20px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr><td height="1" bgcolor="{LINE}" style="height:1px;line-height:1px;font-size:0;background-color:{LINE};">&nbsp;</td></tr>
            </table>
            <p style="margin:24px 0 10px 0;font-family:{FONT};font-size:13px;line-height:1.5;font-weight:700;color:{TEXT_2};">{FOOTER_TAGLINE}</p>
            {reason_html}
            {footnote_html}
            <p style="margin:0;font-family:{FONT};font-size:13px;line-height:1.5;color:{MUTED};">
              Correo automático: por favor no respondas a este mensaje.<br>
              <a href="{escape(product_url, quote=True)}" target="_blank" style="color:{LINK};text-decoration:underline;">{product_name}</a>
            </p>
          </td>
        </tr>
      </table>
      <!--[if mso]></td></tr></table><![endif]-->
    </td>
  </tr>
</table>
</body>
</html>
"""


def _render(
    *,
    title: str,
    preheader: str,
    eyebrow: str,
    heading: str,
    body: str,
    lead: str = "",
    tone: str = "warm",
    reason: str = "",
    footnote: str = "",
    audience: str = "member",
) -> str:
    content = (
        _hero(eyebrow, heading, lead, tone)
        + '\n<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        + f'<tr><td style="padding:28px 4px 0 4px;">{body}</td></tr></table>'
    )
    return base_template(
        content,
        title,
        preheader=preheader,
        reason=reason,
        footnote=footnote,
        audience=audience,
    )


def _hello(name: str) -> str:
    return _p(f"Hola {_strong(escape(name))},")


# ----------------------------------------------------------------------------
# Account
# ----------------------------------------------------------------------------
def verification_email_html(name: str, code: str, link: str) -> str:
    body = (
        _hello(name)
        + _p(
            "Gracias por registrarte en el ecosistema digital del Ministerio Joven Adventista. "
            "Para activar tu cuenta, ingresa este código en la aplicación:"
        )
        + _code(code, f"Vence en {VERIFICATION_EXPIRES_HOURS} horas.")
        + _p("O verifica tu correo con un solo toque:")
        + _button("Verificar mi cuenta", _safe_url(link))
        + '<div style="height:24px;line-height:24px;font-size:0;">&nbsp;</div>'
        + _notice(
            "warning",
            "¿No fuiste tú?",
            "Si no creaste esta cuenta, ignora este correo: sin verificar, la cuenta no se activa.",
        )
    )
    return _render(
        title="Verifica tu cuenta - Adventist.Club",
        preheader=f"Tu código para activar tu cuenta. Vence en {VERIFICATION_EXPIRES_HOURS} horas.",
        eyebrow="Tu cuenta",
        heading="Verifica tu correo",
        lead="Un paso más y podrás usar todas las funciones de Adventist.Club.",
        tone="blue",
        body=body,
        reason=(
            "Recibes este correo porque alguien creó una cuenta en "
            f"{MEMBER_PRODUCT} con esta dirección."
        ),
    )


def password_reset_email_html(name: str, code: str, link: str) -> str:
    hours = PASSWORD_RESET_EXPIRES_HOURS
    expiry = f"{hours} hora" if hours == 1 else f"{hours} horas"
    body = (
        _hello(name)
        + _p(
            "Recibimos una solicitud para restablecer la contraseña de tu cuenta. "
            "Ingresa este código en la aplicación:"
        )
        + _code(code, f"Vence en {expiry}.")
        + _p("O crea una contraseña nueva desde este botón:")
        + _button("Restablecer contraseña", _safe_url(link))
        + '<div style="height:24px;line-height:24px;font-size:0;">&nbsp;</div>'
        + _notice(
            "warning",
            "Cuida tu cuenta",
            "Nunca compartas este código con nadie. Si no pediste este cambio, ignora este "
            "correo: tu contraseña seguirá igual.",
        )
        + _p("Si tienes problemas, contacta a tu coordinador o administrador.")
    )
    return _render(
        title="Restablecer Contraseña - Adventist.Club",
        preheader=f"Tu código para restablecer la contraseña. Vence en {expiry}.",
        eyebrow="Seguridad",
        heading="Restablece tu contraseña",
        tone="blue",
        body=body,
        reason=(
            "Recibes este correo porque se pidió restablecer la contraseña de la cuenta "
            "asociada a esta dirección."
        ),
    )


def welcome_email_html(name: str, role: str) -> str:
    staff = role in ADMIN_ROLES
    role_message = ROLE_WELCOME_MESSAGES.get(role, "Ya puedes comenzar a usar Adventist.Club.")
    role_name = ROLE_NAMES.get(role, role)
    steps = ROLE_WELCOME_STEPS.get(
        role, DEFAULT_STAFF_STEPS if staff else DEFAULT_MEMBER_STEPS
    )
    if staff:
        cta = _button("Ir a la administración", f"{_admin_url()}/admin")
        where = STAFF_PRODUCT
    else:
        cta = _button("Iniciar sesión", f"{settings.frontend_url}/auth/login")
        where = MEMBER_PRODUCT
    body = (
        _hello(name)
        + _p("Tu cuenta quedó verificada. ¡Bienvenido a la familia Adventist.Club!")
        + _notice("success", f"Tu rol: {escape(role_name)}", escape(role_message))
        + _eyebrow_label("Próximos pasos")
        + _list(escape(step) for step in steps)
        + cta
    )
    return _render(
        title="¡Bienvenido! - Adventist.Club",
        preheader=f"Tu cuenta está lista. Entra en {where} para empezar.",
        eyebrow="Tu cuenta",
        heading="¡Cuenta verificada!",
        lead=f"Todo listo para empezar en {where}.",
        tone="green",
        body=body,
        reason="Recibes este correo porque acabas de verificar tu cuenta.",
        audience="staff" if staff else "member",
    )


def mfa_reset_email_html(name: str, reason: str) -> str:
    body = (
        _hello(name)
        + _p(
            "Otro administrador con rol MASTER restableció la verificación en dos pasos de tu "
            "cuenta. Tu autenticador y tus códigos de recuperación anteriores ya no sirven."
        )
        + _notice("info", "Motivo registrado", escape(reason))
        + _notice(
            "danger",
            "Si no pediste esto",
            "Avisa de inmediato al equipo: alguien con acceso MASTER actuó sobre tu cuenta. "
            "El cambio queda registrado en la auditoría.",
        )
        + _p("Vuelve a activar la verificación en dos pasos en cuanto inicies sesión.")
        + _button("Ir a la administración", f"{_admin_url()}/admin")
    )
    return _render(
        title="Verificación en dos pasos restablecida - Adventist.Club",
        preheader="Tu autenticador anterior ya no sirve. Vuelve a activarlo al iniciar sesión.",
        eyebrow="Seguridad",
        heading="Se restableció tu verificación en dos pasos",
        tone="red",
        body=body,
        reason="Es un aviso de seguridad de tu cuenta: no se puede desactivar.",
        audience="staff",
    )


def recovery_code_used_email_html(name: str) -> str:
    body = (
        _hello(name)
        + _p(
            "Alguien inició sesión en tu cuenta con un código de recuperación en lugar del "
            "código de tu aplicación de autenticación. Ese código ya quedó marcado como usado."
        )
        + _notice(
            "danger",
            "Si no fuiste tú",
            "Cambia tu contraseña ahora mismo y pide que se restablezca tu verificación en "
            "dos pasos.",
        )
        + _p(
            "Si perdiste tu autenticador, vuelve a configurarlo y genera códigos nuevos desde "
            "tu perfil."
        )
        + _button("Revisar mi seguridad", f"{settings.frontend_url}/profile")
    )
    return _render(
        title="Código de recuperación usado - Adventist.Club",
        preheader="Se inició sesión con uno de tus códigos de recuperación.",
        eyebrow="Seguridad",
        heading="Se usó uno de tus códigos de recuperación",
        tone="red",
        body=body,
        reason="Es un aviso de seguridad de tu cuenta: no se puede desactivar.",
    )


# ----------------------------------------------------------------------------
# Clubs and memberships
# ----------------------------------------------------------------------------
def club_decision_email_html(name: str, club_name: str, approved: bool, reason: str | None) -> str:
    club = _strong(escape(club_name))
    if approved:
        heading = "¡Tu club fue aprobado!"
        tone = "green"
        preheader = f"{escape(club_name)} ya está activo en {MEMBER_PRODUCT}."
        body = (
            _hello(name)
            + _p(f"La coordinación de tu asociación aprobó el registro de {club}.")
            + _notice(
                "success",
                "Ya puedes empezar",
                "Gestiona tu club y emite certificados desde tu panel.",
            )
        )
    else:
        heading = "Tu solicitud de club no fue aprobada"
        tone = "warm"
        preheader = f"La coordinación revisó el registro de {escape(club_name)}."
        body = (
            _hello(name)
            + _p(
                f"La coordinación de tu asociación revisó el registro de {club} y por ahora "
                "no fue aprobado."
            )
            + (_notice("warning", "Motivo", escape(reason)) if reason else "")
            + _p(
                "Puedes corregir los datos y enviar una nueva solicitud desde tu panel, o "
                "contactar a tu coordinador."
            )
        )
    body += _button("Ir a mi panel", f"{settings.frontend_url}/panel")
    return _render(
        title="Registro de club - Adventist.Club",
        preheader=preheader,
        eyebrow="Tu club",
        heading=heading,
        tone=tone,
        body=body,
        reason=f"Recibes este correo porque registraste un club en {MEMBER_PRODUCT}.",
    )


ROLE_LABELS = {
    "STUDENT": "miembro",
    "COUNSELOR": "consejero(a) de unidad",
    "INSTRUCTOR": "instructor(a)",
    "CLUB_SECRETARY": "secretario(a) del club",
    "CLUB_DIRECTOR": "director(a)",
}


def club_invitation_email_html(club_name: str, role: str, link: str, inviter_name: str) -> str:
    role_label = ROLE_LABELS.get(role, "miembro")
    inviter = escape(inviter_name)
    club = escape(club_name)
    body = (
        _p(
            f"{_strong(inviter)} te invita a unirte a {_strong(club)} en Adventist.Club como "
            f"{escape(role_label)}."
        )
        + _button("Unirme al club", _safe_url(link))
        + '<div style="height:24px;line-height:24px;font-size:0;">&nbsp;</div>'
        + _notice(
            "warning",
            "Este enlace es personal",
            "No lo compartas. Si no esperabas esta invitación, puedes ignorar este correo.",
        )
    )
    return _render(
        title="Invitación a un club - Adventist.Club",
        preheader=f"{inviter} te invita a {club} como {escape(role_label)}.",
        eyebrow="Invitación",
        heading="Te invitaron a un club",
        lead=f"{club} te espera en {MEMBER_PRODUCT}.",
        tone="blue",
        body=body,
        reason=f"Recibes este correo porque {inviter} te invitó a su club.",
    )


def consent_request_email_html(child_name: str, club_name: str, link: str) -> str:
    """Goes to an adult about a minor in their care, so it does name the minor.
    Nothing else in block E sends a minor's name to a third party."""
    child = escape(child_name)
    club = escape(club_name)
    body = (
        _p(
            f"{_strong(child)} pidió unirse a {_strong(club)} en Adventist.Club y necesita la "
            "autorización de su madre, padre o tutor."
        )
        + _notice(
            "info",
            "Qué verá el club",
            "En la página verás qué datos vería el club (nombre, edad, avance y evidencias) y "
            "quiénes los verían. Sin tu autorización, el club no tiene acceso a nada.",
        )
        + _button("Revisar y autorizar", _safe_url(link))
        + '<div style="height:24px;line-height:24px;font-size:0;">&nbsp;</div>'
        + _notice(
            "warning",
            "Importante",
            "El enlace vence en 14 días y sirve una sola vez. Puedes retirar la autorización "
            "cuando quieras desde tu panel. Si no reconoces esta solicitud, ignora este correo.",
        )
    )
    return _render(
        title="Autorización para unirse a un club - Adventist.Club",
        preheader=f"{child} necesita tu autorización para unirse a {club}.",
        eyebrow="Autorización",
        heading="Autorización para unirse a un club",
        tone="blue",
        body=body,
        reason=(
            "Recibes este correo porque esta dirección figura como madre, padre o tutor(a) "
            f"de {child}."
        ),
    )


def pending_requests_email_html(director_name: str, club_name: str, pending: int, link: str) -> str:
    """To the club's staff. It carries a COUNT and a link, never a name: some
    of the people in that queue are minors (spec §5.10)."""
    count = int(pending)
    what = "una solicitud" if count == 1 else f"{count} solicitudes"
    club = escape(club_name)
    body = (
        _hello(director_name)
        + _p(f"{_strong(club)} tiene {what} de ingreso esperando tu decisión.")
        + _button("Revisar solicitudes", _safe_url(link))
    )
    return _render(
        title="Solicitudes por revisar - Adventist.Club",
        preheader=f"{club} tiene {what} de ingreso por revisar.",
        eyebrow="Tu club",
        heading="Tienes solicitudes por revisar",
        tone="warm",
        body=body,
        reason=f"Recibes este correo porque diriges {club} en {MEMBER_PRODUCT}.",
    )


def pending_reviews_email_html(reviewer_name: str, club_name: str, pending: int, link: str) -> str:
    """«Avisos»: to the club's reviewers, at most once per club every 12 hours. Like the join
    requests it carries a COUNT and a link, never a name: some of the members whose work
    is waiting are minors."""
    count = int(pending)
    what = "un requisito enviado" if count == 1 else f"{count} requisitos enviados"
    club = escape(club_name)
    body = (
        _hello(reviewer_name)
        + _p(f"En {_strong(club)} hay {what} por miembros del club esperando revisión.")
        + _p("Revisa las evidencias y deja tu dictamen: cada requisito aprobado acerca al miembro a su certificado.")
        + _button("Abrir la cola de revisión", _safe_url(link, _admin_url()))
    )
    return _render(
        title="Requisitos por revisar - Adventist.Club",
        preheader=f"{club} tiene {what} esperando revisión.",
        eyebrow="Revisión",
        heading="Hay requisitos por revisar",
        tone="blue",
        body=body,
        reason=(
            f"Recibes este correo porque diriges o instruyes en {club}. Como mucho te llega "
            "uno cada 12 horas; el detalle está siempre en la cola de revisión."
        ),
        audience="staff",
    )


def course_pending_reviews_email_html(instructor_name: str, course_title: str, pending: int, link: str) -> str:
    """«Avisos», modo COURSE: to the instructor of a course, at most once per course every
    12 hours. A count and a link, never a name (some students may be minors)."""
    count = int(pending)
    what = "un requisito enviado" if count == 1 else f"{count} requisitos enviados"
    course = escape(course_title)
    body = (
        _hello(instructor_name)
        + _p(f"En tu curso {_strong(course)} hay {what} esperando tu revisión.")
        + _p("Revisa las evidencias y deja tu dictamen: cada requisito aprobado acerca al estudiante a su certificado.")
        + _button("Abrir el curso", _safe_url(link))
    )
    return _render(
        title="Requisitos por revisar - Adventist.Club",
        preheader=f"{course} tiene {what} esperando revisión.",
        eyebrow="Revisión",
        heading="Hay requisitos por revisar",
        tone="blue",
        body=body,
        reason=(
            f"Recibes este correo porque impartes {course}. Como mucho te llega uno cada "
            "12 horas por curso; el detalle está siempre en el curso."
        ),
        audience="staff",
    )


def membership_decision_email_html(
    name: str, club_name: str, approved: bool, reason: str | None
) -> str:
    club = escape(club_name)
    if approved:
        heading = "¡Ya eres parte del club!"
        tone = "green"
        preheader = f"{club} aceptó tu ingreso."
        body = _hello(name) + _p(f"{_strong(club)} aceptó tu ingreso.")
    else:
        heading = "Novedades sobre tu membresía"
        tone = "warm"
        preheader = f"Hay novedades sobre tu membresía en {club}."
        body = (
            _hello(name)
            + _p(f"Tu membresía en {_strong(club)} no sigue adelante por ahora.")
            + (_notice("warning", "Motivo", escape(reason)) if reason else "")
            + _p("Puedes buscar otro club cercano o hablar con la dirección del club.")
        )
    body += _button("Ir a mi panel", f"{settings.frontend_url}/panel")
    return _render(
        title="Tu membresía de club - Adventist.Club",
        preheader=preheader,
        eyebrow="Tu club",
        heading=heading,
        tone=tone,
        body=body,
        reason=f"Recibes este correo porque tienes una membresía o una solicitud en {club}.",
    )


# ----------------------------------------------------------------------------
# Church letters of leaders
# ----------------------------------------------------------------------------
def letter_submitted_email_html(
    reviewer_name: str, applicant_name: str, role: str, link: str
) -> str:
    """To whoever validates letters. It names an ADULT who presented their own
    document, and nothing about any minor."""
    role_label = ROLE_LABELS.get(role, "instructor(a)")
    applicant = escape(applicant_name)
    body = (
        _hello(reviewer_name)
        + _p(
            f"{_strong(applicant)} presentó la carta de su iglesia para el cargo de "
            f"{escape(role_label)}."
        )
        + _notice(
            "info",
            "Por qué importa",
            "Sin una carta válida, esa persona no puede dictaminar ni ver datos de menores.",
        )
        + _button("Revisar la carta", _safe_url(link, f"{_admin_url()}/admin/cartas"))
    )
    return _render(
        title="Carta por validar - Adventist.Club",
        preheader=f"{applicant} presentó su carta de iglesia.",
        eyebrow="Cartas de iglesia",
        heading="Una carta de iglesia espera tu validación",
        tone="blue",
        body=body,
        reason=(
            "Recibes este correo porque validas cartas de iglesia en "
            f"{STAFF_PRODUCT}."
        ),
        audience="staff",
    )


def letter_decision_email_html(
    name: str, status: str, valid_until: str | None, note: str | None
) -> str:
    note_html = _notice("warning", "Motivo", escape(note)) if note else ""
    if status == "AUTHORIZED":
        until = f" y vale hasta el {_strong(escape(valid_until))}" if valid_until else ""
        heading = "Tu carta fue validada"
        tone = "green"
        preheader = "Tu carta de la iglesia quedó autorizada."
        body = (
            _hello(name)
            + _p(f"Tu carta de la iglesia quedó autorizada{until}.")
            + _notice(
                "info",
                "Renovación",
                "Podrás renovarla desde 60 días antes de esa fecha. Al vencer, vuelves a "
                "«sin verificar» hasta presentar una nueva.",
            )
        )
    elif status == "REVOKED":
        heading = "Se retiró tu validación"
        tone = "red"
        preheader = "La administración revocó la validación de tu carta."
        body = (
            _hello(name)
            + _p("La administración revocó la validación de tu carta de la iglesia.")
            + note_html
        )
    else:
        heading = "Tu carta no fue validada"
        tone = "warm"
        preheader = "La administración revisó tu carta de la iglesia."
        body = (
            _hello(name)
            + _p(
                "La administración revisó la carta de tu iglesia y por ahora no la aceptó. "
                "Puedes presentar otra."
            )
            + note_html
        )
    body += _button("Ir a mi panel", f"{settings.frontend_url}/panel")
    return _render(
        title="Tu carta de la iglesia - Adventist.Club",
        preheader=preheader,
        eyebrow="Tu carta de la iglesia",
        heading=heading,
        tone=tone,
        body=body,
        reason="Recibes este correo porque presentaste la carta de tu iglesia en Adventist.Club.",
    )


def letter_expiring_email_html(name: str, valid_until: str, days: int) -> str:
    until = escape(valid_until)
    body = (
        _hello(name)
        + _p(
            f"Tu validación vence el {_strong(until)}, dentro de {int(days)} días. Ya puedes "
            "presentar la carta del nuevo periodo."
        )
        + _notice(
            "warning",
            "Al vencer",
            "Vuelves a «sin verificar»: no podrás dictaminar ni ver datos de menores hasta "
            "renovarla.",
        )
        + _button("Renovar mi carta", f"{settings.frontend_url}/panel")
    )
    return _render(
        title="Tu carta de la iglesia vence pronto - Adventist.Club",
        preheader=f"Tu validación vence el {until}. Renueva tu carta a tiempo.",
        eyebrow="Tu carta de la iglesia",
        heading="Tu carta de la iglesia está por vencer",
        tone="warm",
        body=body,
        reason="Recibes este correo porque presentaste la carta de tu iglesia en Adventist.Club.",
    )


# ----------------------------------------------------------------------------
# Portfolio
# ----------------------------------------------------------------------------
PROGRESS_SUBJECTS = {
    "PROGRESS_INCOMPLETE": "Tienes una observación en tu especialidad",
    "PROGRESS_READY": "¡Tu especialidad está lista para certificar!",
    "PROGRESS_CERTIFIED": "¡Especialidad certificada! 🎉",
}

PROGRESS_FOOTNOTE = (
    "Puedes desactivar estos avisos de avance desde tu perfil. Los de seguridad, "
    "invitación y autorización no se pueden apagar."
)


PROGRESS_SUBJECTS_PROGRAM = {
    "PROGRESS_INCOMPLETE": "Tienes una observación en tu clase",
    "PROGRESS_READY": "¡Tu clase está lista para la investidura!",
    "PROGRESS_CERTIFIED": "¡Investidura! 🎉",
}


def progress_email_html(
    name: str,
    kind: str,
    honor_name: str,
    note: str | None,
    link: str,
    award_type: str = "honor",
) -> str:
    """Portfolio progress (E9). It carries the honor, an observation the member
    already wrote or read, and a link. Never an image, never an evidence, never
    anything about another member.

    `award_type="program"` (Bloque F: a class or a program) only changes the words:
    a class is invested, not certified."""
    honor = escape(honor_name)
    if award_type == "program" and kind == "PROGRESS_READY":
        heading = "¡Terminaste todos los requisitos!"
        tone = "blue"
        preheader = f"{honor} está lista para la investidura."
        body = _hello(name) + _p(
            f"{_strong(honor)} está lista para que la dirección de tu club te invista."
        )
    elif award_type == "program" and kind == "PROGRESS_CERTIFIED":
        heading = "¡Investidura!"
        tone = "green"
        preheader = f"Recibiste la investidura de {honor}."
        body = _hello(name) + _p(
            f"Recibiste la investidura de {_strong(honor)}. Ya puedes descargar tu certificado."
        )
    elif kind == "PROGRESS_INCOMPLETE":
        heading = "Tienes una observación por revisar"
        tone = "warm"
        preheader = f"Hay un requisito por corregir en {honor}."
        body = (
            _hello(name)
            + _p(f"Quien dictamina {_strong(honor)} pidió que corrijas un requisito.")
            + (_notice("warning", "Observación", escape(note)) if note else "")
        )
    elif kind == "PROGRESS_READY":
        heading = "¡Terminaste todos los requisitos!"
        tone = "blue"
        preheader = f"{honor} está lista para certificar."
        body = _hello(name) + _p(
            f"{_strong(honor)} está lista para que la dirección de tu club la certifique."
        )
    else:
        heading = "¡Especialidad certificada!"
        tone = "green"
        preheader = f"Ya puedes descargar el certificado de {honor}."
        body = _hello(name) + _p(f"Ya puedes descargar el certificado de {_strong(honor)}.")
    body += _button("Ver mi portafolio", _safe_url(link))
    return _render(
        title="Avance de tu portafolio - Adventist.Club",
        preheader=preheader,
        eyebrow="Tu portafolio",
        heading=heading,
        tone=tone,
        body=body,
        reason=f"Recibes este correo porque {honor} está en tu portafolio de {MEMBER_PRODUCT}.",
        footnote=PROGRESS_FOOTNOTE,
    )


# ----------------------------------------------------------------------------
# «Avisos» — hours and points (Bloque F · F2, Bloque G §4.1)
# ----------------------------------------------------------------------------
def _number(value) -> str:
    return f"{float(value):g}".replace(".", ",")


def hours_approved_email_html(name: str, service: float, attendance: float, link: str) -> str:
    """Hours approved by the club. Only the amount: what the member did and where says
    where a minor was, and it stays in the portfolio behind a session."""
    parts = []
    if float(service or 0):
        parts.append(f"{_strong(_number(service))} h de servicio")
    if float(attendance or 0):
        meetings = int(float(attendance))
        parts.append(_strong("1 asistencia" if meetings == 1 else f"{meetings} asistencias"))
    what = " y ".join(parts) or "tus horas"
    body = (
        _hello(name)
        + _p(f"La dirección de tu club aprobó {what}.")
        + _p("Las horas aprobadas cuentan para los requisitos de horas de tu clase y suman XP.")
        + _button("Ver mis horas", _safe_url(link))
    )
    return _render(
        title="Horas aprobadas - Adventist.Club",
        preheader="La dirección de tu club aprobó tus horas.",
        eyebrow="Tus horas",
        heading="¡Horas aprobadas!",
        tone="green",
        body=body,
        reason=f"Recibes este correo porque registras horas de servicio en {MEMBER_PRODUCT}.",
        footnote=PROGRESS_FOOTNOTE,
    )


def _hours_parts(service: float, attendance: float) -> list[str]:
    parts = []
    if float(service or 0):
        parts.append(f"{_strong(_number(service))} h de servicio")
    if float(attendance or 0):
        meetings = int(float(attendance))
        parts.append(_strong("1 asistencia" if meetings == 1 else f"{meetings} asistencias"))
    return parts


def hours_rejected_email_html(
    name: str, service: float, attendance: float, note: str | None, link: str
) -> str:
    """Hours the club did not approve. The amount and, when the director wrote one, the
    reason: it is addressed to the member, like the observation of a requirement (E9).
    What the member did and where is not copied (it says where a minor was)."""
    what = " y ".join(_hours_parts(service, attendance)) or "unas horas"
    body = (
        _hello(name)
        + _p(f"La dirección de tu club no aprobó {what} que registraste.")
        + (_notice("warning", "Motivo", escape(note)) if note else "")
        + _p("Puedes revisar el registro y, si hace falta, enviarlo de nuevo con lo que falte.")
        + _button("Ver mis horas", _safe_url(link))
    )
    return _render(
        title="Horas no aprobadas - Adventist.Club",
        preheader="La dirección de tu club revisó tus horas.",
        eyebrow="Tus horas",
        heading="Unas horas no se aprobaron",
        tone="warm",
        body=body,
        reason=f"Recibes este correo porque registras horas de servicio en {MEMBER_PRODUCT}.",
        footnote=PROGRESS_FOOTNOTE,
    )


XP_CATEGORY_LABELS = {
    "conducta": "conducta",
    "puntualidad": "puntualidad",
    "uniforme": "uniforme",
    "participacion": "participación",
    "servicio": "servicio",
    "otro": "reconocimiento",
}


def xp_awarded_email_html(name: str, points: int, club_name: str, category: str, link: str) -> str:
    """Positive points only (a negative award is never announced by e-mail). The note of
    the award is not copied: it is a judgement about a person."""
    club = escape(club_name)
    label = escape(XP_CATEGORY_LABELS.get(category, "reconocimiento"))
    amount = int(points)
    body = (
        _hello(name)
        + _p(f"La dirección de {_strong(club)} te otorgó {_strong(f'{amount} XP')} por {label}.")
        + _p("Tus puntos suman para subir de nivel. ¡Sigue así!")
        + _button("Ver mi perfil", _safe_url(link))
    )
    return _render(
        title="Ganaste puntos - Adventist.Club",
        preheader=f"{club} te otorgó {amount} XP.",
        eyebrow="Tus puntos",
        heading=f"¡Ganaste {amount} XP!",
        tone="blue",
        body=body,
        reason=f"Recibes este correo porque eres miembro de {club} en {MEMBER_PRODUCT}.",
        footnote=PROGRESS_FOOTNOTE,
    )


# ----------------------------------------------------------------------------
# A certificate was annulled (Bloque D · I7, spec §5.5)
# ----------------------------------------------------------------------------
def certificate_revoked_email_html(name: str, certificate_no: str, honor_name: str | None) -> str:
    """The holder — and the guardians of a minor — are told that a certificate with their
    name on it is no longer valid.

    What it does NOT carry: the reason, who decided it, the course or the instructor. The
    reason is a judgement about a person and can name a third party; it lives in the
    portfolio, behind a session, for whoever is entitled to read it. An e-mail is not a
    private channel: it sits in an inbox a whole family may share.
    """
    honor = f" de {_strong(escape(honor_name))}" if honor_name else ""
    folio = escape(certificate_no)
    body = (
        _hello(name)
        + _p(
            f"El certificado{honor}, con folio {_strong(folio)}, ha sido anulado por la "
            "Asociación y ya no verifica como válido."
        )
        + _p(
            "Entra en tu portafolio para ver el motivo. Si crees que hay un error, habla con "
            "la dirección de tu club."
        )
        + _button("Ver mi portafolio", f"{settings.frontend_url}/portfolio")
    )
    return _render(
        title="Tu certificado fue anulado - Adventist.Club",
        preheader=f"El certificado con folio {folio} ya no es válido.",
        eyebrow="Certificados",
        heading="Tu certificado fue anulado",
        tone="red",
        body=body,
        reason=(
            "Recibes este aviso porque figuras en este certificado o eres madre, padre o "
            "tutor(a) de quien figura en él."
        ),
    )


# ----------------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------------
def _from_header() -> str:
    sender = settings.EMAIL_FROM
    if "<" in sender:
        return sender
    return f"{settings.EMAIL_FROM_NAME} <{sender}>"


def _send_sync(to: str, subject: str, html: str) -> None:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({"from": _from_header(), "to": [to], "subject": subject, "html": html})


async def send_email(to: str, subject: str, html: str) -> bool:
    """Returns True when the message was handed to Resend. Never raises."""
    if not settings.email_configured:
        logger.info("RESEND_API_KEY not configured; skipping email %r", subject)
        return False
    try:
        await anyio.to_thread.run_sync(_send_sync, to, subject, html)
        return True
    except Exception:
        logger.exception("Failed to send email %r", subject)
        return False


async def send_verification_email(to: str, name: str, code: str, token: str) -> bool:
    link = f"{settings.frontend_url}/verify-email?token={token}"
    html = verification_email_html(name, code, link)
    return await send_email(to, "Verifica tu cuenta - Adventist.Club", html)


async def send_password_reset_email(to: str, name: str, code: str, token: str) -> bool:
    # The link carries only the token; the 6-digit code is never derivable from it.
    link = f"{settings.frontend_url}/reset-password?token={token}"
    html = password_reset_email_html(name, code, link)
    return await send_email(to, "Restablecer Contraseña - Adventist.Club", html)


async def send_welcome_email(to: str, name: str, role: str) -> bool:
    return await send_email(to, "¡Bienvenido a Adventist.Club! 🎉", welcome_email_html(name, role))


async def send_mfa_reset_email(to: str, name: str, reason: str) -> bool:
    """Security notice: it cannot be switched off by the account holder."""
    return await send_email(
        to,
        "Se restableció tu verificación en dos pasos - Adventist.Club",
        mfa_reset_email_html(name, reason),
    )


async def send_recovery_code_used_email(to: str, name: str) -> bool:
    return await send_email(
        to,
        "Se usó un código de recuperación - Adventist.Club",
        recovery_code_used_email_html(name),
    )


async def send_club_invitation_email(
    to: str, club_name: str, role: str, link: str, inviter_name: str
) -> bool:
    return await send_email(
        to,
        f"Te invitaron a {club_name} - Adventist.Club",
        club_invitation_email_html(club_name, role, link, inviter_name),
    )


async def send_consent_request_email(
    to: str, child_name: str, club_name: str, link: str
) -> bool:
    return await send_email(
        to,
        "Autorización para unirse a un club - Adventist.Club",
        consent_request_email_html(child_name, club_name, link),
    )


async def send_pending_requests_email(
    to: str, director_name: str, club_name: str, pending: int, link: str
) -> bool:
    return await send_email(
        to,
        f"Solicitudes por revisar en {club_name} - Adventist.Club",
        pending_requests_email_html(director_name, club_name, pending, link),
    )


async def send_membership_decision_email(
    to: str, name: str, club_name: str, approved: bool, reason: str | None = None
) -> bool:
    return await send_email(
        to,
        "Tu membresía de club - Adventist.Club",
        membership_decision_email_html(name, club_name, approved, reason),
    )


async def send_progress_email(
    to: str,
    name: str,
    kind: str,
    honor_name: str,
    note: str | None,
    link: str,
    award_type: str = "honor",
) -> bool:
    subjects = PROGRESS_SUBJECTS_PROGRAM if award_type == "program" else PROGRESS_SUBJECTS
    subject = subjects.get(kind, "Avance de tu portafolio")
    return await send_email(
        to,
        f"{subject} - Adventist.Club",
        progress_email_html(name, kind, honor_name, note, link, award_type),
    )


async def send_pending_reviews_email(
    to: str, reviewer_name: str, club_name: str, pending: int, link: str
) -> bool:
    return await send_email(
        to,
        f"Requisitos por revisar en {club_name} - Adventist.Club",
        pending_reviews_email_html(reviewer_name, club_name, pending, link),
    )


async def send_hours_approved_email(
    to: str, name: str, service: float, attendance: float, link: str
) -> bool:
    return await send_email(
        to, "¡Horas aprobadas! - Adventist.Club", hours_approved_email_html(name, service, attendance, link)
    )


async def send_hours_rejected_email(
    to: str, name: str, service: float, attendance: float, note: str | None, link: str
) -> bool:
    return await send_email(
        to,
        "Horas no aprobadas - Adventist.Club",
        hours_rejected_email_html(name, service, attendance, note, link),
    )


async def send_course_pending_reviews_email(
    to: str, instructor_name: str, course_title: str, pending: int, link: str
) -> bool:
    return await send_email(
        to,
        f"Requisitos por revisar en {course_title} - Adventist.Club",
        course_pending_reviews_email_html(instructor_name, course_title, pending, link),
    )


async def send_xp_awarded_email(
    to: str, name: str, points: int, club_name: str, category: str, link: str
) -> bool:
    return await send_email(
        to,
        f"¡Ganaste {int(points)} XP! - Adventist.Club",
        xp_awarded_email_html(name, points, club_name, category, link),
    )


async def send_letter_submitted_email(
    to: str, reviewer_name: str, applicant_name: str, role: str, link: str
) -> bool:
    return await send_email(
        to,
        "Carta de iglesia por validar - Adventist.Club",
        letter_submitted_email_html(reviewer_name, applicant_name, role, link),
    )


async def send_letter_decision_email(
    to: str, name: str, status: str, valid_until: str | None = None, note: str | None = None
) -> bool:
    return await send_email(
        to,
        "Tu carta de la iglesia - Adventist.Club",
        letter_decision_email_html(name, status, valid_until, note),
    )


async def send_letter_expiring_email(to: str, name: str, valid_until: str, days: int) -> bool:
    return await send_email(
        to,
        "Tu carta de la iglesia vence pronto - Adventist.Club",
        letter_expiring_email_html(name, valid_until, days),
    )


async def send_club_decision_email(
    to: str, name: str, club_name: str, approved: bool, reason: str | None = None
) -> bool:
    subject = (
        "Tu club fue aprobado - Adventist.Club"
        if approved
        else "Tu solicitud de club no fue aprobada - Adventist.Club"
    )
    try:
        html = club_decision_email_html(name, club_name, approved, reason)
    except Exception:  # never let a template problem surface to the caller
        logger.exception("Failed to render club decision email")
        return False
    return await send_email(to, subject, html)


async def send_certificate_revoked_email(
    to: str, name: str, certificate_no: str, honor_name: str | None = None
) -> bool:
    return await send_email(
        to,
        "Tu certificado fue anulado - Adventist.Club",
        certificate_revoked_email_html(name, certificate_no, honor_name),
    )
