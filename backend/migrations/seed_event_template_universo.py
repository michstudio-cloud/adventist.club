"""Precarga el evento «Camporee Familiar de Aventureros 2026 — Universo de Dios» (spec
docs/superpowers/specs/2026-09-24-eventos.md §3.4) como plantilla editable de la asociación.

    pip install "psycopg[binary]"
    DATABASE_URL=... python seed_event_template_universo.py --association NTAM            # simulacro (ROLLBACK)
    DATABASE_URL=... python seed_event_template_universo.py --association NTAM --commit   # escribe

Sin DATABASE_URL sólo valida la plantilla con el motor de puntajes e imprime el plan.

Fuente: boletín de la Asociación Norte de Tamaulipas (15 págs.), usado como DATOS de referencia.
Reglas:
  * el evento entra en DRAFT, ministerio `adventurers`, dueño = la asociación de `--association`
    (por `organizations.code`); 20–22 nov 2026, La Morita, N.L.;
  * cada actividad se valida con app/services/event_scoring.py: las READY deben estar completas
    y su máximo coincidir con su configuración; la suma base es 1 000 (p. 5);
  * lo que el boletín no cuantifica entra TO_DEFINE (las dos inspecciones y el evento previo) o
    con monto NULL (faltas de disciplina): la asociación lo completa en el editor;
  * idempotente por (asociación, slug): si el evento ya existe NO se toca — pudo haberse
    editado — y sólo se informa. Nunca crea inscripciones, personal ni puntajes.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.services import event_scoring as scoring  # noqa: E402  (pure module, no DB)

MINISTRY = "adventurers"
SEEDER = "seed_event_template_universo"
AUDIT_ACTION = "EVENT_SEED"
BASE_TOTAL = 1000

EVENT = {
    "name": "Camporee Familiar de Aventureros 2026 — Universo de Dios",
    "slug": "camporee-familiar-aventureros-2026-universo-de-dios",
    "venue": "La Morita",
    "city": "La Morita, N.L.",
    "starts_on": "2026-11-20",
    "ends_on": "2026-11-22",
    "source_note": (
        "Boletín «Camporee Familiar de Aventureros 2026 — Universo de Dios», Asociación Norte de "
        "Tamaulipas (15 págs.). Total base 1 000 puntos; extra posible 50 (p. 5). Precargado por "
        "seed_event_template_universo.py; lo marcado «por definir» lo completa la asociación."
    ),
    "honor_bands": [
        {"key": "primeros", "label": "Primeros honores", "min": 950, "max": None},
        {"key": "segundos", "label": "Segundos honores", "min": 800, "max": 949},
        {"key": "terceros", "label": "Terceros honores", "min": None, "max": 799},
    ],
}


def _criteria(*rows, step=None):
    out = []
    for key, label, maximum in rows:
        item = {"key": key, "label": label, "max": maximum}
        if step is not None:
            item["deduction_step"] = step
        out.append(item)
    return out


# (clave local, madre, nombre, kind, máx, config, estado, descripción)
ACTIVITIES = [
    ("senal", None, "Señal eterna", "participation", 150, {"max": 150}, "READY",
     "Revisión de fotos y videos (boletín p. 6)."),
    ("conexion", None, "Conexión con Dios", "group", 200, {}, "READY",
     "Ronda general de aventureros, de padres y directiva, y final (pp. 7–8)."),
    ("conexion_av", "conexion", "Ronda general aventureros", "bands", 100,
     {"bands": [{"min": 22, "max": 26, "points": 100}, {"min": 17, "max": 21, "points": 95},
                {"min": 12, "max": 16, "points": 90}, {"min": 0, "max": 11, "points": 85}]},
     "READY", "Aciertos → puntos por banda (p. 7)."),
    ("conexion_pd", "conexion", "Ronda general padres y directiva", "bands", 80,
     {"bands": [{"min": 16, "max": 18, "points": 80}, {"min": 13, "max": 15, "points": 75},
                {"min": 10, "max": 12, "points": 70}, {"min": 7, "max": 9, "points": 65},
                {"min": 0, "max": 6, "points": 60}]},
     "READY", "Aciertos → puntos por banda (p. 7)."),
    ("conexion_final", "conexion", "Ronda final", "per_correct", 20,
     {"points_each": 2, "max_items": 10, "finalists_only": True}, "READY",
     "5 clubes finalistas; empates a discreción de la dirección del evento (p. 8). "
     "No clasificar vale 0 en esta ronda, no en toda la actividad."),
    ("esencia", None, "Conociendo mi esencia", "rubric", 200,
     {"criteria": _criteria(("tiempo", "Tiempo", 40), ("participacion", "Participación", 20),
                            ("mensaje_final", "Mensaje final", 40),
                            ("creatividad_titeres", "Creatividad de títeres", 50), step=2)
      + _criteria(("presentacion_iglesia", "Presentación previa en la iglesia", 50))},
     "READY", "Descuentos de 2 puntos por incidencia en todos los criterios menos la presentación previa (p. 9)."),
    ("mision", None, "Misión Galáctica", "stations", 200,
     {"stations": [
         {"key": "plataforma", "label": "Plataforma", "points": 50},
         {"key": "reparacion", "label": "Reparación", "points": 30},
         {"key": "campo_base", "label": "Campo Base", "points": 30},
         {"key": "gravedad_cero", "label": "Gravedad Cero", "points": 20},
         {"key": "asteroides", "label": "Asteroides", "points": 30},
         {"key": "nebulosas", "label": "Nebulosas", "points": 20},
         {"key": "explorando", "label": "Explorando", "points": 20},
         {"key": "centro_mando", "label": "Centro de Mando (pin)", "points": 0},
     ]}, "READY", "El juez marca la estación completada por el club (pp. 10–12)."),
    ("marcha", None, "Marcha a las estrellas", "rubric", 100,
     {"criteria": _criteria(("coordinacion", "Coordinación", 30), ("voz_mando", "Voz de mando", 20),
                            ("uniformidad", "Uniformidad", 20), ("tiempo", "Tiempo", 30), step=2)},
     "READY", "Descuentos de 2 puntos por incidencia (p. 13)."),
    ("insp_sabado", None, "Inspección sábado", "rubric", 50,
     {"criteria": _criteria(("uniforme_gala", "Portar correctamente el uniforme de gala", None),
                            ("versiculo", "Versículo del devocional memorizado", None),
                            ("disciplina", "Mantener la disciplina", None),
                            ("carpas_limpias", "Área de carpas limpia", None),
                            ("cocina_limpia", "Cocina limpia y con lugar para basura", None))},
     "TO_DEFINE", "El boletín no reparte los 50 puntos entre criterios (p. 4): por definir."),
    ("insp_domingo", None, "Inspección domingo", "rubric", 50,
     {"criteria": _criteria(("versiculo", "Versículo del devocional memorizado", None),
                            ("carpas_limpias", "Área de carpas limpia", None),
                            ("cocina_limpia", "Cocina limpia y con lugar para basura", None))},
     "TO_DEFINE", "El boletín no reparte los 50 puntos entre criterios (p. 4): por definir."),
    ("previo", None, "Evento previo", "participation", 50, {"max": 50}, "TO_DEFINE",
     "Sin rúbrica en el boletín (p. 5): por definir."),
]

DISCIPLINE = [
    "Sin brazalete de campamento",
    "No respetar el toque de queda",
    "Llegar tarde a las reuniones en el auditorio",
    "Aventureros sin la supervisión de sus consejeros",
    "Ausencia de directivos en juntas o comisiones",
    "Desorden en las áreas asignadas",
    "Falta de respeto a los jueces",
    "Otros criterios que determinen los jueces",
]

# Decisión del propietario (2026-09-24): el modo de monto se elige por tipo. FIXED = monto del
# tipo aplicado tal cual (None = por definir, bloquea aplicarlo); FREE = monto por ajuste.
# «Otros criterios que determinen los jueces» es FREE; las demás faltas, FIXED por definir.
FREE_DISCIPLINE = {"Otros criterios que determinen los jueces"}

# (kind, etiqueta, modo, puntos, máx. puntos (FREE), máx. por evento, máx. por club)
ADJUSTMENT_TYPES = [
    ("BONUS", "Ganador de la final de Conexión con Dios", "FIXED", 50, None, 1, None),
    ("PENALTY", "Área de acampar sucia al retirarse", "FIXED", 50, None, None, 1),
] + [("PENALTY", f"Disciplina: {label}", "FREE" if label in FREE_DISCIPLINE else "FIXED",
      None, None, None, None) for label in DISCIPLINE]


class SeedError(Exception):
    """Loud failure: nothing is written when the template does not make sense."""


def build() -> dict:
    """Validates every activity with the scoring engine; returns the normalized plan."""
    activities, base = [], 0
    for key, parent, name, kind, maximum, config, status, description in ACTIVITIES:
        try:
            clean = scoring.validate_config(kind, config, draft=status == "TO_DEFINE")
        except scoring.ScoringError as exc:
            raise SeedError(f"{name}: {exc}") from None
        if status == "READY" and kind != "group":
            allowed = scoring.config_max(kind, clean)
            if allowed != maximum:
                raise SeedError(f"{name}: máximo {maximum} ≠ configuración {allowed}")
        if parent is None:
            base += maximum
        activities.append({"key": key, "parent": parent, "name": name, "kind": kind,
                           "max_points": maximum, "config": clean, "status": status,
                           "description": description})
    children = {}
    for row in activities:
        if row["parent"]:
            children[row["parent"]] = children.get(row["parent"], 0) + row["max_points"]
    for row in activities:
        if row["kind"] == "group" and children.get(row["key"]) != row["max_points"]:
            raise SeedError(f"{row['name']}: las rondas suman {children.get(row['key'])}")
    for kind, label, mode, points, bound, _, _ in ADJUSTMENT_TYPES:
        if (mode == "FIXED" and bound is not None) or (mode == "FREE" and points is not None):
            raise SeedError(f"{label}: FIXED usa points; FREE usa max_points")
    if base != BASE_TOTAL:
        raise SeedError(f"La suma base es {base}, no {BASE_TOTAL}")
    return {"event": {**EVENT, "honor_bands": scoring.validate_honor_bands(EVENT["honor_bands"])},
            "activities": activities, "adjustment_types": ADJUSTMENT_TYPES}


def connect(url: str):
    import psycopg

    return psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))


def run(conn, plan: dict, *, association: str, operator: str | None = None) -> dict:
    """Apply the plan inside the caller's transaction (the caller commits or rolls back)."""
    from psycopg.types.json import Jsonb

    cur = conn.cursor()
    cur.execute("SELECT id, name FROM organizations WHERE code = %s AND type = 'association'",
                (association,))
    found = cur.fetchone()
    if found is None:
        raise SeedError(f"No existe una asociación con código {association!r}")
    org_id, org_name = found
    cur.execute("SELECT id FROM ministries WHERE slug = %s AND status = 'active'", (MINISTRY,))
    ministry = cur.fetchone()
    if ministry is None:
        raise SeedError(f"No existe el ministerio activo {MINISTRY!r}")
    event = plan["event"]
    cur.execute("SELECT id, status FROM events WHERE organization_id = %s AND slug = %s",
                (org_id, event["slug"]))
    existing = cur.fetchone()
    if existing is not None:
        return {"association": org_name, "event_id": str(existing[0]), "created": False,
                "status": existing[1],
                "note": "El evento ya existe: no se toca (pudo haberse editado)."}
    event_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO events (id, organization_id, ministry_id, name, slug, venue, city, starts_on,"
        " ends_on, status, rules_version, honor_bands, source_note)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'DRAFT', 1, %s, %s)",
        (event_id, org_id, ministry[0], event["name"], event["slug"], event["venue"], event["city"],
         event["starts_on"], event["ends_on"], Jsonb(event["honor_bands"]), event["source_note"]),
    )
    ids: dict[str, uuid.UUID] = {}
    positions: dict[str | None, int] = {}
    for row in plan["activities"]:
        positions[row["parent"]] = positions.get(row["parent"], 0) + 1
        ids[row["key"]] = uuid.uuid4()
        cur.execute(
            "INSERT INTO event_activities (id, event_id, parent_id, position, name, description,"
            " kind, max_points, config, status, counts_to_total)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)",
            (ids[row["key"]], event_id, ids[row["parent"]] if row["parent"] else None,
             positions[row["parent"]], row["name"], row["description"], row["kind"],
             row["max_points"], Jsonb(row["config"]), row["status"]),
        )
    for position, (kind, label, mode, points, bound, per_event, per_club) in enumerate(
        plan["adjustment_types"], 1
    ):
        cur.execute(
            "INSERT INTO event_adjustment_types (id, event_id, kind, label, amount_mode, points,"
            " max_points, max_per_event, max_per_club, position)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (uuid.uuid4(), event_id, kind, label, mode, points, bound, per_event, per_club, position),
        )
    cur.execute(
        "INSERT INTO audit_log (id, action, entity_type, entity_id, details, metadata_json)"
        " VALUES (%s, %s, 'EVENT', %s, %s, %s)",
        (uuid.uuid4(), AUDIT_ACTION, str(event_id), event["name"],
         Jsonb({"seeder": SEEDER, "operator": operator, "association": association})),
    )
    return {"association": org_name, "event_id": str(event_id), "created": True, "status": "DRAFT",
            "activities": len(plan["activities"]),
            "to_define": [r["name"] for r in plan["activities"] if r["status"] == "TO_DEFINE"],
            "adjustment_types": len(plan["adjustment_types"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--association", required=True, help="organizations.code de la asociación dueña")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="por defecto: todo en una transacción y ROLLBACK")
    mode.add_argument("--commit", action="store_true", help="escribe en DATABASE_URL")
    parser.add_argument("--operator", default=os.environ.get("USER"), help="quién precarga (audit_log)")
    args = parser.parse_args()
    try:
        plan = build()
    except SeedError as exc:
        sys.exit(f"ERROR: {exc}")
    for row in plan["activities"]:
        indent = "   " if row["parent"] else ""
        print(f"{indent}{row['name']} · {row['kind']} · {row['max_points']} · {row['status']}")
    print(f"Tipos de ajuste: {len(plan['adjustment_types'])}")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Sin DATABASE_URL: sólo se validó la plantilla.")
        return
    with connect(url) as conn:
        try:
            report = run(conn, plan, association=args.association, operator=args.operator)
        except SeedError as exc:
            conn.rollback()
            sys.exit(f"ERROR: {exc}")
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if not args.commit:
        print("SIMULACRO: nada se ha escrito (ROLLBACK). Repite con --commit.")


if __name__ == "__main__":
    main()
