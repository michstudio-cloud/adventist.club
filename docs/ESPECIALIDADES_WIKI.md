# Especialidades: fuente oficial (Pathfinder Wiki) y nombres por idioma

Fuente: <https://wiki.pathfindersonline.org/w/AY_Honors> — wiki oficial del Ministerio Juvenil de la
División Norteamericana. En inglés son *Honors*, en español *Especialidades*, en portugués
*Especialidades*, en francés *Spécialisations*, etc.; por eso el catálogo guarda **un nombre por idioma**
y nunca traduce "a mano" un nombre oficial.

## Reglas de uso de la fuente

- **Licencia:** el texto de la wiki es **CC BY-SA 3.0** → atribución obligatoria y la misma licencia para
  lo derivado. Cada fila guarda `source`, `source_url` y `license`; donde se muestre texto de la wiki
  (requisitos, respuestas) debe aparecer "Fuente: Pathfinder Wiki, CC BY-SA" con enlace.
- **robots.txt:** para bots genéricos prohíbe `/w/api.php`, `/w/index.php?` y `/w/Special:`. Solo se leen
  páginas normales `/w/AY_Honors/...`, **una petición cada 10 s**, con User-Agent identificable.
- Las imágenes de parches de la wiki solo se usaron para **comparar** (no se sirven ni se copian a R2;
  nuestros parches siguen siendo los ya cargados).

## Estado (21 sep 2026)

| | |
|---|---|
| Especialidades en el índice oficial `/es` | 525 |
| Enlazadas con nuestro catálogo (809) | **506** — 470 por nombre, 22 por errata conocida, 14 comparando el parche |
| De la wiki sin equivalente nuestro | 19 → `backend/migrations/data/wiki_manual_links.csv` |
| Nuestras sin página en la wiki | 303 (Doctrinales, Maestrías, ADRA, Asociación de Florida, DSA/otras divisiones) |
| Nombres cargados en Neon | en 506 · pt-BR 445 · fr 105 · uk 83 · de 12 (+ 43 nombres de categoría) |

Decisiones tomadas al comparar parches: las ocho de *Servicios Comunitarios* de la wiki (Refugee
Assistance, Rural Development, Identifying Community Needs, Crisis Intervention, Disaster Ministries,
Feeding Ministries, Tutoring, Serving Communities) corresponden a nuestras copias
`…-community-services`, **no** a las de ADRA (parche distinto). "Mayordomía" ≠ "La mayordomía"
(Doctrinales), "Introducción a la herencia de los pioneros" ≠ "Pioneros adventistas", "Primeros auxilios"
(NAD) ≠ "Primeros auxilios de San Juan".

## Cómo enlazar a mano

1. Abrir `backend/migrations/data/wiki_manual_links.csv` y escribir en `nuestro_slug` el slug de nuestra
   especialidad (el de la URL `/categories/...` o la columna `slug` de `honors`). Dejar vacío si no existe.
2. `DATABASE_URL=… python import_wiki_names.py data/wiki_honor_links.json --manual data/wiki_manual_links.csv`
   (simulacro) y luego con `--commit`. Es idempotente.
3. Las 303 nuestras sin wiki están en `wiki_honor_links.json → ours_without_wiki`. No son un error: la
   wiki solo cubre AG + DNA. Sus nombres en otros idiomas saldrán de los manuales de su división.

Para rehacer el cruce: `catalog_tools/match_wiki.py <catalog.json> <wiki_es.json> <salida.json>`; las
erratas aceptadas (`ALIASES`), los enlaces verificados por imagen (`IMAGE_VERIFIED`) y los falsos
parecidos (`IMAGE_REJECTED`) están en ese archivo y son la memoria de las decisiones.

## API

`GET /api/v1/honors?locale=en`, `/honors/categories?locale=pt-BR`, `/honors/{id}?locale=fr`.
- `name` viene en el idioma pedido si existe; si no, en español (`name_locale` dice cuál llegó y
  `original_name` conserva el español). Región → idioma → cualquier región del idioma (`pt` → `pt-BR`).
- La búsqueda `q` encuentra por el nombre en cualquiera de los dos idiomas.
- El idioma es **siempre explícito** (`?locale=`, el idioma de la UI). No se usa `Accept-Language`:
  una pantalla en español en un navegador en inglés se llenaba de nombres en inglés.
- Nuevos campos públicos: `wiki_title`, `authority` (GC/NAD), `skill_level`, `year_introduced`.

## Siguiente paso: requisitos oficiales

Cada especialidad enlazada tiene `wiki_title`; sus requisitos están en
`/w/AY_Honors/<wiki_title>/Requirements` (+ `/es`, `/pt-br`, `/fr`) y las respuestas en `/Answer_Key`.
Plan: un rastreador reanudable (10 s por página ≈ 506 × 2 idiomas ≈ 3 h), que guarda el HTML crudo fuera
del repo, extrae la lista numerada a `honor_requirements` (con `locale`, `source_url`, `license`) y deja
las especialidades en el flujo normal (los requisitos importados no sustituyen los que un instructor ya
haya escrito). Requiere añadir `locale` a `honor_requirements` (migración aditiva) — pendiente.
