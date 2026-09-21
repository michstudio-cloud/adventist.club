Plantilla de INVESTIDURA de clase (Conquistadores y Aventureros), bloque F · F1.

`meta.json` declara `{"kinds": ["program"], "ministries": ["pathfinders", "adventurers"]}`:
sólo se usa para certificados de programa y sólo de esos dos ministerios. Sin él, una
plantilla vale para todos los ministerios y es de tipo `honor` (compatibilidad con las que
ya existían). La investidura de Guía Mayor necesita su propia carpeta
(`investidura-guia-mayor`, `"ministries": ["master-guides"]`) y llega con F4.

DISEÑO PROVISIONAL: el armazón es el de `especialidad-basica` con los textos de investidura,
para que el flujo funcione de punta a punta en el piloto. **El responsable debe sustituirlo
por el diseño definitivo antes de publicar la clase «Amigo»**, manteniendo los `id` y el
contrato de `docs/I18N_Y_PLANTILLAS.md`. El nombre de la clase viaja en `honor_name`.
