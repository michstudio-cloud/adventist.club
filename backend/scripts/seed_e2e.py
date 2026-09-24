"""Datos mínimos para la batería E2E del frontend (`scripts/e2e/run.mjs` en conquistadores-app).

    cd backend
    DATABASE_URL=postgresql://test@127.0.0.1:55432/etl_e2e python scripts/seed_e2e.py

Idempotente: cada fila lleva un id derivado de una clave fija (uuid5), así que correrlo dos veces
no duplica nada y deja los valores como aquí se describen. Sólo escribe en una base LOCAL: se niega
a correr contra cualquier otra (la misma regla que `tests/conftest.py`).

Siembra lo que el API no deja crear desde fuera:
  * ministerios (pathfinders, adventurers, master-guides);
  * el árbol división → unión → asociación → zona → iglesia (más una segunda asociación, ajena);
  * una categoría y tres especialidades PUBLICADAS con requisitos en español (una con un requisito
    práctico);
  * la clase «Amigo» PUBLICADA, con dos secciones y tres requisitos;
  * cuentas con contraseña conocida: MASTER_GC, ADMIN_ASSOCIATION (de la asociación y de la ajena)
    y COORDINATOR_ZONE. El resto de personas las crea cada recorrido por el API.
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, connect_args_for, engine, normalize_database_url  # noqa: E402
from app.security import hash_password_sync  # noqa: E402

NAMESPACE = uuid.UUID("5f0c2a4e-3c1b-4e0a-9d3e-e2e000000000")
PASSWORD = os.environ.get("E2E_PASSWORD", "E2e-Passw0rd")
DOMAIN = "e2e.example.com"


def sid(key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, key)


MINISTRIES = [("pathfinders", "Pathfinders"), ("adventurers", "Adventurers"), ("master-guides", "Master Guides")]

# (clave, tipo, nombre, código, clave del padre)
ORGS = [
    ("division", "division", "División E2E Interamericana", "E2EDIV", None),
    ("union", "union", "Unión E2E Mexicana del Norte", "E2EUN", "division"),
    ("association", "association", "Asociación E2E del Noreste", "E2EASOC", "union"),
    ("zone", "zone", "Zona E2E Uno", None, "association"),
    ("church", "church", "Iglesia E2E Central", None, "zone"),
    ("association-b", "association", "Asociación E2E Ajena", "E2EASOCB", "union"),
]

# (clave, rol, nombre, organización)
USERS = [
    ("master", "MASTER_GC", "E2E Master", None),
    ("asoc", "ADMIN_ASSOCIATION", "E2E Administración Asociación", "association"),
    ("asoc-b", "ADMIN_ASSOCIATION", "E2E Administración Ajena", "association-b"),
    ("zona", "COORDINATOR_ZONE", "E2E Coordinación Zona", "zone"),
]

# (clave, código, nombre, [(texto, teórico)])
HONORS = [
    ("nudos", "E2E-001", "E2E Nudos", [
        ("Explica la diferencia entre un nudo y una vuelta.", True),
        ("Nombra cinco usos del nudo llano.", True),
        ("Ata frente a tu instructor el as de guía, el ballestrinque y el nudo llano.", False),
    ]),
    ("astronomia", "E2E-002", "E2E Astronomía", [
        ("¿Qué es una estrella?", True),
        ("Nombra los planetas del sistema solar en orden.", True),
        ("¿Qué es un año luz?", True),
    ]),
    ("aves", "E2E-003", "E2E Aves", [
        ("Nombra diez aves de tu región.", True),
        ("Explica qué es la migración.", True),
    ]),
]

# (sección, [etiqueta, texto])
AMIGO = [
    ("generales", "Generales", [("1", "Tener como mínimo 10 años."), ("2", "Memorizar el voto y la ley del Conquistador.")]),
    ("descubrimiento", "Descubrimiento espiritual", [("3", "Memorizar los libros del Antiguo Testamento.")]),
]


def _require_local() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url or connect_args_for(normalize_database_url(url)) != {}:
        sys.exit("seed_e2e: DATABASE_URL debe apuntar a una base local (127.0.0.1 / localhost).")


async def seed() -> dict:
    password_hash = hash_password_sync(PASSWORD)
    async with SessionLocal() as db:
        run = lambda sql, **p: db.execute(text(sql), p)  # noqa: E731

        ministry_ids = {}
        for slug, name in MINISTRIES:
            await run(
                "INSERT INTO ministries (id, slug, name, status) VALUES (:id, :slug, :name, 'active')"
                " ON CONFLICT (slug) DO NOTHING",
                id=sid(f"ministry:{slug}"), slug=slug, name=name,
            )
            ministry_ids[slug] = (await run("SELECT id FROM ministries WHERE slug = :s", s=slug)).scalar_one()
        pathfinders = ministry_ids["pathfinders"]

        paths: dict[str, str] = {}
        for key, org_type, name, code, parent in ORGS:
            org_id = sid(f"org:{key}")
            path = f"{paths[parent]}.{org_id.hex}" if parent else org_id.hex
            paths[key] = path
            await run(
                "INSERT INTO organizations (id, parent_id, type, name, code, status, path, city, country)"
                " VALUES (:id, :parent, :type, :name, :code, 'active', text2ltree(:path), 'Monterrey', 'MX')"
                " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = 'active',"
                " parent_id = EXCLUDED.parent_id, path = EXCLUDED.path",
                id=org_id, parent=sid(f"org:{parent}") if parent else None, type=org_type,
                name=name, code=code, path=path,
            )

        for key, role, name, org in USERS:
            await run(
                "INSERT INTO users (id, email, password_hash, name, role, organization_id, is_minor,"
                " status, verification_status, child_protection_completed)"
                " VALUES (:id, :email, :hash, :name, :role, :org, false, 'ACTIVE', 'VERIFIED', true)"
                " ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role,"
                " organization_id = EXCLUDED.organization_id, status = 'ACTIVE',"
                " verification_status = 'VERIFIED', mfa_enabled = false, mfa_secret = NULL",
                id=sid(f"user:{key}"), email=f"{key}@{DOMAIN}", hash=password_hash, name=name,
                role=role, org=sid(f"org:{org}") if org else None,
            )

        category_id = sid("category:e2e")
        await run(
            "INSERT INTO honor_categories (id, ministry_id, name, slug) VALUES (:id, :m, 'E2E Artes y Habilidades', 'e2e-artes')"
            " ON CONFLICT (id) DO NOTHING",
            id=category_id, m=pathfinders,
        )
        for key, code, name, requirements in HONORS:
            honor_id = sid(f"honor:{key}")
            await run(
                "INSERT INTO honors (id, ministry_id, category_id, name, slug, code, active, status,"
                " published_at, description, skill_level)"
                " VALUES (:id, :m, :c, :name, :slug, :code, true, 'PUBLISHED', now(), :desc, 1)"
                " ON CONFLICT (id) DO UPDATE SET status = 'PUBLISHED', active = true",
                id=honor_id, m=pathfinders, c=category_id, name=name, slug=f"e2e-{key}", code=code,
                desc=f"Especialidad de prueba {name}.",
            )
            for position, (description, theoretical) in enumerate(requirements, start=1):
                await run(
                    "INSERT INTO honor_requirements (id, honor_id, position, description, is_theoretical, locale)"
                    " VALUES (:id, :h, :pos, :d, :t, 'es')"
                    " ON CONFLICT (id) DO UPDATE SET description = EXCLUDED.description,"
                    " is_theoretical = EXCLUDED.is_theoretical",
                    id=sid(f"req:{key}:{position}"), h=honor_id, pos=position, d=description, t=theoretical,
                )

        program_id = sid("program:amigo")
        await run(
            "INSERT INTO programs (id, ministry_id, kind, slug, code, name, description, sort_order, status,"
            " issuer_level, published_at)"
            " VALUES (:id, :m, 'CLASS', 'amigo', 'AMIGO', 'Amigo', 'Primera clase regular.', 1, 'PUBLISHED',"
            " 'CLUB', now())"
            " ON CONFLICT (id) DO UPDATE SET status = 'PUBLISHED'",
            id=program_id, m=pathfinders,
        )
        position = 0
        for section_position, (slug, section_name, items) in enumerate(AMIGO, start=1):
            section_id = sid(f"program:amigo:section:{slug}")
            await run(
                "INSERT INTO program_sections (id, program_id, position, slug, name)"
                " VALUES (:id, :p, :pos, :slug, :name) ON CONFLICT (id) DO NOTHING",
                id=section_id, p=program_id, pos=section_position, slug=slug, name=section_name,
            )
            for label, description in items:
                position += 1
                requirement_id = sid(f"program:amigo:req:{position}")
                await run(
                    "INSERT INTO program_requirements (id, program_id, section_id, position, label, kind)"
                    " VALUES (:id, :p, :s, :pos, :label, 'FREE') ON CONFLICT (id) DO NOTHING",
                    id=requirement_id, p=program_id, s=section_id, pos=position, label=label,
                )
                await run(
                    "INSERT INTO program_requirement_texts (requirement_id, locale, description)"
                    " VALUES (:r, 'es', :d) ON CONFLICT (requirement_id, locale) DO NOTHING",
                    r=requirement_id, d=description,
                )
        await db.commit()
    await engine.dispose()
    return {
        "association_id": str(sid("org:association")),
        "zone_id": str(sid("org:zone")),
        "church_id": str(sid("org:church")),
        "program_id": str(program_id),
        "honors": {key: str(sid(f"honor:{key}")) for key, *_ in HONORS},
    }


if __name__ == "__main__":
    _require_local()
    ids = asyncio.run(seed())
    print("seed_e2e: listo", ids)
