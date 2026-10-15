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
| `RATE_LIMIT_LOGIN_ACCOUNT` | `10/15 minutes` | Por correo: `/auth/login` (en el nodo, techo `60/15 minutes`) |
| `LOGIN_LOCKOUT_MODE` | `account` | `account` (producción) o `device` (nodo del evento): ver abajo |
| `RATE_LIMIT_LOGIN_ACCOUNT_DEVICE` | `5/5 minutes` | Sólo en modo `device`: por correo + dispositivo (IP) |
| `RATE_LIMIT_LOGIN_DEVICE` | `20/5 minutes` | Sólo en modo `device`: inicios de sesión **fallidos** por dispositivo, cualquier cuenta |
| `RATE_LIMIT_REGISTER` | `20/hour` | Por IP: `/auth/register` |
| `RATE_LIMIT_REGISTER_ACCOUNT` | `5/hour` | Por correo: `/auth/register` |
| `RATE_LIMIT_FORGOT_PASSWORD` | `20/hour` | Por IP: `/auth/forgot-password` |
| `RATE_LIMIT_FORGOT_PASSWORD_ACCOUNT` | `3/hour` | Por correo: `/auth/forgot-password` |
| `RATE_LIMIT_RESET_PASSWORD` | `30/minute` | Por IP: `/auth/reset-password` |
| `RATE_LIMIT_INVITATION_PREVIEW` | `30/minute` | Por IP: vistas previas de invitaciones de membresía, de consentimiento y de organización |
| `PROXY_SHARED_SECRET` | sin valor | Secreto compartido con el servidor de Next (≥ 32 caracteres). Sin valor, `X-Proxy-Client-IP` nunca se cree |
| `NODO_OPERADOR_TOKEN` | sin valor | Sólo en el nodo: token de coordinación (≥ 32 caracteres) para `/api/v1/nodo/operador/*`. Sin valor (o más corto), esas rutas responden 404 |

`/auth/verify-email` (con sesión) cuenta ahora por cuenta (`account_or_ip`), 10/minuto.

Dónde poner `PROXY_SHARED_SECRET` (el mismo valor en ambos lados, generado al azar, p. ej.
`openssl rand -hex 32`):

- **API (Render):** `PROXY_SHARED_SECRET`.
- **Web (Vercel, proyecto del frontend):** `PROXY_SHARED_SECRET` (variable de servidor, nunca
  `NEXT_PUBLIC_`).

Orden de despliegue: cualquiera. Si sólo uno de los dos lados lo tiene, todo sigue como hoy
(la API ignora la cabecera o la web no la manda).

## Nodo local del evento

La API del nodo recibe la IP real de cada teléfono: Caddy la pone en `X-Forwarded-For` (no
confía en la que mande el cliente) y Next la pasa a la API en `X-Proxy-Client-IP` con
`PROXY_SHARED_SECRET` (propio del nodo). Así cada teléfono es un «dispositivo». Sin el secreto,
todos comparten la IP del contenedor web y el modo `device` se comporta como un solo dispositivo.

### Bloqueo por dispositivo (`LOGIN_LOCKOUT_MODE=device`)

El problema: en la Wi-Fi del evento un desconocido puede escribir contraseñas falsas con el correo
de un juez y, con el límite por cuenta de producción (10 en 15 min, para todos), dejarlo fuera
15 minutos. En el nodo (`docker-compose.yml`, ancla `x-api-env`) el modo es `device`:

| Capa | Nodo | Qué hace |
| --- | --- | --- |
| Por IP (`RATE_LIMIT_LOGIN`) | `200/minute` | Igual que siempre (cada petición cuenta) |
| Correo + dispositivo (`RATE_LIMIT_LOGIN_ACCOUNT_DEVICE`) | `5/5 minutes` | Cada intento cuenta. El teléfono que insiste con la cuenta de un juez se bloquea **a sí mismo** para esa cuenta; el juez entra desde su teléfono |
| Por cuenta (`RATE_LIMIT_LOGIN_ACCOUNT`) | `60/15 minutes` | Techo contra la fuerza bruta repartida en muchos teléfonos (un solo teléfono llega como mucho a 15 en 15 min) |
| Por dispositivo (`RATE_LIMIT_LOGIN_DEVICE`) | `20/5 minutes` | Sólo cuentan los **fallos**, de cualquier cuenta: frena a quien recorre muchos correos desde un teléfono. Los inicios correctos no cuentan (jueces detrás de un punto de acceso con NAT no se estorban) |

- La contraseña correcta borra el contador del correo (global) y el de correo + ese dispositivo.
- El 429 es el mismo de siempre («Demasiados intentos. Espera N minutos…»), exista o no la
  cuenta: nunca revela qué correos están registrados.
- En el nodo los inicios de sesión fallidos se auditan (`audit_log.action = AUTH_LOGIN_FAILED`,
  `entity_id` = id del usuario o el correo escrito si no existe, `metadata_json = {"email",
  "cuenta_existe"}`, IP del dispositivo). Nunca se guarda la contraseña escrita. Los 429 no se
  auditan (los límites ya acotan el volumen). En producción (`account`) no cambia nada.
- Producción sigue con `account`: exactamente el comportamiento de antes.

### Rutas del operador del nodo (coordinación, desde la Mac)

Prefijo `/api/v1/nodo/operador`. Cabecera `X-Nodo-Operador: <NODO_OPERADOR_TOKEN>` (comparación en
tiempo constante). Sin `NODO_OPERADOR_TOKEN` (producción) → **404**; sin cabecera → 401; token
incorrecto → 403. La app de Mac genera el token, lo guarda en el Llavero y lo pasa por el entorno
como los demás secretos. No salen en `/docs`.

**`POST /cuentas/desbloquear`** — cuerpo `{"email": "juez@ejemplo.org"}` (se normaliza). Borra
todos los contadores de inicio de sesión de ese correo (global y por dispositivo). No borra el
límite por dispositivo entre cuentas: un teléfono que recorrió correos sigue esperando.
Se audita como `AUTH_LOGIN_UNLOCK` (actor «operador del nodo», en `details` y
`metadata_json.actor`).

```json
{"email": "juez@ejemplo.org", "desbloqueada": true, "contadores_borrados": 3,
 "dispositivos": ["10.20.0.66", "10.20.0.10"]}
```

**`GET /cuentas/bloqueadas`** — cuentas que hoy no pueden entrar desde todos lados
(`bloqueo_global`) o desde algún dispositivo:

```json
{"generado": "2026-10-15T18:00:00+00:00", "modo": "device",
 "cuentas": [{"email": "juez@ejemplo.org", "bloqueo_global": false, "intentos": 7, "limite": 60,
              "hasta": "2026-10-15T18:12:00+00:00",
              "dispositivos": [{"ip": "10.20.0.66", "intentos": 5, "limite": 5,
                                "hasta": "2026-10-15T18:04:00+00:00"}]}]}
```

**`GET /dispositivos?minutos=60`** (1–720) — para el panel «Dispositivos conectados»: cada IP vista
en esos minutos, la más reciente primero.

```json
{"generado": "…", "minutos": 60, "total": 1,
 "dispositivos": [{"ip": "10.20.0.66", "primera_vez": "…", "ultima_vez": "…",
   "cuenta": {"email": "juez@ejemplo.org", "nombre": "Ana Pérez", "a_las": "…"},
   "fallos_10min": 4, "ultimo_fallo": "…",
   "correos_distintos": 4, "correos": ["a@x.org", "b@x.org", "c@x.org", "d@x.org"],
   "sospechoso": true, "peticiones": 120, "peticiones_por_minuto": 3.4,
   "bloqueado_hasta": null}]}
```

- `cuenta`: el último inicio de sesión correcto desde esa IP (o `null`).
- `fallos_10min` / `correos_distintos`: fallos y correos distintos con fallo en los últimos 10 min;
  `correos` sólo aparece (y `sospechoso` es `true`) con más de 3.
- `peticiones`: peticiones a la API en la ventana (hasta 60 min); `peticiones_por_minuto`:
  promedio de los últimos 5 minutos. No cuentan el healthcheck ni las rutas del operador.
- `bloqueado_hasta`: si el dispositivo agotó `RATE_LIMIT_LOGIN_DEVICE`, hasta cuándo.
- En memoria del proceso, con tope de 2000 IP (se olvidan las menos recientes y las de más de
  12 h), nunca se guarda en disco: un reinicio lo vacía. El nodo corre **un solo** proceso de
  uvicorn (`nodo-evento/api.Dockerfile`), así que la vista es completa. Sólo se registra en el
  nodo (modo `device` o con `NODO_OPERADOR_TOKEN`); en producción no hace nada.
- Las peticiones que Next hace por su cuenta (render del servidor, sin `X-Proxy-Client-IP`)
  aparecen con la IP del contenedor web.

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
  no permanente). Es el precio de limitar por cuenta. En el nodo (modo `device`) necesita muchos
  teléfonos para eso, y coordinación puede desbloquear la cuenta (`/cuentas/desbloquear`).
