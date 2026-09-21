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


def mfa_reset_email_html(name: str, reason: str) -> str:
    frontend = settings.frontend_url
    content = f"""
        <h2>Se restableció tu verificación en dos pasos</h2>
        <p>Hola {escape(name)},</p>
        <p>Otro administrador con rol MASTER restableció la verificación en dos pasos de tu cuenta.
           Tu autenticador y tus códigos de recuperación anteriores ya no sirven.</p>
        <div class="info"><strong>Motivo registrado:</strong><br>{escape(reason)}</div>
        <div class="warning">
            <strong>⚠️ Si no pediste esto</strong>, avisa de inmediato al equipo: alguien con acceso
            MASTER actuó sobre tu cuenta. El cambio queda registrado en la auditoría.
        </div>
        <p>Vuelve a activar la verificación en dos pasos en cuanto inicies sesión.</p>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{frontend}/panel" class="button">Ir a mi panel</a>
        </div>
    """
    return base_template(content, "Verificación en dos pasos restablecida - Adventist.Club")


def recovery_code_used_email_html(name: str) -> str:
    content = f"""
        <h2>Se usó uno de tus códigos de recuperación</h2>
        <p>Hola {escape(name)},</p>
        <p>Alguien inició sesión en tu cuenta con un código de recuperación en lugar del código
           de tu aplicación de autenticación. Ese código ya quedó marcado como usado.</p>
        <div class="warning">
            <strong>⚠️ Si no fuiste tú</strong>, cambia tu contraseña ahora mismo y pide que se
            restablezca tu verificación en dos pasos.
        </div>
        <p>Si perdiste tu autenticador, vuelve a configurarlo y genera códigos nuevos desde tu perfil.</p>
    """
    return base_template(content, "Código de recuperación usado - Adventist.Club")


ROLE_LABELS = {
    "STUDENT": "miembro",
    "COUNSELOR": "consejero(a) de unidad",
    "INSTRUCTOR": "instructor(a)",
    "CLUB_SECRETARY": "secretario(a) del club",
    "CLUB_DIRECTOR": "director(a)",
}


def club_invitation_email_html(club_name: str, role: str, link: str, inviter_name: str) -> str:
    role_label = ROLE_LABELS.get(role, "miembro")
    content = f"""
        <h2>Te invitaron a un club</h2>
        <p><strong>{escape(inviter_name)}</strong> te invita a unirte a
           <strong>{escape(club_name)}</strong> en Adventist.Club como {escape(role_label)}.</p>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{escape(link, quote=True)}" class="button">Unirme al club</a>
        </div>
        <div class="warning">
            <strong>⚠️ Este enlace es personal:</strong> no lo compartas. Si no esperabas esta
            invitación, puedes ignorar este correo.
        </div>
    """
    return base_template(content, "Invitación a un club - Adventist.Club")


def consent_request_email_html(child_name: str, club_name: str, link: str) -> str:
    """Goes to an adult about a minor in their care, so it does name the minor.
    Nothing else in block E sends a minor's name to a third party."""
    content = f"""
        <h2>Autorización para unirse a un club</h2>
        <p><strong>{escape(child_name)}</strong> pidió unirse a
           <strong>{escape(club_name)}</strong> en Adventist.Club y necesita la autorización
           de su madre, padre o tutor.</p>
        <div class="info">
            En la página verás qué datos vería el club (nombre, edad, avance y evidencias) y
            quiénes los verían. Sin tu autorización, el club no tiene acceso a nada.
        </div>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{escape(link, quote=True)}" class="button">Revisar y autorizar</a>
        </div>
        <div class="warning">
            <strong>⚠️ Importante:</strong><br>
            • El enlace vence en <strong>14 días</strong> y sirve una sola vez<br>
            • Puedes retirar la autorización cuando quieras desde tu panel<br>
            • Si no reconoces esta solicitud, ignora este correo
        </div>
    """
    return base_template(content, "Autorización para unirse a un club - Adventist.Club")


def pending_requests_email_html(director_name: str, club_name: str, pending: int, link: str) -> str:
    """To the club's staff. It carries a COUNT and a link, never a name: some
    of the people in that queue are minors (spec §5.10)."""
    what = "una solicitud" if pending == 1 else f"{pending} solicitudes"
    content = f"""
        <h2>Tienes solicitudes por revisar</h2>
        <p>Hola {escape(director_name)},</p>
        <p><strong>{escape(club_name)}</strong> tiene {what} de ingreso esperando tu decisión.</p>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{escape(link, quote=True)}" class="button">Revisar solicitudes</a>
        </div>
    """
    return base_template(content, "Solicitudes por revisar - Adventist.Club")


def membership_decision_email_html(
    name: str, club_name: str, approved: bool, reason: str | None
) -> str:
    frontend = settings.frontend_url
    if approved:
        body = f"""
        <h2>¡Ya eres parte del club! 🎉</h2>
        <p>Hola {escape(name)},</p>
        <p><strong>{escape(club_name)}</strong> aceptó tu ingreso.</p>
        """
    else:
        reason_html = (
            f'<div class="warning"><strong>Motivo:</strong><br>{escape(reason)}</div>'
            if reason
            else ""
        )
        body = f"""
        <h2>Novedades sobre tu membresía</h2>
        <p>Hola {escape(name)},</p>
        <p>Tu membresía en <strong>{escape(club_name)}</strong> no sigue adelante por ahora.</p>
        {reason_html}
        <p>Puedes buscar otro club cercano o hablar con la dirección del club.</p>
        """
    content = f"""{body}
        <div style="text-align: center; margin: 30px 0;">
            <a href="{frontend}/panel" class="button">Ir a mi panel</a>
        </div>
    """
    return base_template(content, "Tu membresía de club - Adventist.Club")


def letter_submitted_email_html(
    reviewer_name: str, applicant_name: str, role: str, link: str
) -> str:
    """To whoever validates letters. It names an ADULT who presented their own
    document, and nothing about any minor."""
    role_label = ROLE_LABELS.get(role, "instructor(a)")
    content = f"""
        <h2>Una carta de iglesia espera tu validación</h2>
        <p>Hola {escape(reviewer_name)},</p>
        <p><strong>{escape(applicant_name)}</strong> presentó la carta de su iglesia para el
           cargo de {escape(role_label)}.</p>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{escape(link, quote=True)}" class="button">Revisar la carta</a>
        </div>
        <div class="info">
            Sin una carta válida, esa persona no puede dictaminar ni ver datos de menores.
        </div>
    """
    return base_template(content, "Carta por validar - Adventist.Club")


def letter_decision_email_html(
    name: str, status: str, valid_until: str | None, note: str | None
) -> str:
    frontend = settings.frontend_url
    note_html = (
        f'<div class="warning"><strong>Motivo:</strong><br>{escape(note)}</div>' if note else ""
    )
    if status == "AUTHORIZED":
        body = f"""
        <h2>Tu carta fue validada ✅</h2>
        <p>Hola {escape(name)},</p>
        <p>Tu carta de la iglesia quedó autorizada{
            f" y vale hasta el <strong>{escape(valid_until)}</strong>" if valid_until else ""
        }.</p>
        <div class="info">
            Podrás renovarla desde 60 días antes de esa fecha. Al vencer, vuelves a
            «sin verificar» hasta presentar una nueva.
        </div>
        """
    elif status == "REVOKED":
        body = f"""
        <h2>Se retiró tu validación</h2>
        <p>Hola {escape(name)},</p>
        <p>La administración revocó la validación de tu carta de la iglesia.</p>
        {note_html}
        """
    else:
        body = f"""
        <h2>Tu carta no fue validada</h2>
        <p>Hola {escape(name)},</p>
        <p>La administración revisó la carta de tu iglesia y por ahora no la aceptó.
           Puedes presentar otra.</p>
        {note_html}
        """
    content = f"""{body}
        <div style="text-align: center; margin: 30px 0;">
            <a href="{frontend}/panel" class="button">Ir a mi panel</a>
        </div>
    """
    return base_template(content, "Tu carta de la iglesia - Adventist.Club")


def letter_expiring_email_html(name: str, valid_until: str, days: int) -> str:
    frontend = settings.frontend_url
    content = f"""
        <h2>Tu carta de la iglesia está por vencer</h2>
        <p>Hola {escape(name)},</p>
        <p>Tu validación vence el <strong>{escape(valid_until)}</strong>, dentro de
           {days} días. Ya puedes presentar la carta del nuevo periodo.</p>
        <div class="warning">
            Al vencer vuelves a «sin verificar»: no podrás dictaminar ni ver datos de
            menores hasta renovarla.
        </div>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{frontend}/panel" class="button">Renovar mi carta</a>
        </div>
    """
    return base_template(content, "Tu carta de la iglesia vence pronto - Adventist.Club")


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
