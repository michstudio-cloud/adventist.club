# Límites de intentos (rate limiting)

Código: `backend/app/rate_limit.py` (slowapi + `limits`, almacenamiento en memoria del proceso).
Pruebas: `backend/tests/test_rate_limit.py`.

## El problema que resuelve

Todos los inicios de sesión pasan por el servidor de Next (puente `/api/session/*` y proxy
`/api/v1/*` en Vercel). La API veía la IP de ese servidor, así que **todas las personas
compartían un solo cupo** de 5 intentos por minuto. En un camporí, 20–40 jueces entran en pocos
minutos detrás de la misma IP pública (NAT del lugar) o del nodo local del evento.

## Dos capas

1. **Por dirección IP** (decoradores de slowapi). Alto, para tolerar el NAT de un evento.
2. **Por cuenta** (correo normalizado: `strip().lower()`), además de la anterior. Es la barrera
   real contra la fuerza bruta: cambiar de IP no da más intentos contra una cuenta. Se cuenta por
   el correo escrito, exista o no la cuenta, así que un 429 nunca revela qué correos están
   registrados.
   - `login`: cada intento cuenta (sin carreras entre peticiones en paralelo); la contraseña
     correcta borra el contador.
   - `forgot-password` y `register`: cada petición cuenta.
   - `mfa/verify` y `reset-password` (por código) ya estaban limitados por cuenta con
     `code_attempts` (bloqueo tras códigos incorrectos).

## La IP real: `X-Proxy-Client-IP` + `X-Proxy-Auth`

El servidor de Next manda la IP de quien visita (de `x-real-ip` / `x-forwarded-for` de Vercel,
que Vercel sobrescribe) en `X-Proxy-Client-IP`, junto con el secreto compartido en `X-Proxy-Auth`.
La API usa esa IP **sólo** si el secreto coincide con `PROXY_SHARED_SECRET` (comparación en tiempo
constante, `hmac.compare_digest`) y el valor es una IP válida. En cualquier otro caso (sin secreto
configurado, secreto ausente o incorrecto, cabecera falsificada por un cliente) se ignora y se
usa lo de siempre: el primer salto de `X-Forwarded-For` y, si no hay, la IP del socket.

La misma IP es la que guarda la auditoría (`audit_log.ip_address`, vía `client_ip`).

Un secreto de menos de 32 caracteres se ignora (con un aviso en el log).

> El respaldo (`X-Forwarded-For` sin secreto) lo puede falsificar un cliente que llame a la API
> directamente: es amortiguación, no una frontera de seguridad. La frontera son los límites por
> cuenta.

## Variables de entorno

Todas opcionales. Sintaxis de `limits` (`"60/minute"`, `"10/15 minutes"`, `"20/hour"`). Un valor
vacío o ilegible usa el valor por omisión (con aviso), nunca apaga el límite.

| Variable | Por omisión | Qué limita |
| --- | --- | --- |
| `RATE_LIMIT_ENABLED` | `true` | Interruptor general |
| `RATE_LIMIT_LOGIN` | `60/minute` | Por IP: `/auth/login` y `/auth/mfa/verify` |
| `RATE_LIMIT_LOGIN_ACCOUNT` | `10/15 minutes` | Por correo: `/auth/login` |
| `RATE_LIMIT_REGISTER` | `20/hour` | Por IP: `/auth/register` |
| `RATE_LIMIT_REGISTER_ACCOUNT` | `5/hour` | Por correo: `/auth/register` |
| `RATE_LIMIT_FORGOT_PASSWORD` | `20/hour` | Por IP: `/auth/forgot-password` |
| `RATE_LIMIT_FORGOT_PASSWORD_ACCOUNT` | `3/hour` | Por correo: `/auth/forgot-password` |
| `RATE_LIMIT_RESET_PASSWORD` | `30/minute` | Por IP: `/auth/reset-password` |
| `RATE_LIMIT_INVITATION_PREVIEW` | `30/minute` | Por IP: vistas previas de invitaciones de membresía, de consentimiento y de organización |
| `PROXY_SHARED_SECRET` | sin valor | Secreto compartido con el servidor de Next (≥ 32 caracteres). Sin valor, `X-Proxy-Client-IP` nunca se cree |

`/auth/verify-email` (con sesión) cuenta ahora por cuenta (`account_or_ip`), 10/minuto.

Dónde poner `PROXY_SHARED_SECRET` (el mismo valor en ambos lados, generado al azar, p. ej.
`openssl rand -hex 32`):

- **API (Render):** `PROXY_SHARED_SECRET`.
- **Web (Vercel, proyecto del frontend):** `PROXY_SHARED_SECRET` (variable de servidor, nunca
  `NEXT_PUBLIC_`).

Orden de despliegue: cualquiera. Si sólo uno de los dos lados lo tiene, todo sigue como hoy
(la API ignora la cabecera o la web no la manda).

## Nodo local del evento

Sin `PROXY_SHARED_SECRET` en el nodo, la API ve la IP del contenedor de Next para todos: un solo
cupo por IP de 60/minuto y los límites por cuenta intactos. Para 20–40 jueces entrando en unos
minutos alcanza, y la protección contra fuerza bruta sigue (por cuenta). Con el secreto en la API
y en la web del nodo, cuenta por dispositivo: Caddy pone en `X-Forwarded-For` la IP del teléfono
(no confía en la que mande el cliente salvo `trusted_proxies`).

## Respuesta 429

```
HTTP/1.1 429 Too Many Requests
Retry-After: 540
{"detail": "Demasiados intentos. Espera 9 minutos y vuelve a intentarlo."}
```

El puente de sesión y el proxy de la web reenvían `Retry-After`; la pantalla de inicio de sesión
muestra el mensaje y deja el botón en cuenta atrás («Espera 8:59») hasta que se pueda volver a
intentar (cambiar el correo lo libera: la espera puede ser de otra cuenta).

## Límites conocidos

- Memoria del proceso: con varios procesos/instancias cada uno lleva su cuenta (hoy Render corre
  uno). Un reinicio vacía los contadores.
- Un atacante puede agotar el cupo de una cuenta concreta durante 15 minutos (bloqueo temporal,
  no permanente). Es el precio de limitar por cuenta.
