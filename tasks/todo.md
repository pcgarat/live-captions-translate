# Deuda: extraer el ciclo de presets de Settings

Origen: fase 2.13, `docs/specs/fase2.13-presets-guardar-restaurar-ver-valores-2026-10-23.md`.

La fase está cerrada en comportamiento. Esto no abre una fase nueva: es trabajo
aplazado para no mezclar un refactor con la regla de botones ya entregada.

## Por qué

`src/ui/settings.py` repite el mismo ciclo tres veces (preset general, latencia
y decoding): estado vivo, aplicar, guardar, guardar como nuevo, restaurar,
borrar y refrescar la barra. `PresetActionBar` ya comparte los botones; el
diálogo sigue decidiendo por dominio con métodos paralelos.

El estado «modificado» lo refresca un `QTimer` de 250 ms que lee widgets. El
botón puede tardar hasta ese intervalo en aparecer, y el diálogo hace polling
aunque no haya cambiado nada.

## Qué haría el cambio

- Un controller por dominio (`App`, `Latency`, `Decode`) con la misma
  superficie: `ids()`, `values(id)`, `live()`, `apply(id)`, `save()`,
  `save_as()`, `delete()`, `restore()`.
- `PresetActionBar` se conecta a ese controller. `SettingsDialog` construye
  los tres y pinta los controles.
- Quitar `_dirty_timer`. Los widgets que entran en la comparación emiten
  `changed` y la barra se refresca en ese momento.
- La comparación sigue en `src/preset_state.py`, sin Qt, para poder testearla
  sin `QApplication`.

## Fuera de este apunte

- No cambia lo que ve el usuario en la 2.13.
- No se implementa hasta un spec propio (intent → spec → plan).

## Task

- [ ] Spec de la extracción antes de tocar código
- [ ] Controllers por dominio; la barra deja de conocer los tres selectores
- [ ] Sustituir `_dirty_timer` por la señal `changed`
- [ ] Verify: `tests/test_preset_state.py` y `tests/test_settings_ui.py` siguen
      verdes, incluida la tabla de `ActionState`
