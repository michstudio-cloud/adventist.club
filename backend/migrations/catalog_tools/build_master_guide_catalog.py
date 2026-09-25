"""Build `data/master_guide_catalog.json`: the certifications of Guías Mayores (024).

    python migrations/catalog_tools/build_master_guide_catalog.py          # rewrites the JSON
    python migrations/catalog_tools/build_master_guide_catalog.py --check  # exit 1 if it differs

Nothing is downloaded here: the content was read on mundoja.org (Mundo J.A, voluntarios, DIA) on
2026-09-24 and is summarised in `docs/GUIAS_MAYORES_CATALOGO.md`.

Three families (programs.kind, 024):
  * TRAINING — the five EMC certifications (Entrenamiento para el Ministerio de Clubes / Club
    Ministry Training) of https://mundoja.org/clubes/guias-mayores/emc. The workshop names and
    codes are copied as they are (they are identifiers); the general requirements (serve in the
    role, mentor + field work, portfolio) are our own summary of the page's description.
  * MASTERY — ONE example. mundoja lists 17 «maestrías» (https://mundoja.org/maestrias,
    …/1390-maestrias-2026) as PATHFINDER HONORS of the category «Maestrías», which our honors
    catalogue already has: whether a maestría certificate is that honor or a program is an owner
    decision. The example follows «Maestría en Naturaleza» (…/1041-maestria-en-naturaleza).
  * MEDALLION — ONE example: no source lists the medallions (mundoja's Guías Mayores pages do not).
Examples are marked `example: true`, carry «(ejemplo, sustituir)» in their name and are never
published by the importer.
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent.parent / "data" / "master_guide_catalog.json"

EMC_URL = "https://mundoja.org/clubes/guias-mayores/emc"
NATURE_URL = "https://mundoja.org/component/content/article/1041-maestria-en-naturaleza"
GM_URL = "https://mundoja.org/clubes/guias-mayores"

# (slug, name, role, [(workshop, code)…]) — mundoja.org/clubes/guias-mayores/emc, 2026-09-24.
EMC = [
    ("emc-capacitacion-personal-conquistadores", "Capacitación del personal de Conquistadores",
     "cualquier cargo de la directiva del club (entrenamiento básico)", [
         ("Ministerio del Club: Propósito e Historia", "PFAD 001"),
         ("Organización de clubes", "PFAD 002"),
         ("Programación y Planificación", "PFAD 003"),
         ("Alcance del club", "PFAD 004"),
         ("Ceremonias y Simulacro", "PFAD 005"),
         ("Crecimiento del desarrollo", "PYSO 104"),
         ("Introducción a la Enseñanza", "EDUC 001"),
         ("Cuestiones médicas, de gestión de riesgos y de seguridad infantil", "MEDI 100"),
     ]),
    ("emc-consejero-conquistadores", "Consejero de Conquistadores", "consejero de unidad", [
        ("Alcance del Club de Conquistadores", "PFAD 004"),
        ("La Organización del Club de Conquistadores: El equipo de apoyo del consejero", "PFAD 100"),
        ("Las Responsabilidades del Consejero", "PFAD 101"),
        ("Crecimiento del desarrollo", "PYSO 104"),
        ("Discipulado y Disciplina", "PYSO 121"),
        ("La relación del consejero con el Conquistador", "PYSO 124"),
        ("Seguridad y el Consejero", "RCSF 120"),
        ("Aplicaciones Espirituales en la Naturaleza", "NAOS 120"),
    ]),
    ("emc-instructor-conquistadores", "Instructor del Club de Conquistadores", "instructor del club", [
        ("Estilos", "EDUC 002"),
        ("Comprender el aprendizaje, Estilos", "EDUC 003"),
        ("Trabajando con Niños con Necesidades Especiales", "EDUC 006"),
        ("Enseñanza cristiana, Valores", "EDUC 150"),
        ("Logro de Investidura Docente: Intención y Organización", "EDUC 200"),
        ("Aplicaciones prácticas para la realización de la investidura docente", "EDUC 210"),
        ("Enseñanza de honores", "EDUC 230"),
        ("Introducción a la Disciplina", "PYSO 120"),
    ]),
    ("emc-secretario-tesorero-conquistadores", "Secretario / Tesorero del Club de Conquistadores",
     "secretario o tesorero del club", [
         ("Informes, Registros y Sistemas de Mérito", "PFAD 140"),
         ("Desarrollo del Calendario Anual", "PFAD 141"),
         ("Formularios: Salud y Médico, Permiso, Voluntario y Conductor de Vehículo", "PFAD 142"),
         ("Introducción a la elaboración de presupuestos", "FINA 101"),
         ("Finanzas del club", "FINA 100"),
         ("Introducción a la recaudación de fondos", "FINA 110"),
         ("Comunicación Práctica", "CMME 104"),
     ]),
    ("emc-director-conquistadores", "Director del Club de Conquistadores", "director o subdirector del club", [
        ("Introducción a las Habilidades de Liderazgo", "LEAD 001"),
        ("La Asociación y la Junta de Iglesia local", "LEAD 002"),
        ("Introducción al personal de reclutamiento, selección y capacitación", "LEAD 150"),
        ("Encuesta de Camping y Planificación del campamento", "WILD 101"),
        ("Investidura Docente, Logro", "EDUC 200"),
        ("Finanzas del club", "FINA 100"),
        ("Introducción a la Disciplina", "PYSO 120"),
        ("Trabajar y comunicarse con los padres", "PYSO 207"),
    ]),
]

EMC_DESCRIPTION = (
    "Certificación del Entrenamiento para el Ministerio de Clubes (EMC). No es una investidura: se "
    "obtiene sirviendo en el cargo, asistiendo a los talleres, trabajando con un mentor, haciendo "
    "trabajo de campo y preparando un portafolio con las evidencias. Pensada para completarse en un año "
    "(salvo el entrenamiento básico). Reemplaza a Guía Mayor Avanzado y Guía Mayor Instructor."
)


def _requirement(position: int, label: str, text: str, url: str, evidence: bool = True) -> dict:
    return {"position": position, "label": label, "kind": "TEXT", "text": {"es": text},
            "source_url": {"es": url}, "evidence_required": evidence}


def emc_program(order: int, slug: str, name: str, role: str, workshops: list[tuple[str, str]]) -> dict:
    position = 0

    def nxt() -> int:
        nonlocal position
        position += 1
        return position

    sections = [
        {"slug": "servicio", "names": {"es": "Servicio en el cargo"}, "requirements": [
            _requirement(nxt(), "1", f"Servir en el liderazgo de clubes de la iglesia local o de la Asociación/Misión"
                                    f" como {role} mientras dura la certificación.", EMC_URL),
        ]},
        {"slug": "talleres", "names": {"es": "Talleres"}, "requirements": [
            _requirement(nxt(), code, f"Asistir al taller «{workshop}» ({code}).", EMC_URL)
            for workshop, code in workshops
        ]},
        {"slug": "mentoria-y-campo", "names": {"es": "Mentoría y trabajo de campo"}, "requirements": [
            _requirement(nxt(), "M1", "Trabajar con un mentor durante la certificación.", EMC_URL),
            _requirement(nxt(), "M2", "Hacer el trabajo de campo que piden los talleres.", EMC_URL),
        ]},
        {"slug": "portafolio", "names": {"es": "Portafolio"}, "requirements": [
            _requirement(nxt(), "P1", "Preparar un portafolio con los documentos y evidencias del cumplimiento de"
                                      " cada requisito.", EMC_URL),
        ]},
    ]
    return {
        "kind": "TRAINING", "ministry": "master-guides", "slug": slug, "code": f"EMC-{order}",
        "names": {"es": name}, "description": {"es": EMC_DESCRIPTION}, "sort_order": order,
        "authority": "IAD", "issuer_level": "ASSOCIATION", "family": "emc",
        "source_url": {"es": EMC_URL}, "sections": sections,
    }


def examples() -> list[dict]:
    mastery = {
        "kind": "MASTERY", "ministry": "master-guides", "slug": "ejemplo-maestria-naturaleza", "code": "EJ-MAESTRIA",
        "names": {"es": "Maestría en Naturaleza (ejemplo, sustituir)"},
        "description": {"es": "Ejemplo, sustituir. Muestra cómo se vería el certificado de una maestría como"
                              " programa. mundoja lista 17 maestrías como especialidades de Conquistadores"
                              " (categoría «Maestrías»): decidir si el certificado es esa especialidad o un"
                              " programa de Guías Mayores."},
        "sort_order": 100, "authority": "IAD", "issuer_level": "ASSOCIATION", "example": True,
        "source_url": {"es": NATURE_URL},
        "sections": [{"slug": "especialidades", "names": {"es": "Especialidades"}, "requirements": [
            _requirement(1, "1", "Desarrollar por lo menos 4 especialidades del grupo «flora».", NATURE_URL),
            _requirement(2, "2", "Desarrollar por lo menos 2 especialidades del grupo «fauna».", NATURE_URL),
            _requirement(3, "3", "Desarrollar por lo menos 1 especialidad del grupo «fauna doméstica».", NATURE_URL),
        ]}],
    }
    medallion = {
        "kind": "MEDALLION", "ministry": "master-guides", "slug": "ejemplo-medallon", "code": "EJ-MEDALLON",
        "names": {"es": "Medallón (ejemplo, sustituir)"},
        "description": {"es": "Ejemplo, sustituir. No hay todavía una fuente con la lista de medallones ni sus"
                              " requisitos: esta ficha sólo reserva la estructura."},
        "sort_order": 200, "authority": "IAD", "issuer_level": "ASSOCIATION", "example": True,
        "source_url": {"es": GM_URL},
        "sections": [{"slug": "requisitos", "names": {"es": "Requisitos"}, "requirements": [
            _requirement(1, "1", "Ejemplo, sustituir por los requisitos oficiales del medallón.", GM_URL, evidence=False),
        ]}],
    }
    return [mastery, medallion]


def build() -> dict:
    return {
        "author": "Mundo J.A (voluntarios)",
        "license": "© GC Youth Ministries, permiso pendiente",
        "retrieved": "2026-09-24",
        "programs": [emc_program(order, *row) for order, row in enumerate(EMC, start=1)] + examples(),
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


def main() -> None:
    text = render()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text(encoding="utf-8") == text else 1)
    OUT.write_text(text, encoding="utf-8")
    print(f"{OUT} ({len(build()['programs'])} programas)")


if __name__ == "__main__":
    main()
