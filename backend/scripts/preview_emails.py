"""Render every transactional e-mail with realistic sample data, one HTML file each,
plus an index.html that links them all — open it in a browser to review the designs.

    cd backend
    python scripts/preview_emails.py [output_dir]   # default: $TMPDIR/email-previews

Nothing is sent and no database is touched. The links point at the production
domains (conquistadores.app / admin.adventist.club) so the previews read as the
real thing; the sample names and codes are invented.
"""
import os
import sys
import tempfile
from html import escape
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
# Rendering needs the settings object, not a database.
os.environ.setdefault("DATABASE_URL", "postgresql://preview@localhost/preview")

from app.config import settings  # noqa: E402
from app.services import email as mail  # noqa: E402

settings.FRONTEND_URL = "https://www.conquistadores.app"
settings.ADMIN_WEB_URL = "https://admin.adventist.club"

APP = settings.frontend_url
ADMIN = settings.admin_url

# (file name, what it is, subject line, html)
PREVIEWS = [
    (
        "verification",
        "Verificación de correo",
        "Verifica tu cuenta - Adventist.Club",
        mail.verification_email_html("Ana Martínez", "482913", f"{APP}/verify-email?token=5f1c2a9e"),
    ),
    (
        "password_reset",
        "Restablecer contraseña",
        "Restablecer Contraseña - Adventist.Club",
        mail.password_reset_email_html("Ana Martínez", "730511", f"{APP}/reset-password?token=9ab04e71"),
    ),
    ("welcome_student", "Bienvenida · miembro", "¡Bienvenido a Adventist.Club! 🎉", mail.welcome_email_html("Ana Martínez", "STUDENT")),
    ("welcome_instructor", "Bienvenida · instructor(a)", "¡Bienvenido a Adventist.Club! 🎉", mail.welcome_email_html("Luis Herrera", "INSTRUCTOR")),
    ("welcome_director", "Bienvenida · director(a)", "¡Bienvenido a Adventist.Club! 🎉", mail.welcome_email_html("Marta Solís", "CLUB_DIRECTOR")),
    ("welcome_guardian", "Bienvenida · tutor(a)", "¡Bienvenido a Adventist.Club! 🎉", mail.welcome_email_html("Rosa Díaz", "PARENT_GUARDIAN")),
    ("welcome_coordinator", "Bienvenida · coordinación de zona", "¡Bienvenido a Adventist.Club! 🎉", mail.welcome_email_html("Jorge Treviño", "COORDINATOR_ZONE")),
    ("welcome_master", "Bienvenida · MASTER", "¡Bienvenido a Adventist.Club! 🎉", mail.welcome_email_html("Daniel Ruiz", "MASTER_GC")),
    (
        "club_approved",
        "Club aprobado",
        "Tu club fue aprobado - Adventist.Club",
        mail.club_decision_email_html("Marta Solís", "Club Orión", True, None),
    ),
    (
        "club_rejected",
        "Club no aprobado",
        "Tu solicitud de club no fue aprobada - Adventist.Club",
        mail.club_decision_email_html(
            "Marta Solís", "Club Orión", False, "Falta el nombre de la iglesia que patrocina el club."
        ),
    ),
    (
        "mfa_reset",
        "Verificación en dos pasos restablecida",
        "Se restableció tu verificación en dos pasos - Adventist.Club",
        mail.mfa_reset_email_html("Daniel Ruiz", "Perdió el teléfono con el autenticador (ticket 1142)."),
    ),
    (
        "recovery_code_used",
        "Código de recuperación usado",
        "Se usó un código de recuperación - Adventist.Club",
        mail.recovery_code_used_email_html("Daniel Ruiz"),
    ),
    (
        "club_invitation",
        "Invitación a un club",
        "Te invitaron a Club Orión - Adventist.Club",
        mail.club_invitation_email_html("Club Orión", "INSTRUCTOR", f"{APP}/join?t=c81d0f", "Marta Solís"),
    ),
    (
        "consent_request",
        "Autorización de tutor(a)",
        "Autorización para unirse a un club - Adventist.Club",
        mail.consent_request_email_html("Sofía Díaz", "Club Orión", f"{APP}/consent?t=77e2b1"),
    ),
    (
        "pending_requests",
        "Solicitudes por revisar",
        "Solicitudes por revisar en Club Orión - Adventist.Club",
        mail.pending_requests_email_html("Marta Solís", "Club Orión", 3, f"{APP}/panel/club"),
    ),
    (
        "membership_approved",
        "Membresía aceptada",
        "Tu membresía de club - Adventist.Club",
        mail.membership_decision_email_html("Ana Martínez", "Club Orión", True, None),
    ),
    (
        "membership_rejected",
        "Membresía no aceptada",
        "Tu membresía de club - Adventist.Club",
        mail.membership_decision_email_html(
            "Ana Martínez", "Club Orión", False, "El club ya completó su cupo para este año."
        ),
    ),
    (
        "letter_submitted",
        "Carta por validar (staff)",
        "Carta de iglesia por validar - Adventist.Club",
        mail.letter_submitted_email_html("Jorge Treviño", "Luis Herrera", "INSTRUCTOR", f"{ADMIN}/admin/cartas"),
    ),
    (
        "letter_authorized",
        "Carta validada",
        "Tu carta de la iglesia - Adventist.Club",
        mail.letter_decision_email_html("Luis Herrera", "AUTHORIZED", "31/12/2027", None),
    ),
    (
        "letter_rejected",
        "Carta no validada",
        "Tu carta de la iglesia - Adventist.Club",
        mail.letter_decision_email_html("Luis Herrera", "REJECTED", None, "La carta no tiene firma del secretario de iglesia."),
    ),
    (
        "letter_revoked",
        "Validación retirada",
        "Tu carta de la iglesia - Adventist.Club",
        mail.letter_decision_email_html("Luis Herrera", "REVOKED", None, "La iglesia retiró su respaldo."),
    ),
    (
        "letter_expiring",
        "Carta por vencer",
        "Tu carta de la iglesia vence pronto - Adventist.Club",
        mail.letter_expiring_email_html("Luis Herrera", "31/12/2026", 30),
    ),
    (
        "progress_incomplete",
        "Avance · observación",
        "Tienes una observación en tu especialidad - Adventist.Club",
        mail.progress_email_html(
            "Ana Martínez", "PROGRESS_INCOMPLETE", "Nudos y amarres",
            "Falta la foto del nudo as de guía terminado.", f"{APP}/portfolio/enrollments/2f9c",
        ),
    ),
    (
        "progress_ready",
        "Avance · lista para certificar",
        "¡Tu especialidad está lista para certificar! - Adventist.Club",
        mail.progress_email_html("Ana Martínez", "PROGRESS_READY", "Nudos y amarres", None, f"{APP}/portfolio/enrollments/2f9c"),
    ),
    (
        "progress_certified",
        "Avance · certificada",
        "¡Especialidad certificada! 🎉 - Adventist.Club",
        mail.progress_email_html("Ana Martínez", "PROGRESS_CERTIFIED", "Nudos y amarres", None, f"{APP}/portfolio/enrollments/2f9c"),
    ),
    (
        "hours_rejected",
        "Horas no aprobadas",
        "Horas no aprobadas - Adventist.Club",
        mail.hours_rejected_email_html(
            "Ana Martínez", 2.5, 0, "Falta la constancia firmada por la iglesia.", f"{APP}/portfolio/horas"
        ),
    ),
    (
        "course_pending_reviews",
        "Requisitos por revisar · curso (staff)",
        "Requisitos por revisar en Primeros auxilios - Adventist.Club",
        mail.course_pending_reviews_email_html("Jorge Treviño", "Primeros auxilios", 3, f"{APP}/teach/courses/7c1e"),
    ),
    (
        "certificate_revoked",
        "Certificado anulado",
        "Tu certificado fue anulado - Adventist.Club",
        mail.certificate_revoked_email_html("Ana Martínez", "ANT-2026-000412", "Nudos y amarres"),
    ),
]


def main() -> None:
    default = Path(os.environ.get("EMAIL_PREVIEW_DIR", Path(tempfile.gettempdir()) / "email-previews"))
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, label, subject, html in PREVIEWS:
        (out / f"{name}.html").write_text(html, encoding="utf-8")
        rows.append(
            f'<li><a href="{name}.html" target="preview">{escape(label)}</a>'
            f"<span>{escape(subject)}</span></li>"
        )
    index = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Correos · vista previa</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
       background:#050608; color:#f7f8fb; display:flex; min-height:100vh; }}
nav {{ width:320px; flex:none; padding:24px 16px; border-right:1px solid #262a32; overflow:auto; max-height:100vh; box-sizing:border-box; }}
h1 {{ font-size:12px; letter-spacing:.16em; text-transform:uppercase; color:#dce0e8; margin:0 0 16px; }}
ul {{ list-style:none; padding:0; margin:0; }}
li {{ margin:0 0 6px; }}
a {{ display:block; padding:10px 12px; border-radius:14px; color:#f7f8fb; text-decoration:none; font-weight:600; font-size:14px; }}
a:hover {{ background:#15171c; }}
span {{ display:block; padding:0 12px 6px; font-size:12px; color:#9aa1ae; }}
iframe {{ flex:1; border:0; min-height:100vh; background:#0a0b0d; }}
@media (max-width:760px) {{ body {{ display:block; }} nav {{ width:auto; max-height:none; border:0; }} iframe {{ width:100%; }} }}
</style></head>
<body><nav><h1>Correos · {len(PREVIEWS)} vistas</h1><ul>{''.join(rows)}</ul></nav>
<iframe name="preview" src="{PREVIEWS[0][0]}.html" title="Vista previa"></iframe></body></html>
"""
    (out / "index.html").write_text(index, encoding="utf-8")
    print(f"{len(PREVIEWS)} previews + index.html in {out}")


if __name__ == "__main__":
    main()
