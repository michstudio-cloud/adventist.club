# Traducciones no oficiales del catálogo (requisitos, nombres, descripciones)

Pedido del propietario (25 sep 2026): «Necesitamos traducir todo a los idiomas, no dejarlas solo en inglés
cuando se muestra en español.» La app se ofrece en **es / en / pt**. Cuando una especialidad no tiene la
lista en el idioma de la interfaz, el API devuelve la mejor que haya (`best_locale`: exacto → idioma base →
región, `pt` → `pt-BR`) y el frontend pone la etiqueta «Sólo disponibles en Inglés». Este flujo rellena
esos huecos con traducciones **marcadas como no oficiales**, sin tocar nunca lo oficial.

## Reglas

- **Nunca se pisa** una lista escrita por un instructor (filas sin `source`) ni una oficial
  (`pathfinder-wiki` — la wiki solo se importa con páginas 100 % traducidas —, `guiasmayores.com`,
  `mundoja.org`, `gc-award-book-2020`…), ni un nombre de `honor_translations` con otro origen.
- Lo que se carga lleva `source = traduccion-no-oficial-<origen>` (convención del importador de
  Aventureros): `traduccion-no-oficial-pathfinder-wiki` (licencia **CC BY-SA 3.0**, obligatoria en lo
  derivado; `source_url` = la página de la wiki de la que se tradujo), `traduccion-no-oficial-guiasmayores.com`
  (sin licencia; `source_url` = el PDF), etc.
- Idiomas guardados: `es`, `en`, `pt-BR` (para portugués se usa **pt-BR**; el API lo sirve a `?locale=pt`).
- Se traduce desde el **original**: la lista inglesa de la wiki si existe; si no, la española.
  `position` e `is_theoretical` son los de la lista origen.
- El español es el idioma fuente del catálogo: `honors.name` / `honors.description` están en español,
  así que en `es/` solo se cargan requisitos.
- Cuando la wiki publique la página oficial 100 % traducida, `import_wiki_requirements.py` **reemplaza**
  la traducción no oficial en el sitio (mismos ids: el progreso de los miembros se conserva);
  `import_wiki_names.py` hace lo mismo con los nombres.

## Ficheros

`backend/data/requirement_translations/<locale>/<honor-slug>.json`, uno por especialidad e idioma:

```json
{
 "honor_slug": "aves-de-rapina",
 "ministry": "pathfinders",
 "source_locale": "en",
 "target_locale": "es",
 "source": "traduccion-no-oficial-pathfinder-wiki",
 "source_url": "https://wiki.pathfindersonline.org/w/AY_Honors/Raptors/Requirements",
 "license": "CC BY-SA 3.0",
 "name": null,
 "description": null,
 "requirements": [
  {"position": 1, "description": "¿Qué significa la palabra «rapiña»?", "instructions": null},
  {"position": 2, "description": "Clasificación:\n  a. Identificar …\n  b. …", "instructions": null}
 ]
}
```

- `name` / `description`: `null` = no se toca. Opcionales `name_source`, `name_source_url`, `name_license`
  cuando el nombre no es traducción propia: los nombres ingleses tomados del título de la wiki llevan
  `name_source: "pathfinder-wiki"` (es el nombre oficial, sin « (GC)» ni el número de las doctrinales).
- `requirements: []` = fichero solo de nombre.
- Un requisito es su enunciado más los sub-incisos, uno por línea, con dos espacios de sangría por nivel
  y el mismo marcador que el origen (`a.`, `i.`, `a)`…).

`backend/data/requirement_translations/pending.json`: lo que falta, por locale y ministerio
(`requirements`: especialidades sin lista en ese idioma que tienen de dónde traducir; `names`: sin nombre;
`no_list_at_all`: sin ninguna lista, no hay nada que traducir). Se regenera con el inventario.

## Herramientas (`backend/migrations/…`, todas con `DATABASE_URL`)

| Script | Qué hace | Escribe en la base |
|---|---|---|
| `catalog_tools/requirement_translation_gaps.py [--json] [--pending FICHERO]` | Inventario por ministerio y locale; tabla + JSON; `--pending` escribe `pending.json` | no |
| `catalog_tools/export_translation_sources.py --locale L (--slugs … \| --pending pending.json --offset N --limit 15) --out DIR` | Material de trabajo por especialidad: lista origen, referencias (lista en el otro idioma, página de la wiki parcialmente traducida si está en `~/adventist-wiki`), y el `draft` con los metadatos ya puestos | no |
| `catalog_tools/validate_requirement_translations.py FICHEROS…` | Valida contra la lista origen: posiciones, nº de líneas, sangría y marcadores de cada requisito, instrucciones, convención `source`/`license`, nombres vacíos (**errores**); palabras residuales del idioma origen y cifras que faltan (**avisos**). Sale 1 con errores, 2 con solo avisos | no |
| `import_requirement_translations.py [RUTAS] [--locale L] [--commit]` | Carga idempotente (simulacro por defecto) | sí, con `--commit` |

## Procedimiento para traducir un lote (agentes)

1. Base de trabajo local con los datos reales del catálogo (solo tablas de catálogo; Neon SOLO LECTURA):
   `etl_transl` en el Postgres de pruebas (127.0.0.1:55432, rol `test`). Si hay que rehacerla: esquema de
   `etl_hontr` (`pg_dump -s … etl_hontr | psql … etl_transl`) y los datos de `ministries, honor_categories,
   honor_category_translations, honors, honor_translations, honor_requirements` copiados de Neon con
   `COPY … TO STDOUT` en una transacción `READ ONLY` (el `pg_dump` 16 local no lee el servidor 18 de Neon).
2. Elegir el lote en `pending.json` (~15 especialidades por agente, por orden alfabético de slug) y exportar:
   ```sh
   cd backend/migrations/catalog_tools
   export DATABASE_URL=postgresql://test@127.0.0.1:55432/etl_transl
   python export_translation_sources.py --locale pt-BR --pending ../../data/requirement_translations/pending.json \
       --offset 0 --limit 15 --out /tmp/lote-pt-01
   ```
   Glosario de nombres (para citar otras especialidades por su nombre en el idioma destino):
   `psql "$DATABASE_URL" -A -F $'\t' -P footer=off -c "select h.slug, h.name es, coalesce(en.name, h.wiki_title) en, pt.name pt_br from honors h left join honor_translations en on en.honor_id=h.id and en.locale='en' left join honor_translations pt on pt.honor_id=h.id and pt.locale='pt-BR' order by h.slug" > /tmp/glosario.tsv`
3. Traducir: copiar el `draft` de cada fichero exportado, rellenar `description` (e `instructions`, `name`,
   `description` donde el draft trae `""`) y guardarlo en `backend/data/requirement_translations/<locale>/<slug>.json`
   (`json.dump(…, ensure_ascii=False, indent=1)`). Guía de estilo abajo.
4. Validar hasta **0 errores**; cada aviso se corrige o se justifica en el informe del lote:
   `python validate_requirement_translations.py ../../data/requirement_translations/pt-BR/<slug>.json …`
5. Probar la carga en la base de trabajo: `python ../import_requirement_translations.py --locale pt-BR` (simulacro)
   y luego `--commit` contra `etl_transl`; una segunda corrida debe decir `requirement_lists_unchanged` para todo.
6. `pytest backend/tests/test_requirement_translations.py` (comprueba la forma de todos los ficheros).
7. Regenerar `pending.json`: `python requirement_translation_gaps.py --pending ../../data/requirement_translations/pending.json`
   (contra `etl_transl` ya cargada).

## Guía de estilo

Común: conservar el número de líneas, la sangría y los marcadores; no añadir el número del requisito; conservar
todas las cifras; medidas imperiales → **añadir** la métrica entre paréntesis («10 pies (3 m)»), nunca sustituir;
nombres científicos intactos; citas bíblicas con el libro en el idioma destino (texto citado: RVR1960 en
español, ARA en portugués, KJV en inglés); libros de Elena G. de White por su título publicado; otras
especialidades por su nombre del catálogo en ese idioma; nada de texto inventado (lo vacío queda vacío).
Instrucciones de sección: `Sección: …` (es), `Seção: …` (pt-BR), `Section: …` (en).

- **Español (DIA):** verbos en infinitivo como las listas oficiales (Identificar, Describir, Explicar, Conocer,
  Nombrar, Hacer una lista de…); «Hacer uno de los siguientes:»; trato de usted («su comunidad»);
  Conquistador, club de Conquistadores, especialidad, Guía Mayor, consejero, unidad, director, investidura;
  clases Amigo / Compañero / Explorador / Orientador / Viajero / Guía; asociación o misión; español neutro.
- **Português do Brasil:** infinitivo (Identificar, Descrever, Explicar, Conhecer, Citar, Fazer uma lista de…);
  «Fazer uma das seguintes atividades:»; «seu/sua»; Desbravador, Clube de Desbravadores, especialidade,
  Guia Maior, conselheiro, unidade, diretor, investidura; classes Amigo / Companheiro / Pesquisador /
  Pioneiro / Excursionista / Guia; Associação ou Missão; ortografia do Acordo de 1990; sem espanholismos.
- **English:** Pathfinder Wiki style, imperative verbs, US spelling («honor», «counselor»); official English
  honour names when the honour is a known GC/division/ACS/ADRA one.

## Cargar en producción (lo hace el coordinador)

```sh
cd backend
DATABASE_URL='<Neon>' python migrations/import_requirement_translations.py                     # simulacro: revisar el JSON
DATABASE_URL='<Neon>' python migrations/import_requirement_translations.py --commit --operator <email>
DATABASE_URL='<Neon>' python migrations/import_requirement_translations.py                     # debe salir todo «unchanged»
```
Cada especialidad que cambia deja una fila `HONOR_TRANSLATION_IMPORT` en `audit_log` (hash del fichero).
Las hojas PDF cacheadas en R2 no hay que purgarlas: su clave lleva la huella del contenido.

## Estado (25 sep 2026, rama `feat/requirement-translations`)

Cargado y verificado en la base de trabajo `etl_transl` (datos reales del catálogo); **no** en Neon.

| Conquistadores (809) | es | en | pt-BR |
|---|---|---|---|
| Con lista antes | 738 | 787 | 0 |
| Listas traducidas (requisitos) | 58 (581) | 9 (70) | 119 (1 104) |
| Con lista después | 796 | 796 | 119 |
| Nombres añadidos | — (fuente) | 303 (281 título de la wiki + 22 traducidos) | 61 |
| Sin ninguna lista (nada que traducir) | 13 | 13 | 13 |

Fase A (es + en) completa. Fase B (pt-BR): hechas las **120 primeras por slug** (`abocetar` … `cactus`; una,
`album-de-recortes-avanzado-florida-conference`, sin lista: solo nombre). Pendiente según `pending.json`:
pt-BR Conquistadores **677 listas (6 016 requisitos) y 303 nombres**; pt-BR Aventureros 164 listas (989
requisitos) y 164 nombres. Las descripciones de Conquistadores están vacías en el origen (809): no hay nada
que traducir. Los 13 sin ninguna lista: `album-de-recortes-avanzado-florida-conference`, `comida-cruda`,
`desarrollo-rural`, `desarrollo-urbano`, `ensamblaje-y-mantenimiento-de-computadoras`,
`la-unidad-en-el-cuerpo-de-cristo`, `noken`, `nudos-avanzado`, `primeros-auxilios-de-san-juan`,
`procesar-texto-division-del-pacifico-sur`, `recaudacion-de-fondos-para-adra-{bronce,plata,oro}`.
Faltan también los nombres pt de 5 categorías (ADRA, Doctrinales, Servicios Comunitarios, Asociación de
Florida, Maestrías) en `honor_category_translations` (fuera de este importador).
