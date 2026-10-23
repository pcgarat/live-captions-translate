Última modificación: 2026-10-23

# Intent: Fase 2.13 — Ciclo de vida de presets en el selector

## Qué

Unificar el comportamiento de **todos** los selectores de preset de la app
(preset general de app, decoding de traducción y modo de latencia) alrededor de
tres ideas:

1. **Un preset de fábrica no se guarda encima.** Los que vienen con la app al
   arrancar por primera vez son de referencia: no muestran «Guardar».
2. **Si toco algo, aparecen las acciones de edición.** Al modificar cualquier
   opción que ese preset fija, junto al selector aparecen «Guardar nuevo» (pide
   nombre) y «Restaurar valores del preset».
3. **Siempre se pueden ver los valores.** Con cualquier preset seleccionado hay
   un botón «Ver valores» que abre un diálogo con las opciones que fija y el
   valor que asigna a cada una.

## Por qué

Hoy los tres selectores se comportan distinto y de forma sorprendente:

- El preset general permite «Guardar» **encima de un preset de fábrica**
  (persiste una copia editada de `video-en-es`, por ejemplo).
- El preset de decoding permite «Guardar» encima de un preset de usuario, pero
  editar un slider salta a `custom` sin decirlo.
- El selector de latencia no tiene ni guardar ni borrar: solo editar en sitio y
  un botón de reset que además es ambiguo (¿reset del modo o de toda la app?).

El resultado es que no se puede saber, mirando la UI, qué es de fábrica, qué he
cambiado yo ni cuánto se desvía un preset de su origen. Y no hay forma de
inspeccionar un preset sin aplicarlo.

## Criterio de éxito

- Con un preset de fábrica recién aplicado: no hay botón de guardar.
- Tras tocar una opción cubierta por el preset: aparecen «Guardar nuevo» (pide
  nombre) y «Restaurar».
- Guardar pregunta siempre qué hacer: **sobrescribir** el preset activo (si es
  de usuario) o **crear uno nuevo**.
- «Ver valores» está siempre disponible y muestra opciones + valores del preset
  seleccionado, sin aplicarlo.
- Ningún preset de fábrica se puede borrar ni sobrescribir, en ningún selector.

## Fuera de alcance

- Sincronizar presets entre selectores (un preset general sigue conteniendo a
  los otros dos como estado; no se convierten en referencias cruzadas).
- Renombrar presets, exportar/importar fichero, cloud.
- Marcar «sucio» el preset sin tocar opciones (dirty solo por valores distintos).
