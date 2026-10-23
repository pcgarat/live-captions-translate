Última modificación: 2026-10-23

# Intent: Fase 2.12 — redimensión continua y persistencia total del overlay

**Estado:** confirmado (chat 2026-10-23).

## Qué

1. Redimensionar el overlay en vertical de forma **continua** (píxel a píxel), no a saltos
   de líneas de caption.
2. Que **todos** los parámetros de apariencia, el tamaño y la posición del overlay se
   guarden y se restauren **siempre** entre reinicios.

## Por qué

La fase anterior cuantizó la altura de la ventana a múltiplos del alto de línea para que
el texto nunca quedara cortado. El efecto colateral es que arrastrar el borde inferior
«salta»: el usuario pide control fino y no puede dejar la ventana a la altura que quiere.

Además, la altura guardada se cuantizaba también al restaurar: cada arranque recalculaba
la altura con el redondeo de la fase anterior y el tamaño real dejaba de coincidir con el
elegido. Con el cuantizado fuera, la geometría guardada debe ser exacta y escribirse
también cuando el cambio viene de algo que no es arrastrar el ratón (p. ej. un cambio de
tipografía o un preset).

## Decisiones del usuario

1. **Persistencia**: la apariencia se escribe al pulsar Guardar en Ajustes y, como red de
   seguridad, se vuelca al cerrar la app. No escritura en vivo por cada tick de un slider.
2. **Sobrante de alineación**: el bloque de captions se **centra verticalmente** en el
   hueco libre; el aire sobrante se reparte arriba y abajo.

## Fuera de alcance

Redimensión desde la UI de Ajustes (sigue siendo arrastre del borde), cambios de
tipografía que redimensionen la ventana para conservar el número de líneas, y
persistencia de estado de sesión (texto en pantalla).
