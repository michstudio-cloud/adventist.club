# Checklist de lanzamiento · Etapa 1 (especialidades + clubes + certificados)

Estado al 2026-09-23. Leyenda: ✅ hecho · 🔧 lo hago yo (en curso o listo para hacer) · 🙋 depende del propietario · ⏳ después de la etapa 1.

## 1. Infraestructura y operación

| # | Ítem | Estado | Detalle |
|---|---|---|---|
| 1.1 | API en Render | 🙋 **Subir de plan `free` a `starter`** | El plan gratuito apaga el servicio tras inactividad: el primer usuario de la mañana espera 30–60 s y los correos/verificaciones pueden fallar por tiempo. Servicio `adventist-club-api` (Ohio), Dashboard → Settings → Instance type. |
| 1.2 | Health check en Render | 🙋 | Settings → Health Check Path = `/api/v1/health` (hoy vacío): Render reinicia solo si el proceso se cuelga. |
| 1.3 | Base de datos Neon | ✅ | Proyecto `adventist.club`, Postgres 18, rama `production`. **Snapshot manual creado** `pre-etapa-1-2026-09-23`. Retención de historial: 6 h (plan actual). |
| 1.4 | Respaldos programados | 🔧 | Programar snapshot diario (7 días) — si el plan de Neon no lo permite, 🙋 subir a Launch. |
| 1.5 | Errores (Sentry) | 🙋 | El SDK está instalado y es no-op sin `SENTRY_DSN`. Crear proyecto en sentry.io (gratis) y poner `SENTRY_DSN` en Render. Sin esto no nos enteramos de los 500. |
| 1.6 | Dominios y TLS | ✅ | conquistadores.app (+www), adventist.club (+www), admin.adventist.club en Vercel; api.adventist.club en Render; media.adventist.club (R2). |
| 1.7 | CORS | ✅ | `CORS_ORIGINS` incluye los tres dominios. |
| 1.8 | Correo saliente | ✅ | Resend con DKIM verificado en adventist.club; DMARC `p=quarantine`. Plantillas rediseñadas. Remitente `hi@adventist.club`. 🙋 Confirmar que `support@adventist.club` (destino de reportes DMARC y contacto legal) existe y alguien lo lee. |
| 1.9 | Variables de Vercel | 🙋 | Borrar `ADMIN_SECRET_KEY` («Needs attention»), `NEXT_ADMIN_EMAIL`, `NEXT_PUBLIC_MAPBOX_TOKEN` (no las usa nadie). |
| 1.10 | Rate limiting | ✅ | Login 5/min, registro 3/h, olvidé contraseña 3/h, perfiles 60/min. |
| 1.11 | Migraciones | ✅ | 007–014 aplicadas en Neon. Próximo número libre: 015. |

## 2. Seguridad y protección de menores

| # | Ítem | Estado | Detalle |
|---|---|---|---|
| 2.1 | 2FA en cuentas MASTER | 🙋 | Activar en Configuración para `mattislas@icloud.com` y `matias@adventist.club`; después 🔧 `MASTER_MFA_ENFORCED=true` en Render. |
| 2.2 | Curso de protección infantil obligatorio para líderes | 🙋 decisión | Hoy nadie lo tiene completado (ni MASTER). ¿Se exige antes de dirigir un club / dictaminar? Si sí: 🔧 gate en backend (ya hay campo y endpoint). |
| 2.3 | Carta de iglesia obligatoria | 🙋 decisión | `LEADER_VERIFICATION_ENFORCED_FROM` sin fecha = no se exige. Poner fecha cuando cada asociación activa tenga al menos un validador. |
| 2.4 | Perfiles de menores | ✅ | Nunca públicos; foto solo con permiso del tutor; sin correo/fecha de nacimiento en la API pública. |
| 2.5 | Evidencias de menores | ✅ | Bucket privado, enlaces firmados de 5–15 min. 🙋 **Prueba real**: subir una foto desde un teléfono (nunca se ha hecho con dispositivo real). |
| 2.6 | Sesiones | ✅ | Cookies HttpOnly, tokens nunca en JS; proxy con control de origen. |
| 2.7 | Auditoría | ✅ | Toda mutación escribe en `audit_log`. |

## 3. Producto (lo que un club real necesita el día 1)

| # | Ítem | Estado | Detalle |
|---|---|---|---|
| 3.1 | Catálogo | ✅ | 809 especialidades publicadas, 13 categorías, 1 151 traducciones, parches en R2 con caché. |
| 3.2 | Estructura | ✅ | 17 divisiones, 182 uniones, 792 asociaciones. **0 zonas, 0 iglesias, 0 clubes reales** (los 4 «clubs» son del prototipo de certificados). 🙋 Crear el primer club real desde admin → Clubes → Nuevo club (o que un director lo registre y tú lo apruebes). |
| 3.3 | Usuarios | 🙋 | 3 cuentas migradas «pendientes de verificar» (miguelcastillo27, jeisondaniel8, carabajalvalentina24): verificar o rechazar en admin → Usuarios. |
| 3.4 | Clases (Amigo…Guía) | ⏳ | `programs` vacío: el bloque F necesita el contenido aprobado. La etapa 1 sale sin clases. |
| 3.5 | Certificados | ✅ | Emisión por director/portafolio, QR y `/verify/{folio}`, revocación. 5 certificados de prueba del prototipo en la base. |
| 3.6 | Flujo completo probado por ti | 🙋 | Registro → verificación por correo → unirse a un club → especialidad → evidencia → revisión → certificado → perfil. |
| 3.7 | Video en vivo | ⏳ | Decisión pendiente (propuesta: YouTube Live + chat propio). |

## 4. Legal y textos

| # | Ítem | Estado | Detalle |
|---|---|---|---|
| 4.1 | Política de privacidad, Términos, Consentimiento del tutor | 🔧 en curso | Páginas `/privacidad`, `/terminos`, `/consentimiento-tutor` con borrador marcado «pendiente de revisión legal». 🙋 Revisar y rellenar `[NOMBRE LEGAL DE LA ORGANIZACIÓN]` y `[JURISDICCIÓN]`. |
| 4.2 | Aceptación en el registro | 🔧 en curso | «Al crear la cuenta aceptas…» con enlaces. |
| 4.3 | Páginas 404 / error, robots, sitemap, Open Graph | 🔧 en curso | Faltaban todas. |
| 4.4 | Fecha de lanzamiento público | 🙋 | `PUBLIC_LAUNCH_DATE` en Render (insignia «fundador» para cuentas anteriores). |

## 5. Después del lanzamiento (no bloquea)

- Uptime externo (UptimeRobot gratis contra `/api/v1/health` y la home).
- Analytics de Vercel (privado, sin cookies).
- Traducciones de categorías (API lista, sin pantalla).
- Portada multilingüe en adventist.club.
- Stickers reales para el álbum (56 px carpeta / 104 px cabecera, encargar a 3×).
- Bloque F (clases y Guía Mayor), Secretaría del club, video en vivo.
