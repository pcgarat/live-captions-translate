Última modificación: 2026-10-23

# Spec: Fase 2.13 — Ciclo de vida de presets en los selectores

**Estado:** aprobado (2026-10-23; decisiones de UX confirmadas con el autor).  
**Intent:** `docs/intent/fase2.13-presets-guardar-restaurar-ver-valores-2026-10-23.md`  
**Sustituye en parte:** fase 2.1 (selector de modo de latencia), fase 2.4
(presets de decoding) y fase 2.8 (presets generales).

## Objective

Unificar los tres selectores de preset (general, decoding de traducción y
latencia) con una regla única:

| Estado | Botones junto al selector |
|--------|---------------------------|
| Preset de fábrica sin cambios | **Ver valores** |
| Preset de fábrica modificado | **Ver valores**, **Guardar como nuevo**, **Restaurar** |
| Preset propio (usuario) sin cambios | **Ver valores**, **Borrar** |
| Preset propio modificado | **Ver valores**, **Guardar**, **Restaurar**, **Borrar** |
| Sin preset (`(ninguno)`) | ninguno |

**Usuario:** solo el autor. **Éxito:** no se puede sobrescribir un preset de
fábrica; cualquier desviación es visible y reversible; cualquier preset se puede
inspeccionar sin aplicarlo.

## Modelo: preset = catálogo + valores guardados + estado vivo

Un *preset* es un **id** con un mapa de valores. Hay dos clases:

- **Fábrica**: ids y valores definidos en código; inmutables. Se re-sincronizan
  en cada `validate_config` (igual que los perfiles de decoding de la 2.4). Un
  `config.json` antiguo con copias editadas de fábrica pierde esas ediciones.
- **Propio**: ids creados por el usuario, guardados en config. Sobrescribibles y
  borrables.

El **estado vivo** (config efectiva) no pertenece a ningún preset: seleccionar
un preset lo copia al estado vivo, y a partir de ahí el estado vivo evoluciona.
*Modificado* = alguna opción que el preset fija difiere del valor vivo.

### Preset general (`app_presets`)

- Fábrica: catálogo de `src/presets.py` (`default` + `video-*`). Sin cambios en
  el modelo: snapshots completos (`default`) o parciales (`video-*`).
- Propios: snapshots completos en `app_presets`, igual que hasta ahora.
- Opciones comparadas (`APP_PRESET_OPTIONS` de `src/preset_state.py`): solo
  opciones visibles y accionables por el usuario. **Excluidas** de comparación y
  de «Ver valores»: geometría (`window_*`, `settings_window_*`),
  `installed_languages`, espejos (`agreement_n`, `max_latency_sec`,
  `min_chunk_seconds`) y flags internos (`opt_*`).
- Guardar en un preset de fábrica → `ValueError` en config y botón inexistente
  en UI.

### Preset de decoding de traducción (`translation_profiles`)

- Fábrica: `fast` / `balanced` / `quality` (constantes).
- `custom` deja de ser un salto automático: es un preset propio más (editable y
  sobrescribible, no borrable).
- Editar los sliders **ya no** cambia la selección a `custom`: deja el preset
  seleccionado en estado *modificado* con un borrador en memoria del diálogo.
- Opciones: `beam_size`, `length_penalty`, `no_repeat_ngram_size`.

### Preset de latencia (`latency_profiles`)

Cambio de modelo para poder guardar presets:

- Ids de fábrica: `stable` / `low`. Valores ahora incluyen `beam_size`
  (estable 5, baja 1) para que un preset de latencia sea autocontenido; se
  elimina el `beam_size_for_mode(mode)` cableado por nombre.
- La fábrica **se deriva de `DEFAULTS`** (es decir, de `latency_profiles` en
  `config.example.json`) más el beam del catálogo. Así una instalación nueva no
  nace con el preset de fábrica ya «modificado», que es lo que pasaba al tener
  los knobs duplicados en el código.
- Ids propios: claves extra en `latency_profiles`, con los mismos cuatro knobs.
- `latency_mode` pasa a ser el id del preset activo (validado contra el mapa).
- Opciones mostradas: `agreement_n`, `max_latency_sec`, `min_chunk_seconds`,
  `beam_size`. La UI sigue editando solo las dos primeras; las otras dos viajan
  en el preset.
- Una config de fase 1 con los knobs sueltos en la raíz migra a un preset
  propio `personalizado` que queda activo: no se puede escribir en fábrica.

## Comportamiento

### Modificado (dirty)

Se recalcula al vuelo (timer de 250 ms mientras el diálogo está abierto) y se
compara contra los valores del preset seleccionado. `(ninguno)` nunca marca
nada.

Un `audio_monitor` vacío en el preset no cuenta como modificación: la app elige
el primer dispositivo al arrancar cuando la config no trae ninguno, y eso no es
una desviación del preset.

### Guardar

- Preset de fábrica modificado: un solo botón **Guardar como nuevo** → pide
  nombre, valida slug y colisión, crea el preset con los valores vivos.
- Preset propio modificado: **Guardar** → diálogo con
  **Sobrescribir «id»** / **Guardar como nuevo…** / **Cancelar**.
  - Sobrescribir: escribe los valores vivos en ese id y lo deja activo.
  - Guardar como nuevo: pide nombre; rechaza ids existentes (no hay
    sobrescritura silenciosa).
- Al guardar (sobrescribiendo o creando), la config efectiva pasa a ser la del
  preset guardado. Si no, el preset recién guardado nacería *modificado* y los
  cambios del usuario se verían revertidos en cuanto el diálogo recargara.

### Restaurar

Devuelve las opciones del preset a **sus** valores (no a los de fábrica
globales), descartando el borrador del diálogo y, en el preset general,
reaplicando el snapshot al runtime.

### Ver valores

Diálogo modal con las opciones que fija el preset y el valor que asigna a cada
una. No aplica nada. En presets parciales se avisa de que el resto se hereda
del estado vivo.

### Aceptar el diálogo de Ajustes con cambios sin guardar

- Preset propio: el borrador se persiste en ese preset al aceptar (equivale a
  guardar los ajustes).
- Preset de fábrica: no se puede persistir; se pregunta
  **Guardar como nuevo…** / **Descartar cambios** / **Cancelar**. Descartar
  revierte las opciones del preset antes de aplicar.
- Preset general: no hay borrador (el estado vivo se aplica igual); el preset
  queda *modificado*.

## Boundaries

**Always:**
- Fábrica inmutable y re-sincronizada al validar, en los tres dominios.
- Botón de guardar inexistente (no solo deshabilitado) sobre fábrica limpia.
- «Ver valores» disponible siempre que haya preset seleccionado.
- Borrar solo presets propios, con confirmación.
- Restaurar = valores del preset, no valores de fábrica globales.

**Ask first:**
- Renombrar presets, export/import, presets compartidos entre selectores.
- Incluir geometría en la detección de modificado.

**Never:**
- Escribir en un preset de fábrica.
- Guardar como nuevo sobrescribiendo un id existente en silencio.
- Mostrar botones de edición con `(ninguno)`.
- Añadir estos botones al chrome del overlay (decisión: solo Ajustes).

## Archivos

```
src/config.py              # fábrica inmutable y derivada de DEFAULTS, latencia
                           # con beam_size + CRUD, guardar/sobrescribir propios
src/preset_state.py        # catálogo de opciones, formateo y comparación dirty
src/asr/pipeline.py        # beam_size desde el perfil de latencia
src/ui/presets_ui.py       # diálogo Ver valores + diálogo guardar
src/ui/settings.py         # tres barras, borradores y refresco de acciones
config.example.json        # beam_size en latency_profiles
tests/test_preset_state.py # catálogo, dirty, formateo
tests/test_config.py       # latencia usuario, fábrica inmutable
tests/test_app_presets.py  # fábrica no sobrescribible, guardar coherente
tests/test_settings_ui.py  # visibilidad de botones, borradores, ver valores
```

## Tests

| Tipo | Caso |
|------|------|
| Unit | Editar un preset de fábrica y validar → vuelve a valores de fábrica |
| Unit | `save_app_preset` sobre fábrica → `ValueError` |
| Unit | Guardar (sobrescribir o como nuevo) deja el preset sin modificar |
| Unit | Presets de latencia propios: crear, sobrescribir, borrar, activo |
| Unit | `effective_latency_profile` incluye `beam_size` del preset activo |
| Unit | `changed_option_keys` ignora geometría y detecta cambios de opciones |
| UI | Fábrica limpia: sin Guardar/Restaurar; con cambio: aparecen |
| UI | Propio: Guardar pregunta sobrescribir/nuevo; cancelar no toca config |
| UI | Editar sliders de decoding no cambia el preset seleccionado |
| UI | «Ver valores» lista las opciones del preset |
| UI | Aceptar con fábrica modificada: guardar como nuevo / descartar / cancelar |
| Manual | Ciclo completo en los tres selectores + reinicio de la app |
