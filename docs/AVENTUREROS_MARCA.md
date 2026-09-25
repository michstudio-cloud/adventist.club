# Aventureros · marca (Brand Book 2016)

Fuente: *Adventurer Club Brand Book* (General Conference Youth Ministries, 12 págs., 2016;
`~/Documents/DEEL/aventureros/brand-book-2016.pdf`, gcyouthministries.org). Encargo del
propietario 2026-09-24. Aquí: paleta, logos, tipografía, reglas y la propuesta de tokens `--av-*`
para el tema del ministerio (selector Conquistadores / Aventureros / Guías Mayores).

## Logo: qué significa (págs. 4–5)
Escudo de forma propia («Distinctive Emblem Shape», un triángulo redondeado como los parches de
award) con banda azul y el texto **ADVENTURER** arriba y **CLUB** abajo; dentro, un paisaje:

| Elemento | Símbolo | Mensaje del libro |
|---|---|---|
| Cruz blanca al centro | Jesucristo | Jesús es el centro de la vida del Aventurero |
| Familia (padre, madre, niño) caminando | Familia | El ministerio fortalece a la familia: padres e hijos siguen juntos a Jesús |
| Árboles, colinas, camino, cielo | Naturaleza | Padres e hijos aprenden de Jesús en la creación |
| Banda con el nombre | Nombre del ministerio | — |

## Variantes (págs. 6–7) — extraídas
Extraídas del PDF como vector con `tools/adventurer_brand.py` (mismo método que los parches:
se quita el texto de la página, se redacta todo lo que toca el exterior de la caja del logo y se
exporta la salida SVG de MuPDF optimizada con `adventurer_patches.optimize_svg`, más PNG 1024 px
y WebP 512 px con fondo transparente) a `~/Documents/DEEL/aventureros/marca/{svg,png,webp}` +
`manifest.json`. No van al repo (son del ministerio; el frontend ya tiene su copia).

| Archivo | Variante del libro | Uso |
|---|---|---|
| `aventureros-color` | Full Color | **Preferida** |
| `aventureros-grises` | Grayscale | Impresión en escala de grises |
| `aventureros-tinta` | One Tint (rojo `#9D1C1F`) | Una tinta sobre claro |
| `aventureros-tinta-negra` | One Tint / Black | Una tinta negra |
| `aventureros-tinta-negativo` | One Tint / Negative | Una tinta sobre fondo oscuro |
| `aventureros-contorno-negro` | Outline / Black | Contorno, grabados, bordados |
| `aventureros-contorno-negativo` | Outline / Negative (sobre cuadro azul) | Contorno blanco sobre color |
| `aventureros-contorno-color` | Outline / Color (celeste) | Contorno en color |
| `aventureros-mundial-color` | Adventurer Club World Logo · Full Color | Club mundial (líderes, institución global) |
| `aventureros-mundial-una-tinta` | World Logo · One Color | Ídem, una tinta |

El **logo mundial** (escudo sobre un globo con meridianos amarillo `#FDDE04` y óvalo granate) es
para representar al Club de Aventureros como institución global; lo usan líderes (pág. 7).

## Paleta (págs. 8–9)
El libro imprime CMYK, RGB y HEX de cada color, pero **tres de sus HEX no coinciden con su propio
RGB/CMYK ni con el color dibujado** (errata del libro). La columna «dibujado» es el color real de
las muestras y del logo vectorial (medido con pymupdf); es la que proponemos usar en pantalla.

| Color | Significado (libro) | CMYK | RGB (libro) | HEX (libro) | Dibujado (usar) |
|---|---|---|---|---|---|
| Azul | El cielo, el reino celestial | 90/64/0/0 | 32 98 175 | `#2062AF` | `#2363AF` |
| Escarlata | Sangre y redención (Lv 17:11) | 29/100/100/38 | 128 0 0 | `#800000` ⚠ | `#7F1416` |
| Blanco | Justicia, pureza | 0/0/0/0 | 255 255 255 | `#FFFFFF` | `#FFFFFF` |
| Verde | Crecer y florecer; vida cristiana que da fruto | 75/6/100/0 | 64 141 53 ⚠ | `#40AB35` ⚠ | `#40AA48` |
| Celeste (compl.) | — | 54/0/10/0 | 3 238 **272** ⚠ | `#03EEFC` ⚠ | `#66CBE0` |
| Ocre (compl.) | — | 18/41/94/1 | 209 152 53 | `#BFDE00` ⚠ (copia del lima) | `#D09833` |
| Verde oscuro (compl.) | — | 90/34/100/27 | 6 103 48 | `#066730` | `#0C6735` |
| Verde oliva (compl.) | — | 71/25/100/9 | 84 138 53 | `#548A35` | `#558A3E` |
| Lima (compl.) | — | 30/0/100/0 | 191 222 0 | `#BFDE00` | `#BDD630` |
| Amarillo (logo mundial) | — | — | — | — | `#FDDE04` |

⚠ Erratas: el RGB del celeste es imposible (B = 272) y su HEX `#03EEFC` es un cian neón que no se
parece a la muestra; el HEX del ocre repite el del lima; el RGB del verde no casa con su HEX. El
escarlata `#800000` es el «maroon» web; el CMYK 29/100/100/38 y el dibujo dan `#7F1416`.
El libro pide reproducir el logo en CMYK cuando se pueda y usar RGB/HEX en digital.

### Comparación con `public/brand/aventureros.svg` del frontend
El SVG del frontend (conquistadores-app, 26,9 KB, viewBox 581×541) es el mismo dibujo pero
**coloreado con los HEX impresos**, erratas incluidas: `#2062AF`, `maroon`, `#40AB35`, **`#03EEFC`**,
`#D19935`, `#BFDE00`, `#548A35`, `#066730`. La diferencia visible es el cielo: cian neón `#03EEFC`
en vez del celeste `#66CBE0` del libro; el lima (`#BFDE00` frente a `#BDD630`) y el escarlata
(`#800000` frente a `#7F1416`) apenas se notan. Propuesta (frontend, no hecho aquí): sustituir por
`~/Documents/DEEL/aventureros/marca/svg/aventureros-color.svg` (sale del PDF oficial) o, como
mínimo, cambiar `#03EEFC` → `#66CBE0` en ese archivo.

## Tipografía (pág. 10)
- Logotipo: **Arial Bold** (familia oficial del logo; «optimizada para impresión, excelente
  legibilidad»). El libro no define tipografías de texto corrido.
- En la app: los textos siguen con la tipografía del sistema de diseño (`cq-*`); Arial Bold solo
  si se compone texto junto al logo (p. ej. «CLUB DE AVENTUREROS» en un certificado).

## Reglas de uso (págs. 6 y 11)
- **Zona de exclusión**: alrededor del logo, en todos los lados, un espacio libre igual a la
  **longitud de la cruz**, medido desde el borde más externo. Si hay color detrás, debe extenderse
  al menos una cruz por cada lado. Ningún elemento invade ese espacio.
- Preferir fondo blanco o neutro. Si no hay más remedio que ponerlo sobre color o foto, usar la
  **versión negativa**.
- Prohibido: usar **solo una parte** del logotipo; **rotarlo**; ponerlo sobre colores que no
  combinan; usar la versión negativa sobre fondos demasiado claros o recargados; **efectos**
  (difuminado, relieve, sombras, etc.). (Y, como para todos los emblemas denominacionales: no
  redibujar, recolorear ni deformar — `public/brand/README.md`.)
- A color es la opción preferida; una tinta cuando el diseño lo exija.

## Tokens propuestos `--av-*` (tema del ministerio)
Para el selector de ministerio: el tema se activa con un atributo en la raíz (p. ej.
`[data-ministry-theme="adventurers"]`, junto a `pathfinders` / `master-guides`) y sobreescribe los
tokens de marca del sistema `cq-*` (`--brand-gradient`, `--shadow-brand`, el tono de
`.cq-min-chip[data-ministry="adventurers"]`, que hoy usa `--sys-blue`). Contraste (WCAG): el azul y el
escarlata pasan AA con texto blanco (6,0:1 y 10,4:1); el verde (3,0:1), el celeste, el lima y el ocre
**no** llevan texto blanco en tamaño normal: sobre ellos, `--av-ink` (6,2:1 a 11,2:1).

```css
/* app/styles/tokens.css — primitivos del Brand Book 2016 (colores «dibujados», ver la tabla) */
:root {
  --av-blue: #2363af;        /* cielo / reino celestial — primario */
  --av-scarlet: #7f1416;     /* redención — acento, bordes del escudo */
  --av-green: #40aa48;       /* crecimiento — éxito, progreso (texto encima: --av-ink) */
  --av-sky: #66cbe0;         /* complementario — fondos suaves, ilustración */
  --av-ochre: #d09833;       /* complementario — camino, destacados cálidos */
  --av-forest: #0c6735;      /* complementario — verde oscuro */
  --av-olive: #558a3e;       /* complementario */
  --av-lime: #bdd630;        /* complementario — colinas */
  --av-gold: #fdde04;        /* logo mundial */
  --av-ink: #11151c;         /* texto sobre celeste / lima / ocre */
}

/* semánticos del tema */
[data-ministry-theme="adventurers"] {
  --ministry-primary: var(--av-blue);
  --ministry-primary-ink: #ffffff;
  --ministry-accent: var(--av-scarlet);
  --ministry-success: var(--av-green);
  --ministry-surface-tint: color-mix(in srgb, var(--av-sky) 14%, transparent);
  --brand-gradient: linear-gradient(135deg, var(--av-sky), var(--av-blue) 55%, var(--av-scarlet));
  --shadow-brand: 0 14px 34px color-mix(in srgb, var(--av-blue) 22%, transparent);
  --ministry-logo: url("/brand/aventureros.svg");
}
.dark[data-ministry-theme="adventurers"],
[data-ministry-theme="adventurers"] .dark {
  /* en oscuro el azul del libro queda apagado: se aclara sin salir del tono */
  --ministry-primary: color-mix(in srgb, var(--av-blue) 70%, white);
  --ministry-primary-ink: #0b1220;
  --ministry-accent: color-mix(in srgb, var(--av-scarlet) 60%, white);
  --ministry-surface-tint: color-mix(in srgb, var(--av-sky) 10%, transparent);
}
```

Emblemas de clase (pines, arte GC del Director's Manual págs. 24 y 55 y de mundoja): ver
`docs/AVENTUREROS_CLASES.md`.
