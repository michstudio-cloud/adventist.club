"""Transactional email through Resend. Sending never raises: a mail outage
must not fail a registration or a password reset."""
import logging
from html import escape

import anyio

from app.config import settings

logger = logging.getLogger(__name__)

VERIFICATION_EXPIRES_HOURS = 24
PASSWORD_RESET_EXPIRES_HOURS = 1

_STYLES = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
       line-height: 1.6; color: #333; background-color: #f4f4f4; margin: 0; padding: 0; }
.container { max-width: 600px; margin: 40px auto; background: white; border-radius: 8px;
             overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.header { background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%); color: white;
          padding: 30px; text-align: center; }
.content { padding: 40px 30px; }
.button { display: inline-block; padding: 14px 32px; background: #2563eb; color: white !important;
          text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0; }
.code-box { background: #f3f4f6; border: 2px dashed #d1d5db; border-radius: 8px; padding: 20px;
            text-align: center; margin: 20px 0; }
.code { font-size: 32px; font-weight: 700; letter-spacing: 8px; color: #2563eb;
        font-family: 'Courier New', monospace; }
.footer { background: #f9fafb; padding: 30px; text-align: center; color: #6b7280; font-size: 14px;
          border-top: 1px solid #e5e7eb; }
.footer a { color: #2563eb; text-decoration: none; }
.warning { background: #fef3c7; border-left: 4px solid #f59e0b; padding: 16px; margin: 20px 0;
           border-radius: 4px; }
.info { background: #dbeafe; border-left: 4px solid #2563eb; padding: 16px; margin: 20px 0;
        border-radius: 4px; }
"""

ROLE_WELCOME_MESSAGES = {
    "STUDENT": "Ya puedes inscribirte en especialidades y comenzar tu camino.",
    "INSTRUCTOR": "Recuerda completar tu curso de protección infantil para crear especialidades.",
    "CLUB_DIRECTOR": "Ya puedes comenzar a gestionar tu club y registrar miembros.",
    "COORDINATOR_ZONE": "Ya puedes supervisar los clubes de tu zona.",
    "ADMIN_ASSOCIATION": "Ya puedes administrar tu asociación.",
    "MASTER_GC": "Recuerda configurar MFA para mayor seguridad.",
}


def base_template(content: str, title: str) -> str:
    frontend = settings.frontend_url
    logo = f"{settings.R2_PUBLIC_URL.rstrip('/')}/adventist-club-fav.png"
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escape(title)}</title>
    <style>{_STYLES}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <img src="{logo}" alt="Adventist.Club" width="64" height="64"
                 style="display:block; margin: 0 auto 12px auto; border-radius: 12px;" />
            <h1 style="margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0.5px;">ADVENTIST.CLUB</h1>
            <p style="margin: 8px 0 0 0; opacity: 0.9; font-size: 14px;">Ecosistema Digital del Ministerio Joven Adventista</p>
        </div>
        <div class="content">{content}</div>
        <div class="footer">
            <p><strong>Adventist.Club</strong> - Una sola misión, una comunidad conectada</p>
            <p>
                <a href="{frontend}">Ir a Adventist.Club</a> |
                <a href="{frontend}/help">Centro de Ayuda</a>
            </p>
            <p style="margin-top: 20px; font-size: 12px; color: #9ca3af;">
                Este es un correo automático. Por favor no respondas a este mensaje.
            </p>
        </div>
    </div>
</body>
</html>
"""


def verification_email_html(name: str, code: str, link: str) -> str:
    content = f"""
        <h2>¡Bienvenido a Adventist.Club, {escape(name)}! 👋</h2>
        <p>Gracias por registrarte en nuestro ecosistema digital del Ministerio Joven Adventista.</p>
        <p>Para activar tu cuenta, verifica tu correo electrónico usando uno de estos métodos:</p>
        <div class="info">
            <strong>Opción 1: Código de Verificación</strong>
            <div class="code-box"><div class="code">{escape(code)}</div></div>
            <p style="margin: 0;">Ingresa este código en la aplicación</p>
        </div>
        <div class="info">
            <strong>Opción 2: Link Directo</strong><br>
            <a href="{escape(link, quote=True)}" class="button">Verificar mi cuenta</a>
        </div>
        <div class="warning">
            <strong>⚠️ Importante:</strong><br>
            • El código expira en <strong>{VERIFICATION_EXPIRES_HOURS} horas</strong><br>
            • Si no solicitaste este registro, puedes ignorar este correo
        </div>
        <p>Una vez verificada tu cuenta, podrás acceder a todas las funcionalidades de Adventist.Club.</p>
    """
    return base_template(content, "Verifica tu cuenta - Adventist.Club")


def password_reset_email_html(name: str, code: str, link: str) -> str:
    content = f"""
        <h2>Solicitud de Restablecimiento de Contraseña</h2>
        <p>Hola {escape(name)},</p>
        <p>Recibimos una solicitud para restablecer la contraseña de tu cuenta en Adventist.Club.</p>
        <div class="info">
            <strong>Código de Restablecimiento</strong>
            <div class="code-box"><div class="code">{escape(code)}</div></div>
            <p style="margin: 0;">Ingresa este código en la aplicación</p>
        </div>
        <p style="text-align: center;"><strong>O</strong></p>
        <div style="text-align: center;">
            <a href="{escape(link, quote=True)}" class="button">Restablecer Contraseña</a>
        </div>
        <div class="warning">
            <strong>⚠️ Seguridad:</strong><br>
            • Este código expira en <strong>{PASSWORD_RESET_EXPIRES_HOURS} hora</strong><br>
            • Si no solicitaste este cambio, ignora este correo y tu contraseña permanecerá sin cambios<br>
            • Nunca compartas este código con nadie
        </div>
        <p>Si tienes problemas, contacta a tu coordinador o administrador.</p>
    """
    return base_template(content, "Restablecer Contraseña - Adventist.Club")


def welcome_email_html(name: str, role: str) -> str:
    frontend = settings.frontend_url
    role_message = ROLE_WELCOME_MESSAGES.get(role, "Ya puedes comenzar a usar Adventist.Club")
    content = f"""
        <h2>¡Cuenta Verificada! 🎉</h2>
        <p>¡Hola {escape(name)}!</p>
        <p>Tu cuenta ha sido verificada exitosamente. ¡Bienvenido a la familia Adventist.Club!</p>
        <div class="info">
            <strong>Tu rol:</strong> {escape(role)}<br>
            {role_message}
        </div>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{frontend}/login" class="button">Iniciar Sesión</a>
        </div>
        <h3>Próximos Pasos:</h3>
        <ul>
            <li>Completa tu perfil</li>
            <li>Explora las especialidades disponibles</li>
            <li>Únete a la comunidad global</li>
        </ul>
        <p>Si necesitas ayuda, visita nuestro <a href="{frontend}/help">Centro de Ayuda</a>.</p>
    """
    return base_template(content, "¡Bienvenido! - Adventist.Club")


def club_decision_email_html(name: str, club_name: str, approved: bool, reason: str | None) -> str:
    frontend = settings.frontend_url
    if approved:
        body = f"""
        <h2>¡Tu club fue aprobado! 🎉</h2>
        <p>Hola {escape(name)},</p>
        <p>La coordinación de tu asociación aprobó el registro de <strong>{escape(club_name)}</strong>.</p>
        <div class="info">Ya puedes gestionar tu club y emitir certificados desde tu panel.</div>
        """
    else:
        reason_html = (
            f'<div class="warning"><strong>Motivo:</strong><br>{escape(reason)}</div>' if reason else ""
        )
        body = f"""
        <h2>Tu solicitud de club no fue aprobada</h2>
        <p>Hola {escape(name)},</p>
        <p>La coordinación de tu asociación revisó el registro de <strong>{escape(club_name)}</strong>
           y por ahora no fue aprobado.</p>
        {reason_html}
        <p>Puedes corregir los datos y enviar una nueva solicitud desde tu panel, o contactar a tu coordinador.</p>
        """
    content = f"""{body}
        <div style="text-align: center; margin: 30px 0;">
            <a href="{frontend}/panel" class="button">Ir a mi panel</a>
        </div>
    """
    return base_template(content, "Registro de club - Adventist.Club")


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
