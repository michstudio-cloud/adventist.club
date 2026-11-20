# Certificado HTTPS del nodo: diseño a largo plazo (propuesta, NO construido)

Estado: propuesta para decidir (2026-11-19). Lo construido hoy está en la migración 032 y en
`backend/app/services/node_certs.py`.

## 1. El problema

Hasta 030/031, «Preparar» metía en el paquete de CUALQUIER nodo el certificado real de Let's Encrypt
**y su llave privada** para `events/api/admin/media.adventist.club`, el mismo par para todos los nodos y
reutilizado hasta que le quedaran 30 días. La Mac lo guarda sin cifrar (`tls/actual/privkey.pem`, 600).

Con un solo camporee y la Mac de MICH era aceptable. Si el nodo se regala a otras asociaciones, quien
tenga esa llave puede **hacerse pasar por adventist.club** (la plataforma entera, no sólo su evento) en
cualquier red cuyo DNS controle, durante toda la vigencia del certificado (≈ 60–90 días). Revocarlo
obliga a sacar otro para todos y los navegadores no siempre consultan la revocación.

## 2. Lo que hace la 032 (medida inmediata)

- Por defecto el paquete dice `certificate.status = "ca_local"` y **no lleva llave**: el nodo usa su CA
  local (plan B). La llave real sólo viaja si un MASTER_GC encendió `events.offline_node_real_cert`
  para ESE evento (`PUT /api/v1/events/{id}/offline/settings`).
- El nodo borra el certificado real que le haya quedado de otro evento al preparar uno que usa la CA
  local, al subir su final, y al apagarse después del último día del evento (`EVENTO_FIN` = `ends_on`).

Costo: con la CA local cada teléfono debe instalar y confiar en el perfil de la CA (fricción alta con
100 teléfonos). Por eso esto es un puente, no el destino.

## 3. Diseño propuesto: un nombre por evento, llave generada en la Mac

### 3.1 Nombres

- Un nombre propio por nodo, bajo un dominio **separado** de la plataforma, p. ej.
  `<código>.nodo-adventist.club` (o `<código>.nodo.adventist.club` si no se quiere otro dominio).
  `<código>` = 6–8 caracteres aleatorios que la API asigna al nodo al prepararlo (no el nombre del
  evento: no filtra datos y no se adivina).
- Recomiendo **otro dominio registrado**: los límites de Let's Encrypt (50 certificados por dominio
  registrado por semana) no compiten con los de adventist.club, una cookie o un service worker del nodo
  nunca alcanza a la plataforma, y un problema con un nodo no toca la reputación del dominio principal.
- El DNS del evento (router o la Mac) resuelve ese nombre a la IP de la Mac. En internet el nombre no
  necesita apuntar a nada (sólo el TXT temporal de la validación DNS-01).

### 3.2 La llave nunca sale de la Mac (CSR)

1. Al «Preparar», la app genera una llave P-256 en la Mac (archivo 600 en Application Support; Caddy
   necesita leerla, así que no puede vivir sólo en el Secure Enclave) y un CSR con el nombre asignado.
2. La Mac manda el CSR a la API: `POST /api/v1/events/{id}/offline/nodes/{node_id}/certificate`, con la
   sesión de coordinación y **firmado con la llave Ed25519 del nodo** (la misma que firma las subidas).
3. La API comprueba: evento habilitado para nodos, nodo abierto, el CSR pide sólo el nombre de ESE nodo,
   llave P-256 (o RSA ≥ 2048), firma del nodo válida. Pide el certificado a Let's Encrypt por DNS-01
   (la API ya tiene el token de Cloudflare: `node_certs.py`) **con el CSR de la Mac** y devuelve sólo la
   cadena pública. La API nunca ve la llave; no hay nada que cachear ni cifrar en `node_tls_certs`.
4. Se registra en la bitácora (EVENT_OFFLINE_NODE_CERT) con la huella del certificado.

### 3.3 Certificados cortos

- Perfil de vida corta de Let's Encrypt (≈ 6 días) o, si no está disponible, el normal de 90 días
  revocado al cerrar el nodo.
- La Mac lo pide el día anterior al evento (con internet) y lo renueva sola cada vez que tenga internet
  y le queden < 3 días. Un evento de 3–4 días sin internet queda cubierto con uno pedido la víspera.
- Al subir el final o al cerrar el nodo (`close_node`), la API revoca el certificado (ACME `revokeCert`)
  y el nodo borra su llave.

### 3.4 Qué cambia en el resto

- **Web del nodo**: la app web debe servirse bajo el nombre del nodo y hablar con su API en el mismo
  origen (o en `api-<código>…`); hoy está fijada a `events.adventist.club`/`api.adventist.club`.
- **Pases e invitaciones** que se escanean en el evento: el QR debe llevar el nombre del nodo o una ruta
  que el nodo resuelva; los pases ya impresos con `events.adventist.club` necesitan una redirección
  (el DNS del evento puede seguir respondiendo `events.adventist.club` sólo para redirigir… lo que
  vuelve a pedir un certificado de ese nombre: hay que decidir si se reimprimen o si se usa la CA local
  sólo para esa redirección).
- **Passkeys (WebAuthn)**: un origen bajo `adventist.club` puede usar `rpId = adventist.club`; bajo otro
  dominio registrado no, así que en el nodo se entra con contraseña (como hoy, sin segundo factor).
- **Cookies / sesión**: origen propio del nodo; nada de la plataforma se comparte.

## 4. Decisiones pendientes (MICH)

1. ¿Dominio separado (`nodo-adventist.club` u otro) o subdominio de `adventist.club`?
2. ¿Qué hacer con los QR ya impresos que apuntan a `events.adventist.club`?
3. ¿Perfil de vida corta o 90 días + revocación?
4. Mientras tanto: ¿qué eventos reciben el certificado real (`offline_node_real_cert`)? Recomendado:
   sólo los operados por MICH en su propia Mac.
