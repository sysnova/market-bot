# Countertrend con EMA21/SMA50 — MarketBot 7.53.0

Implementación en Windows: GERI 1.11.0, reglas 1.5.0, Entry Opportunity 13.0.0.
Comparación anterior: MarketBot 7.52.0 / GERI 1.10.0. Sin despliegue en WSL.

## Regla elegida

La EMA21 diaria y la SMA50 diaria son primeros objetivos obligatorios cuando están
por encima del precio. Se utiliza la más cercana, o una resistencia diaria reciente
aún más cercana. El R/R debe ser **mayor que 1,50**; nunca se elige una media más
lejana para mejorar artificialmente el cociente.

Una resistencia diaria reciente es un máximo pivote confirmado dentro de las
últimas 20 ruedas, sin un cierre diario posterior que haya recuperado ese nivel.
Los mínimos antiguos y pivotes menores de 4H aparecen como niveles intermedios;
dejan de tener automáticamente la misma prioridad que estas resistencias y medias.
Esta clasificación también explica el cambio frente a la versión anterior.

Se requieren al menos 50 cierres diarios completos. La EMA21 se inicia con el
promedio de los primeros 21 cierres disponibles; la SMA50 usa los últimos 50.
La aceptación 15m requiere RVOL >= 1,20 frente al mismo horario de cinco sesiones
anteriores. Continúan CT2+ como madurez de entrada, riesgo máximo 4%, stop bajo el
mínimo de aceptación, límites de extensión y cinco ruedas de vida de la tesis.

El stop y objetivo de cada ventana se congelan. Las medias del nuevo cierre diario
se actualizan para evaluar entradas nuevas, sin modificar una posición simulada
ya abierta. Se conserva la procedencia del objetivo incluso después de serializar
y restaurar el assessment. Los estados de recuperación/retest de medias son
contexto explicativo; no sustituyen la madurez CT ni generan compras por sí solos.

## HUT: 28/08–04/09/2026

**Dos operaciones simuladas, ambas cerradas por objetivo.** Horarios de Nueva York.

| Entrada | Madurez | Precio | Stop | Objetivo | R/R inicial | Salida por objetivo | P/L bruto |
|---|---|---:|---:|---:|---:|---|---:|
| 02/09 14:30 | CT2 | 80,7550 | 78,4061 | EMA21: 86,5590 | 2,4710 | 03/09 11:33 | +7,1872% |
| 03/09 13:00 | CT4 | 87,1600 | 85,8061 | Máximo diario: 90,2400 | 2,2750 | 04/09 10:45 | +3,5337% |

La primera aceptación tiene RVOL 4,1197. La EMA21 procede del cierre del 01/09 a
las 16:00 ET; la SMA50 de ese momento es 97,6474. El antiguo objetivo 81,55 queda
visible como nivel intermedio. La confirmación CT3 del 03/09 a las 09:45 es un
checkpoint de la misma posición, **no una tercera operación**.

La segunda entrada tiene RVOL 1,6039. La EMA21 vigente es 86,0189, por debajo del
precio; la SMA50 es 96,8496. El máximo diario de 90,24 queda antes de la SMA50 y
limita el objetivo. La primera posición ya estaba cerrada cuando aparece la CT4.

MAE/MFE de la primera operación: −2,0370% / +7,2008%.
MAE/MFE de la segunda: −1,1443% / +3,5337%.
No se suman los porcentajes como rendimiento de cartera: no se definieron capital
ni tamaño de posición.

## Controles y límites

| Ticker | Velas 15m evaluadas | Señales CT2+ | Posiciones simuladas abiertas durante el período |
|---|---:|---:|---:|
| HUT | 156 | 3 | 2 |
| JACK | 156 | 0 | 0 |
| LEU | 156 | 0 | 0 |
| DGII | 155 | 0 | 0 |

Histórico Alpaca SIP ajustado por splits, con calentamiento previo de hasta 600
velas 15m y 100 diarias. Decisiones al cierre 15m; seguimiento de salidas con velas
de un minuto procesadas cronológicamente antes de cada decisión siguiente.
Entry Opportunity se ejecutó en memoria, sin órdenes ni escrituras operativas.
El arranque se reconstruyó desde histórico, sin restaurar el estado de producción.
DGII tiene una vela 15m ausente.

Los precios de entrada suponen ejecución al cierre que confirma la señal y las
salidas al nivel objetivo. El P/L es hipotético y bruto, sin comisiones, spread ni
slippage. No se validó ejecución real ni entrada al siguiente precio negociable.
Este período fue usado para diseñar el cambio; sus dos aciertos no son validación
fuera de muestra ni demuestran rentabilidad general. La agregación diaria actual
requiere 26 velas RTH completas y no cubre sesiones reducidas.

## Reproducción local

Validación: **1.354 pruebas aprobadas, 4 omitidas; cobertura 78,37%** (mínimo 77%).
Ruff y Pyright sin errores; comprobación de espacios del diff aprobada. Las pruebas
cubren objetivo obligatorio, rechazo por R/R o volumen, datos diarios incompletos,
resistencia diaria más cercana, seguimiento CT1 y preservación de niveles después
de rotar el historial y restaurar desde JSON.

- Replay: `uv run --no-sync python .runtime/backtests/replay_averages.py`.
- Datos: `.runtime/backtests/recovery-history.json` y `recovery-minute-history.json`.
- Traza y oportunidades: `.runtime/backtests/recovery-7.53.0.json`.
- Pruebas: `.runtime/recovery-7.53.0-pytest.log`.
- Los archivos `.runtime` son evidencia local ignorada por Git.

La versión 7.52.0 sigue disponible para comparación y rollback. SHORT conserva su
evaluación separada y su geometría bajista.
