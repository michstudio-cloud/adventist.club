# Passkeys (WebAuthn)

Migración `032_webauthn.sql` · código en `backend/app/services/passkeys.py` y
`backend/app/routers/passkeys.py` · pruebas en `backend/tests/test_passkeys.py`.

Una passkey sirve para **entrar sin contraseña** y cuenta como **segundo factor** de las cuentas
cuyo rol lo exige. Funciona con el Llavero de iCloud (iPhone, iPad, Mac), Google Password Manager
(Android, Chrome), Windows Hello y llaves de seguridad.

## Encendido y apagado

| Variable | Valor en producción | Qué hace |
| --- | --- | --- |
| `WEBAUTHN_RP_ID` | `adventist.club` | Dominio al que quedan atadas las passkeys |
| `WEBAUTHN_ORIGINS` | `https://adventist.club,https://events.adventist.club,https://admin.adventist.club,https://conquistadores.app` | Orígenes exactos desde los que se acepta una ceremonia |
| `WEBAUTHN_RP_NAME` | `Adventist.Club` (por omisión) | Nombre que muestra el sistema al guardar la passkey |
| `RATE_LIMIT_PASSKEY_REGISTER` | `20/hour` (por omisión) | Altas por cuenta |

**Sin `WEBAUTHN_RP_ID` o sin `WEBAUTHN_ORIGINS` todo queda apagado**: las rutas responden 404,
`GET /auth/passkeys/config` dice `{"enabled": false}`, nada lee ni escribe las tablas de 032 y la
web oculta sus controles. Es también el apagado de emergencia: las passkeys registradas dejan de
contar y una cuenta obligada vuelve a ser enviada al alta (con aplicación de autenticación).

**Nunca pongas las dos variables antes de aplicar la migración 032**: con ellas el inicio de sesión
consulta `webauthn_credentials` y, si la tabla no existe, responde 500 a las cuentas con dos pasos y
a los `MASTER_GC`.

Orden de despliegue: **(1)** aplicar `032_webauthn.sql`; **(2)** desplegar la API (`render.yaml`
tiene `autoDeploy: true`: fusionar a `main` despliega; con las variables vacías el código nuevo
se comporta como el anterior); **(3)** desplegar la web; **(4)** poner las dos variables.

## Por qué `adventist.club`

El RP ID debe ser un sufijo registrable del host de la página. `adventist.club` cubre
`adventist.club`, `events.adventist.club` y `admin.adventist.club`: una sola passkey sirve en los
tres. `conquistadores.app` es otro dominio registrable; la web lo autoriza con
`https://adventist.club/.well-known/webauthn` (Related Origin Requests: Chrome/Edge 128+,
Safari 18+). En un navegador sin esa función la web oculta el botón en `conquistadores.app` y
quedan la contraseña y el código, como siempre.

La API no confía en eso: valida el origen exacto de cada ceremonia contra `WEBAUTHN_ORIGINS` y el
hash del RP ID.

## Qué cuenta como segundo factor

- **Entrar con passkey** exige siempre verificación del usuario (Face ID, huella, PIN). Es
  posesión + biometría, así que el token sale con el claim `mfa` — «nacido de segundo factor» — y
  cumple la política de `services/mfa.py` (`MFA_REQUIRED_ROLES`, `MASTER_MFA_ENFORCED`).
- **Rol obligado** (hoy `MASTER_GC`): cumple con TOTP **o** con al menos una passkey. Con sólo
  passkey, la contraseña no abre sesión por sí sola: `/auth/login` responde `mfa_required` con
  `mfa_methods: ["passkey", "recovery"]`.
- **Resto de cuentas**: una passkey es un atajo. La contraseña sola sigue abriendo sesión. Si la
  cuenta tiene TOTP, en el segundo paso se ofrece la passkey además del código
  (`mfa_methods: ["passkey", "totp", "recovery"]`).
- `users.mfa_enabled` y `users.mfa_secret` significan lo de siempre (TOTP). 032 no toca `users`.

La primera passkey de una cuenta obligada sin TOTP **es su alta**: `register/verify` devuelve los
diez códigos de recuperación (una sola vez) y `reauth_required` cuando la política está encendida,
igual que `/auth/mfa/verify-setup`.

## Rutas (`/api/v1/auth/passkeys`)

| Ruta | Quién | Qué |
| --- | --- | --- |
| `GET /config` | público | `{enabled}` |
| `POST /register/options` | sesión | Opciones de `navigator.credentials.create` + `challenge_id` |
| `POST /register/verify` | sesión | Guarda la passkey; códigos de recuperación si fue el alta |
| `GET /` · `PATCH /{id}` · `DELETE /{id}` | sesión | Lista, renombra, quita |
| `POST /login/options` | público | Opciones de `navigator.credentials.get`. Con `temp_token` (segundo paso) sólo las passkeys de esa cuenta |
| `POST /login/verify` | público | **Devuelve tokens** (`mfa: true`) |

`login/verify` devuelve tokens: el proxy `/api/v1` de la web lo veta y sólo lo llama su puente de
sesión (`/api/session/passkey`), como a `/auth/mfa/verify`.

## Reglas

- **Reto de un solo uso.** Vive 5 minutos en `webauthn_challenges` y se reclama con un
  `UPDATE … RETURNING` que se confirma **antes** de verificar: una respuesta rechazada no deja el
  reto reutilizable.
- **Añadir o quitar** exige un inicio de sesión de hace menos de 15 minutos y, si la cuenta tiene
  segundo factor, una sesión nacida de él (403 con `detail.code = "passkey_reauth_required"` y
  `X-Reauth-Required: 1`). Una sesión robada no puede colgarse una llave propia.
- **Añadir la aplicación de autenticación** (`/auth/mfa/setup`, `/auth/mfa/verify-setup`) a una
  cuenta obligada que ya cumple con una passkey sigue esa misma regla. Para una cuenta sin ningún
  segundo factor el alta de TOTP no cambia.
- **La última passkey** de una cuenta obligada sin TOTP no se puede quitar (409).
- **Máximo 10** por cuenta.
- **Contador de firmas**: si retrocede, se rechaza (posible clon). Las passkeys sincronizadas
  reportan 0 siempre y no entran en esa regla.
- **Sin bloqueo por cuenta.** No hay secreto que adivinar: contar fallos por cuenta sólo daría una
  forma de bloquear a otra persona. Los límites son por IP (`RATE_LIMIT_LOGIN`), el reto de un
  solo uso y la auditoría.
- **Códigos de recuperación** (`/auth/mfa/recovery-codes`): una cuenta con aplicación de
  autenticación sigue mandando su código TOTP; una que sólo tiene passkey los regenera desde una
  sesión abierta con la passkey hace menos de 15 minutos.
- **Correo** al titular cuando se añade o se quita una passkey.

## Auditoría (`audit_log`)

`PASSKEY_REGISTER`, `PASSKEY_REGISTER_FAILED`, `PASSKEY_RENAME`, `PASSKEY_DELETE`,
`PASSKEY_LOGIN_FAILED` y `MFA_ENABLE` con `metadata.method = "passkey"`. Los fallos guardan el
motivo (`origin`, `rp_id`, `signature`, `counter`, `user_verification`, `challenge`,
`challenge_mismatch`, `unknown_credential`, `other_account`, `user_handle`, `malformed`) y, si la credencial era conocida, de quién es;
nunca la credencial.

## Si se pierde el dispositivo

| Situación | Salida |
| --- | --- |
| Passkey sincronizada (iCloud / Google) | Vuelve sola al iniciar sesión en el teléfono nuevo con la misma cuenta de Apple o Google |
| Cuenta sin dos pasos | La contraseña sigue funcionando |
| Cuenta con dos pasos | Código de recuperación en `/auth/mfa/verify`; luego quitar la passkey perdida, registrar otra y regenerar códigos |
| `MASTER_GC` sin passkey, sin TOTP y sin códigos | `POST /users/{id}/mfa-reset` por otro `MASTER_GC` (borra también las passkeys). Si es el único: `migrations/reset_mfa.py` |

Restablecer la contraseña no quita passkeys ni dos pasos. Apagar el TOTP propio
(`/auth/mfa/disable`) conserva las passkeys. El reset entre pares borra las passkeys aunque la
función esté apagada en ese momento.

Una cuenta que recibe el rol `MASTER_GC` teniendo ya una passkey no recibe códigos de recuperación
en ese momento (nadie se los puede enseñar): debe generarlos ella misma en su perfil
(«Generar códigos de recuperación nuevos»).

## Nodo del evento

En el nodo **no hay passkeys**: el paquete no exporta `webauthn_credentials` (ni los secretos TOTP;
`nodo_sync_core.gather_export`), y `nodo-evento/docker-compose.yml` deja las dos variables vacías.
Se entra con la contraseña, como hasta ahora. Una passkey no sustituye saberse la contraseña:
quien vaya a operar en un nodo debe conocerla.

La app de Mac «Nodo Camporee» inicia sesión contra la nube con contraseña + código TOTP o código
de recuperación; no sabe usar passkeys. **Quien opere nodos con una cuenta `MASTER_GC` debe
conservar la aplicación de autenticación (TOTP) además de la passkey.** Con sólo passkey, cada
inicio de sesión en la app de Mac gastaría uno de los diez códigos de recuperación: en cuanto esa
cuenta tiene una passkey, la contraseña sola deja de abrir sesión, también con
`MASTER_MFA_ENFORCED` apagado.

## Desarrollo y pruebas

```sh
WEBAUTHN_RP_ID=localhost WEBAUTHN_ORIGINS=http://localhost:3000 uvicorn app.main:app
```

`tests/soft_authenticator.py` es un autenticador en software (clave P-256, attestation `none`)
que responde las ceremonias como un teléfono y permite mentir en cada dato: origen, RP ID,
verificación del usuario, contador.
