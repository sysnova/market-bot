# PostgreSQL: oportunidades negativas y replay Swing Trade

Consulta de solo lectura a PostgreSQL local (`marketbot`, esquema `market_bot`). Se revisaron 318 oportunidades: 222 cerradas y 96 activas. Instantánea de datos actualizada al 04/09/2026, aproximadamente 14:27 ET; no se sustituyeron esas marcas por cotizaciones actuales.

## Qué pérdidas corresponden a compras

Las únicas legs/checkpoints de compra confirmada encontrados con resultado inferior a −5% son JACK (−8,58%) y HPE (−6,46%), ambos Swing Trade y cerrados. No se encontraron otras compras abiertas con pérdida superior al 5%. JACK ya se había probado por separado.

Los otros siete símbolos seleccionados presentan caídas de referencias ARMED/IN_ZONE. El estado OPEN de un checkpoint de seguimiento no implica que se haya abierto una compra. RTX tiene una oportunidad OPEN, pero su −7,92% corresponde a la referencia ARMED, no a su compra abierta.

## Resultado del backtest

Misma ventana que JACK: 28/08–04/09/2026, ambas fechas inclusive. MarketBot 7.50.0 / Swing Trade 1.7.0 / reglas 1.4.0 / Entry Opportunity 11.0.0. Alpaca SIP, ajuste split, 21.737 barras de un minuto; estado y bus en memoria, sin modificar PostgreSQL ni colocar órdenes.

**Cero compras Swing Trade ST3/ST4 en los ocho símbolos.**

| Símbolo | Caída en PostgreSQL | Naturaleza | Compras ST3/ST4 | Evaluación |
|---|---:|---|---:|---|
| HPE | -6.46% | Compra cerrada | 0 | Sin confluencia entre soporte y Fibonacci; no llega a ST2. Algunos intentos también superan el límite de riesgo. |
| AEIS | -17.06% | Referencia de seguimiento | 0 | Impulso LONG rechazado: el mínimo de las 60 ruedas aparece después del máximo; no genera assessment. |
| AXSM | -5.56% | Referencia de seguimiento | 0 | Impulso LONG rechazado: el mínimo de las 60 ruedas aparece después del máximo; no genera assessment. |
| BA | -9.03% | Referencia de seguimiento | 0 | Sin tesis válida: falla ubicación/estructura y no confirma rechazo. |
| CRWV | -6.09% | Referencia de seguimiento | 0 | Impulso LONG rechazado: el mínimo de las 60 ruedas aparece después del máximo; no genera assessment. |
| DKNG | -8.21% | Referencia de seguimiento | 0 | Impulso LONG rechazado: el mínimo de las 60 ruedas aparece después del máximo; no genera assessment. |
| MRAM | -6.03% | Referencia de seguimiento | 0 | Impulso LONG rechazado: el mínimo de las 60 ruedas aparece después del máximo; no genera assessment. |
| RTX | -7.92% | Referencia de seguimiento | 0 | Como máximo ST1; sin confluencia soporte/Fibonacci ni confirmación del rebote. |

HPE: la compra histórica fue el 01/09 a las 10:46 ET, en 51,0995, y cerró el 03/09 a las 09:30 ET en 47,7971 (−6,46%). En el replay, la vela del 01/09 a las 10:45 tiene exactamente 51,0995 y queda en ST1: Fibonacci 49,3960–52,0775 y banda de soporte 45,6106–46,9894 no se intersectan. Esta corrida no reproduce aquella compra.

El 03/09 a las 15:30 ET, HPE registra además un intento con riesgo operativo 4,7207%, superior al máximo 4%, y queda bloqueado. Los motivos acumulados pueden referirse a distintos candidatos dentro de una misma evaluación; no todos los rechazos de HPE se explican por ese límite.

## Alcance de la conclusión

- AEIS, AXSM, CRWV, DKNG y MRAM suman 780 rechazos de validación del impulso, con cero assessments. Se verificó por separado el orden de sus extremos diarios. No puede atribuirse su ausencia de compras a los nuevos filtros de aceptación o stop.
- HPE, BA y RTX sí generaron 156 assessments cada uno, incluyendo inicialización, y ninguno alcanzó ST3/ST4.
- El backtester general conserva el contexto diario inicial durante el replay. Por tanto, esto no demuestra que la nueva versión por sí sola habría evitado las pérdidas observadas en producción: falta una comparación controlada con actualización diaria completa e idénticas condiciones.
- La historia 4H de calentamiento de este backtester es más corta que la del runtime. Su ausencia no funciona como veto en Swing Trade 1.7.0.
- Otros motores sí emitieron señales: BA tuvo siete confirmaciones GERI countertrend CT2 y DKNG una confirmación Signal Fusion. No son compras de Swing Trade.
- El JSON bruto desplaza los timestamps siete días por el reloj del simulador; las fechas descritas aquí son las de origen.

## Archivos

- `.runtime/backtests/negative5-20260828-20260904-v1750.json`: resultado íntegro.
- `.runtime/backtests/negative5-comparison.json`: comparación resumida.
- `.runtime/backtests/negative5-invalid-impulses.json`: extremos diarios de los cinco símbolos rechazados.
- `.runtime/backtests/opportunities-all-snapshot.json`: instantánea PostgreSQL de lectura.
