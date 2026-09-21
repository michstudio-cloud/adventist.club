DATOS DE PRUEBA. No son currículo oficial de nada y **no se cargan nunca en una base de
datos real**: existen sólo para que `tests/test_programs.py` ejercite
`migrations/import_programs.py` sin red y sin depender de las páginas descargadas de la wiki.

El contenido oficial vive fuera del repositorio hasta que una persona lo coteja con el manual
vigente (decisión D2) y entra por `migrations/data/programs/<ministerio>/<slug>.json`.
