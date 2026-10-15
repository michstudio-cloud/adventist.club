# Adventist Club API

Backend global para varios ministerios y aplicaciones.

Stack: FastAPI + PostgreSQL/Neon + Render/Docker.

En Render sólo debes configurar el secreto `DATABASE_URL` con la conexión de Neon. No la guardes en GitHub.

Endpoints iniciales:
- GET /api/v1/health
- GET /api/v1/ministries
- GET /api/v1/applications
- GET /api/v1/honors?ministry=pathfinders
- POST /api/v1/certificates/prototype-batch
- GET /api/v1/certificates/verify/{certificate_no}
- POST /api/v1/printing/pdf

## Base de datos desde cero (pruebas, CI, nodo del evento)

Las migraciones (`migrations/*.sql`) sólo ALTERAN un esquema heredado que en producción ya existía;
por sí solas no construyen una base vacía. El arranque oficial lo hace con un solo comando:

```sh
backend/scripts/db_bootstrap.sh --create postgresql://usuario@127.0.0.1:5432/nueva_base
# --test-fixtures   además el árbol NTAM + especialidades que espera la suite de pruebas
# --dry-run         sólo muestra el plan
```

Orden: `db/base/*.sql` (tablas anteriores a 001: ministerios, organizaciones, especialidades,
certificados) → `migrations/001…` en orden de nombre (con `008f` movida tras `009`, de la que
depende) → `db/seeds/minimal.sql` (ministerios y categorías). Cada archivo va en su propia
transacción y queda anotado en `schema_migrations` (nombre + sha256): repetirlo sólo aplica lo
nuevo. Exige UTF8 (con `--create` crea la base `ENCODING 'UTF8' TEMPLATE template0`) y **se niega**
a tocar una base que ya tiene esquema sin `schema_migrations` (p. ej. producción) o un host de Neon.

Suite de pruebas sobre una base recién creada:

```sh
backend/scripts/db_bootstrap.sh --create --test-fixtures postgresql://test@127.0.0.1:55432/pruebas
cd backend && TZ=UTC TEST_DATABASE_URL=postgresql://test@127.0.0.1:55432/pruebas python -m pytest -q
```

Nodo local del evento (sin internet): ver `../nodo-evento/README.md`.
