"""Render honor sheets (worksheet «hoja» and/or compact «ficha») to disk, for the owner's review.

    cd backend
    DATABASE_URL=postgresql://... python scripts/render_honor_sheets.py --out DIR --ids <uuid|slug> ...
    DATABASE_URL=postgresql://... python scripts/render_honor_sheets.py --out DIR --all-published
    DATABASE_URL=postgresql://... python scripts/render_honor_sheets.py --out DIR --samples [--demo]

--modes hoja ficha (default: both) picks the layouts; every PDF is written as
<name>-<modo>.pdf with PNG previews of its first and last page next to it (pypdfium2;
skipped with a warning when it is not installed).

--samples picks three honors from the database: the published one with the most requirements
among those that also have resources, one whose requirements have sub-items («a)», «b)»…) and a
published one WITHOUT requirements. --demo replaces the sub-items sample with a temporary
«Alfarería» honor (invented but realistic Spanish text: sub-items on their own lines and
inline, practical requirements, rows marked as imported from guiasmayores.com and one resource
there, to check that none of it shows) that is inserted, rendered and DELETED again.

--patch-dir DIR draws the patch from DIR/<slug>.webp|png when present (the local copy of the
catalogue's patches) instead of fetching `image_url`. The database is only read, except for
the --demo honor.

--warm (OPTIONAL and MANUAL: no startup job or cron runs it; the API already stores each PDF
lazily on its first request) pre-stores the PDFs in the public R2 bucket with the same key and
upload the API uses (app/services/sheet_cache.py). Dry-run by default: it only counts, without
rendering or touching R2. --apply renders and uploads what is missing (needs the R2_* variables)
and deletes the older versions of each variant:

    DATABASE_URL=... python scripts/render_honor_sheets.py --warm --all-published [--modes hoja ficha]
    DATABASE_URL=... R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET_NAME=... \\
        python scripts/render_honor_sheets.py --warm --all-published --modes hoja --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/adventist")

from sqlalchemy import func, or_, select, text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import Honor, HonorRequirement, HonorResource  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import sheet_cache  # noqa: E402
from app.services.honor_sheet import load_sheet_data, render_sheet_pdf  # noqa: E402

DEMO_REQUIREMENTS = [
    ("Explicar qué es la arcilla, cómo se forma en la naturaleza y nombrar tres tipos de arcilla que se usan en "
     "alfarería.", True, None),
    ("Conocer el significado de los siguientes términos:\n  a) Barbotina\n  b) Engobe\n  c) Bizcocho\n"
     "  d) Esmalte\n  e) Cuero duro", True, None),
    ("Preparar la arcilla para trabajar, eliminando las burbujas de aire mediante el amasado. Explicar por qué "
     "el aire atrapado puede romper una pieza durante la cocción.", False,
     "El instructor verificará el amasado en persona."),
    ("Hacer a mano las siguientes piezas:\n  a) Una vasija con la técnica de pellizco\n  b) Una vasija con "
     "la técnica de rollos (churros) de al menos 10 cm de alto\n  c) Una pieza con la técnica de placas",
     False, "Las tres piezas deben secarse lentamente y presentarse cocidas."),
    ("Describir el proceso de cocción:\n  a) Qué ocurre con la arcilla durante la primera cocción\n  b) La "
     "diferencia entre un horno eléctrico, uno de leña y uno de gas\n  c) Las medidas de seguridad al cargar y "
     "descargar un horno", True, None),
    ("Mencionar tres usos de las vasijas de barro en tiempos bíblicos: a) Para guardar agua, aceite o grano "
     "b) Para cocinar y servir los alimentos c) Para conservar documentos, como los rollos del mar Muerto",
     True, None),
    ("Leer Jeremías 18:1-6 y explicar con tus propias palabras qué enseña la figura del alfarero y el barro "
     "sobre la relación de Dios con nosotros.", True, None),
]
DEMO_RESOURCES = [
    ("Técnicas básicas de modelado a mano", "https://www.youtube.com/watch?v=alfareria-demo", "video"),
    ("Guía de seguridad en el taller de cerámica", "https://media.adventist.club/resources/alfareria-seguridad.pdf",
     "pdf"),
    ("Glosario de cerámica y alfarería", "https://www.conquistadores.app/honors/alfareria", "link"),
    ("Requisitos (sitio externo)", "https://www.guiasmayores.com/alfareria.html", "link"),   # never shown
]


def patch_for(slug: str, patch_dir: Path | None) -> bytes | None:
    if patch_dir is None:
        return None
    for suffix in (".webp", ".png", ".jpg"):
        path = patch_dir / f"{slug}{suffix}"
        if path.exists():
            return path.read_bytes()
    return None


def write_previews(pdf: Path, body: bytes) -> list[Path]:
    """PNGs of the first and the last page next to the PDF (<name>-p1.png, <name>-p<N>.png)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        print("  (pypdfium2 no está instalado: sin vistas previas PNG)", file=sys.stderr)
        return []
    document = pdfium.PdfDocument(body)
    try:
        pages = sorted({0, len(document) - 1})
        paths = []
        for index in pages:
            path = pdf.with_name(f"{pdf.stem}-p{index + 1}.png")
            document[index].render(scale=150 / 72).to_pil().save(path)
            paths.append(path)
        return paths
    finally:
        document.close()


async def render_one(db, honor: Honor, out: Path, name: str, locale: str, paper: str,
                     patch_dir: Path | None, patch_slug: str | None = None,
                     modes: tuple[str, ...] = ("hoja", "ficha")) -> list[Path]:
    data = await load_sheet_data(db, honor, locale)
    patch = patch_for(patch_slug or honor.slug, patch_dir)
    written = []
    for mode in modes:
        started = time.perf_counter()
        body = render_sheet_pdf(data, paper=paper, mode=mode, patch_image=patch)
        elapsed = time.perf_counter() - started
        path = out / f"{name}-{mode}.pdf"
        path.write_bytes(body)
        previews = write_previews(path, body)
        print(f"{path}  {len(body) / 1024:.0f} KB  {elapsed * 1000:.0f} ms  "
              f"({len(data.requirements)} requisitos, {len(data.visible_resources)} recursos)"
              + (f"  + {len(previews)} PNG" if previews else ""))
        written.append(path)
    return written


async def find_honor(db, key: str) -> Honor | None:
    try:
        return await db.get(Honor, uuid.UUID(key))
    except ValueError:
        return (await db.execute(select(Honor).where(Honor.slug == key).limit(1))).scalar_one_or_none()


async def pick_samples(db) -> list[tuple[str, Honor]]:
    published = (Honor.status == "PUBLISHED", Honor.active.is_(True))
    counts = (select(HonorRequirement.honor_id, func.count().label("n"))
              .where(HonorRequirement.locale == "es").group_by(HonorRequirement.honor_id).subquery())
    many = (await db.execute(
        select(Honor).join(counts, counts.c.honor_id == Honor.id)
        .where(*published, Honor.id.in_(select(HonorResource.honor_id)))
        .order_by(counts.c.n.desc(), Honor.slug).limit(1))).scalar_one_or_none()
    nested = (await db.execute(
        select(Honor).where(*published, Honor.id.in_(
            select(HonorRequirement.honor_id).where(
                HonorRequirement.locale == "es",
                or_(HonorRequirement.description.like("%\n  a%"), HonorRequirement.description.like("%\na)%")))))
        .order_by(Honor.slug).limit(1))).scalar_one_or_none()
    empty = (await db.execute(
        select(Honor).where(*published, ~Honor.id.in_(select(HonorRequirement.honor_id)))
        .order_by(Honor.slug).limit(1))).scalar_one_or_none()
    picked = [("01-muchos-requisitos", many), ("02-con-subitems", nested), ("03-sin-requisitos", empty)]
    return [(label, honor) for label, honor in picked if honor is not None]


async def seed_demo(db) -> uuid.UUID:
    """A temporary published «Alfarería». No ministry, so it never collides with the real one."""
    honor_id = uuid.uuid4()
    category = (await db.execute(text(
        "SELECT id FROM honor_categories WHERE slug='arts-crafts-hobbies' ORDER BY created_at LIMIT 1"))).scalar()
    await db.execute(text(
        "INSERT INTO honors (id, category_id, name, slug, active, status, description, honor_type, authority,"
        " skill_level, year_introduced, source_url, version, published_at)"
        " VALUES (:id, :category, 'Alfarería', 'alfareria', true, 'PUBLISHED', :description, 'OFFICIAL_GC', 'GC',"
        " 1, 1929, 'https://www.guiasmayores.com/especialidades-ja---artes-y-actividades-manuales.html', 2, now())"),
        {"id": honor_id, "category": category,
         "description": "Descubre el arte de dar forma al barro: preparar la arcilla, modelar a mano y "
                        "entender la cocción, siguiendo la imagen bíblica del alfarero."})
    for position, (description, theoretical, instructions) in enumerate(DEMO_REQUIREMENTS, 1):
        await db.execute(text(
            "INSERT INTO honor_requirements (id, honor_id, position, description, is_theoretical, instructions,"
            " locale, source) VALUES (:id, :honor, :position, :description, :theoretical, :instructions, 'es',"
            " 'guiasmayores.com')"),
            {"id": uuid.uuid4(), "honor": honor_id, "position": position, "description": description,
             "theoretical": theoretical, "instructions": instructions})
    for position, (name, url, kind) in enumerate(DEMO_RESOURCES, 1):
        await db.execute(text(
            "INSERT INTO honor_resources (id, honor_id, position, name, url, type)"
            " VALUES (:id, :honor, :position, :name, :url, :type)"),
            {"id": uuid.uuid4(), "honor": honor_id, "position": position, "name": name, "url": url, "type": kind})
    await db.commit()
    return honor_id


async def drop_demo(db, honor_id: uuid.UUID) -> None:
    await db.rollback()
    await db.execute(text("DELETE FROM honors WHERE id = :id"), {"id": honor_id})  # requirements/resources cascade
    await db.commit()


async def warm(db, honors: list[Honor], modes: tuple[str, ...], locale: str, paper: str, apply: bool) -> dict:
    """Pre-store in R2 (manual). Without `apply` (the default) it only counts: no render, no R2 call."""
    total = len(honors) * len(modes)
    if not apply:
        print(f"dry-run: {len(honors)} especialidades publicadas × {len(modes)} modo(s) = {total} PDF "
              f"({locale}, {paper}). Nada se renderiza ni se sube; añade --apply para hacerlo.")
        return {"dry-run": total}
    if not settings.storage_configured:
        raise SystemExit("R2 no está configurado (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)")
    counts: dict[str, int] = {}
    for index, honor in enumerate(honors, 1):
        for mode in modes:
            started = time.perf_counter()
            result = await sheet_cache.warm_sheet(db, honor, locale=locale, paper=paper, mode=mode)
            counts[result] = counts.get(result, 0) + 1
            print(f"[{index}/{len(honors)}] {honor.slug} {mode}: {result} "
                  f"({(time.perf_counter() - started) * 1000:.0f} ms)")
    print("resumen: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    return counts


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="output folder (required unless --warm)")
    parser.add_argument("--warm", action="store_true", help="pre-store in R2 instead of writing files (manual)")
    run = parser.add_mutually_exclusive_group()
    run.add_argument("--dry-run", action="store_true", help="with --warm: only count (the default)")
    run.add_argument("--apply", action="store_true", help="with --warm: really render and upload")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", nargs="+", help="honor ids or slugs")
    group.add_argument("--all-published", action="store_true")
    group.add_argument("--samples", action="store_true")
    parser.add_argument("--demo", action="store_true", help="with --samples: use a temporary «Alfarería» honor")
    parser.add_argument("--locale", default="es")
    parser.add_argument("--paper", choices=("a4", "letter"), default="a4")
    parser.add_argument("--patch-dir", type=Path)
    parser.add_argument("--modes", nargs="+", choices=("hoja", "ficha"), default=["hoja", "ficha"])
    args = parser.parse_args()
    if args.warm and args.samples:
        parser.error("--warm works with --ids or --all-published")
    if not args.warm and args.out is None:
        parser.error("--out is required (unless --warm)")
    if not args.warm and args.apply:
        parser.error("--apply only makes sense with --warm")
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    modes = tuple(dict.fromkeys(args.modes))

    async with SessionLocal() as db:
        if args.samples:
            samples = await pick_samples(db)
            demo_id = await seed_demo(db) if args.demo else None
            try:
                for label, honor in samples:
                    if demo_id is not None and label == "02-con-subitems":
                        demo = await db.get(Honor, demo_id)
                        await render_one(db, demo, args.out, f"{label}-alfareria", args.locale, args.paper,
                                         args.patch_dir, "alfareria", modes)
                        continue
                    await render_one(db, honor, args.out, f"{label}-{honor.slug}", args.locale, args.paper,
                                     args.patch_dir, modes=modes)
            finally:
                if demo_id is not None:
                    await drop_demo(db, demo_id)
                    print(f"honor temporal {demo_id} eliminado")
        else:
            if args.all_published:
                honors = (await db.execute(select(Honor).where(
                    Honor.status == "PUBLISHED", Honor.active.is_(True)).order_by(Honor.slug))).scalars().all()
            else:
                honors = []
                for key in args.ids:
                    honor = await find_honor(db, key)
                    if honor is None:
                        print(f"no existe: {key}", file=sys.stderr)
                    else:
                        honors.append(honor)
            if args.warm:
                await warm(db, honors, modes, args.locale, args.paper, args.apply)
                await engine.dispose()
                return
            for honor in honors:
                await render_one(db, honor, args.out, honor.slug, args.locale, args.paper, args.patch_dir,
                                 modes=modes)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
