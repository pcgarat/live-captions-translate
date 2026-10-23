Última modificación: 2026-10-23

# Spec: Fase 2.12 — redimensión continua y persistencia total del overlay

**Estado:** aprobado (chat 2026-10-23).
**Intent:** `docs/intent/fase2.12-redimension-continua-persistencia-2026-10-23.md`

## Objective

El alto del overlay es un valor en píxeles libres (sin cuantizar) y su geometría guardada
se restaura exactamente igual en el siguiente arranque. La apariencia completa
(tipografía, colores, padding, alineación, siempre encima, HUD) sigue persistiendo.

## Modelo de geometría

- **Alto de ventana**: píxeles continuos en `[_MIN_WINDOW_HEIGHT, _MAX_WINDOW_HEIGHT]`
  (48–1600, los mismos clamps de `validate_config`).
- **Suelo del overlay**: 48 px fijos. No crece con padding, chrome, fuente, HUD ni
  aviso. Por debajo de una línea el viewport recorta el texto.
- **Viewport de captions**: `alto_ventana − (2·padding + slot_chrome + hud/notice)`, en
  píxeles libres. Ya **no** se redondea a múltiplos de línea.
- Se eliminan `_MIN_CAPTION_LINES`, `_MAX_CAPTION_LINES`, `_lines_from_height()`,
  `_snap_height_to_lines()`, `_visible_caption_lines()` y el alias `_chrome_height()`:
  eran el mecanismo del cuantizado. `_height_for_lines(n)` se conserva como ayuda para
  calcular la altura por defecto (3 líneas) y para los tests.
- **Resize vertical**: `_continue_resize()` aplica el delta tal cual y recorta al rango.
  `_handle_mouse_release()` ya **no** vuelve a cuantizar.
- **Scroll**: el anclaje inferior es al **final** del contenido, sin redondear. Con alto
  continuo, el borde superior del viewport puede caer a media línea (antes se redondeaba,
  lo que escondía justo la línea más reciente). El contenido sigue construyéndose en
  múltiplos de línea, así que nunca se corta una línea por abajo.

## Alineación

- El área de scroll se alinea `AlignVCenter` (más la alineación horizontal configurada):
  si el texto ocupa menos que el viewport, el bloque queda centrado y el aire se reparte
  arriba/abajo. Sustituye al `AlignTop` de la fase 2.11.
- Los labels conservan `AlignTop` internamente: la alineación vertical la decide el área.

## Persistencia

- `window_pos`, `window_width`, `window_height` se escriben **exactos** (sin redondeo) en
  cada volcado.
- Se guarda al: soltar un arrastre/redimensión, mover (`moveEvent`, debounce 250 ms),
  redimensionar (`resizeEvent`, mismo debounce), cambiar siempre-encima, Guardar en
  Ajustes y cerrar la app.
- Añadir `resizeEvent` al guardado cubre cambios de tamaño que no vienen del ratón
  (`apply_config`, WM, atajos).
- Claves de apariencia persistidas: `always_on_top`, `font_family`, `font_size`,
  `font_weight`, `font_color`, `bg_color`, `bg_alpha`, `padding`, `text_align`,
  `debug_hud` (+ geometría en `window_*`). Ninguna se recalcula al arrancar.

## Tests

- Resize vertical continuo: deltas de 1 px cambian la altura en 1 px y se persisten.
- Restaurar una altura guardada no la modifica (`window_height=333` → 333).
- Resize programático (`resize()`) con la ventana visible persiste la altura.
- Caption corta: el bloque queda centrado verticalmente en el viewport.
- Sin regresiones: chrome flotante, anclaje del scroll, presets y `apply_config`.
