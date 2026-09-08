# Countertrend: recuperación local, confluencia y geometría direccional

Estado: implementada en Windows como GERI 1.9.0, reglas 1.3.0 y Entry Opportunity
12.0.0, ensamblado 7.51.0. Validada funcionalmente con pruebas y replay causal
28/08–04/09/2026; no constituye validación de rentabilidad. Sin despliegue WSL
ni órdenes. Las secciones siguientes describen el criterio acordado; los
parámetros concretos y alcance implementado están en el README del motor.

## Problema verificado

La implementación actual calcula el lado táctico invirtiendo
`structural.trade_side` y copia `structural.active_level_price` como objetivo.
En HUT, una cadena LONG BUILDING con resistencia activa 116.72 produce un
candidato SHORT con objetivo superior al precio. La elegibilidad lo rechaza
por orden de niveles y R/R; la composición solo proyecta countertrend LONG.
Esto explica un candidato analítico inválido, no una orden SHORT ejecutada.
El rebase 1.8.0 solo reconsidera cadenas cuya madurez sea EXTENDED.

## Separación de conceptos

- Contexto estructural: roto/bajista, correctivo, en recuperación o alcista.
- Dirección de operación: LONG o SHORT, seleccionada con evidencia propia.
- Madurez: conservar la familia CT0–CT4 separada de GERI y Swing Trade.
- Confluencia: evidencia adicional, separada de madurez y elegibilidad.

Una etiqueta estructural SHORT no debe presentarse como una orden ni generar
automáticamente una venta. Una cadena LONG incompleta no debe impedir evaluar
una recuperación LONG local dentro de una corrección.

## Madurez de recuperación propuesta

Los contratos ya contienen CT0–CT4. La siguiente semántica se aplica desde la
versión 1.9.0; no reinterpreta eventos históricos de versiones anteriores.

| Madurez | Evidencia causal | Consecuencia |
|---|---|---|
| CT0 | Contexto correctivo y piso candidato | Observación |
| CT1 | Reacción desde soporte y estabilización del piso | Preparación |
| CT2 | Ruptura local confirmada y aceptación por retesteo o consolidación; riesgo válido | Primera entrada analítica elegible |
| CT3 | Recuperación confirmada por bloque 4H completo | Confirmación estructural |
| CT4 | Continuación confirmada conservando soporte recuperado | Continuación |

No equiparar tocar una zona con confirmación. La progresión requiere evidencia
registrada, no acumular puntos de indicadores. Invalidación, expiración y
extensión son estados de elegibilidad separados. La pérdida de elegibilidad
impide nuevas entradas aunque exista una madurez máxima histórica.

La resistencia de ruptura y el mínimo de invalidación deben fijarse antes
del evento. Los pivotes solo se conocen al completarse sus barras de
confirmación. Un nuevo mínimo anterior a la entrada invalida o reinicia el
candidato; no desplazar retroactivamente su referencia.

## Confluencia Fibonacci

Agregar evidencia cuando el precio actual pertenece tanto a una zona de
soporte estructural vigente como a una banda de retroceso Fibonacci válida.
Usar un impulso local LONG confirmado, mínimo anterior al máximo, disponible
antes de evaluar el retroceso. Para el intervalo 50–61.8%, los límites son
`high - 0.618 * (high - low)` y `high - 0.50 * (high - low)`.

Registrar anclas, fechas de confirmación, banda, soporte coincidente y
vigencia. Un Fibonacci de un tramo descendente no equivale automáticamente a
soporte de retroceso LONG. Un soporte roto necesita recuperar su función.

La confluencia aumenta prioridad entre candidatos elegibles comparables; no
promueve CT1 a CT2, no elimina el stop y no compensa un R/R insuficiente. Los
pesos numéricos quedan sujetos a validación, sin optimizarlos solo para HUT.

## Evidencia SHORT solicitada

Evaluar separadamente pérdida de soporte diario, precio por debajo de EMA8,
SMA21 y SMA50 diarias, y debajo de VWAP anclado a un pivote identificado.
Exigir debilidad confirmada con rechazo/retesteo fallido o continuación
bajista. RVOL debe corresponder al tramo vendedor: volumen alto aislado no
determina dirección. Normalizar por sesión/franja horaria y documentar el
umbral antes del replay. Datos faltantes significan evidencia no disponible.

El contexto anterior a 1.9 no recibía barras diarias crudas. Se agregó
`daily_bars` y su alimentación por el ensamblado para estos filtros; las medias
diarias no se sustituyen por medias 4H ni se importa otro motor desde SHORT.

## Objetivos y bloqueos obligatorios

Para SHORT, seleccionar el soporte vigente más cercano estrictamente debajo
de la entrada. Para LONG, seleccionar la resistencia vigente más cercana
estrictamente encima. No saltar un obstáculo cercano para mejorar el R/R.
La ausencia de objetivo válido produce `NO_VALID_DIRECTIONAL_TARGET`, sin
objetivo operable ni madurez de entrada; no inventar un objetivo simétrico.

- LONG: `stop < entry < target`; R/R = `(target-entry)/(entry-stop)`.
- SHORT: `target < entry < stop`; R/R = `(entry-target)/(stop-entry)`.
- Rechazar igualdad, distancias no positivas y R/R <= 1.5.
- Validar en evaluación y al emitir una señal operable; cualquier futura
  integración con órdenes debe validar de nuevo con el precio efectivo.
- Mostrar `INELIGIBLE` y el motivo cuando falle geometría. No etiquetar un
  objetivo incoherente como objetivo de trade.

## Reconstrucción y verificación requerida

Permitir reconsiderar BUILDING cuando una cadena esté obsoleta y haya una
alternativa reciente confirmada. No reconstruir solo para producir una compra.
Conservar trazabilidad de la cadena anterior y motivos de sustitución.

Antes de activar: pruebas de geometría LONG/SHORT, ausencia de objetivos,
medias/VWAP/RVOL faltantes, confluencia sin reacción, pivotes aún no confirmados,
recuperaciones fallidas y cadenas BUILDING vigentes. Corregir/verificar el
cierre de la última vela de 15 minutos de RTH y el bloque 4H parcial final.

Repetir HUT con barras completas y contexto diario causal, además de controles
JACK/LEU/DGII y muestras de rebotes fallidos. Medir hora de primera elegibilidad,
falsas entradas, stops, MAE/MFE y resultado después de costes. Separar arranque
en frío de restauración de estado persistido. Registrar nueva implementación,
reglas y definición inmutable mediante MarketBotAssembly antes de activación.
