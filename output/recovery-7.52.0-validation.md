# Recuperación progresiva — MarketBot 7.52.0

Implementada y validada en el checkout Windows. GERI 1.10.0, reglas 1.4.0 y Entry Opportunity 13.0.0. La versión 7.51.0 / GERI 1.9.0 permanece disponible para comparación y rollback.

## Qué cambia

La madurez estructural continúa después de tocar una resistencia. Cada ruptura con cierre superior al nivel + 0,10 ATR y aceptación en una vela 15m posterior consecutiva puede renovar la entrada. El mínimo de aceptación debe superar al de la ruptura; el nuevo stop debe ser superior al anterior. Se conservan el piso estructural, la aceptación original y el TTL de la tesis.

Cada nueva ventana registra su propia aceptación, secuencia, stop, objetivo y setup ID. Las posiciones simuladas abiertas conservan sus niveles. Una entrada rechazada o un objetivo intermedio alcanzado no se confunden con el cierre de una operación. Una mecha aislada no habilita una compra. La pérdida del piso estructural o la expiración sí terminan la tesis.

## HUT: replay del 28/08 al 04/09/2026

**Resultado: 0 entradas. El bloqueo en 81,55 está corregido, pero los objetivos cercanos siguen impidiendo un R/R superior a 1,5.** El 02/09 se conservan CT2 a las 14:30 ET y CT3 al cierre. El 03/09 a las 10:45 ET aparece CT4 y se renueva la entrada por primera vez. Se evalúan seis renovaciones en total.

| Hora Nueva York | Madurez | Precio de aceptación | Nivel recuperado | Nuevo stop | Objetivo | R/R |
|---|---|---:|---:|---:|---:|---:|
| 2026-09-02 14:30 | CT2 | 80.7550 | 79.9900 | 78.4061 | 81.5500 | 0.3385 |
| 2026-09-03 10:45 | CT4 | 83.4300 | 81.5500 | 81.5511 | 83.9400 | 0.2714 |
| 2026-09-03 11:15 | CT4 | 84.9450 | 83.9400 | 82.8261 | 85.0450 | 0.0472 |
| 2026-09-03 11:45 | CT4 | 86.3650 | 85.0450 | 84.5761 | 86.8200 | 0.2544 |
| 2026-09-03 15:30 | CT4 | 88.1950 | 86.8200 | 86.6261 | 88.6900 | 0.3155 |
| 2026-09-04 10:15 | CT4 | 89.4900 | 88.6900 | 88.0761 | 90.2400 | 0.5305 |
| 2026-09-04 15:45 | CT4 | 91.9600 | 90.2400 | 89.3261 | 92.5600 | 0.2278 |

Todas estas aceptaciones fueron rechazadas por `insufficient_reward_risk`. Entre aceptaciones, el motor conserva la madurez y espera que la siguiente resistencia quede aceptada. Al cierre del 04/09 HUT sigue en CT4, pero todavía espera la aceptación de la nueva ruptura sobre 92,56.

La regla pendiente de evaluación estratégica es la clasificación de obstáculos: continúa usando el pivote válido más cercano, incluidos antiguos soportes rotos. No se cambiaron los umbrales ni se saltaron resistencias para fabricar una compra. Este replay no demuestra que cada obstáculo tenga la misma relevancia económica.

## Controles y alcance

| Ticker | Velas 15m | Entradas emitidas | Posiciones con entrada |
|---|---:|---:|---:|
| HUT | 156 | 0 | 0 |
| JACK | 156 | 0 | 0 |
| LEU | 156 | 0 | 0 |
| DGII | 155 | 0 | 0 |

Datos Alpaca SIP ajustados por splits: histórico previo de hasta 600 velas 15m y 100 diarias, más seis sesiones evaluadas al cierre de cada 15m. Se alimentó Entry Opportunity en memoria con velas reales de un minuto anteriores a cada decisión. HUT tiene las 156 velas 15m esperadas; DGII tiene una ausente. Sin NATS/PostgreSQL operativo ni órdenes. Arranque reconstruido desde histórico, no restauración del estado persistido de producción.

No hay rentabilidad, MAE/MFE o tasa de acierto de operaciones que reportar porque no hubo entradas. Las pruebas sintéticas sí cubren una primera CT2 rechazada seguida de una aceptación posterior con compra válida, la ausencia de objetivo, rechazo de falsas aceptaciones, preservación de madurez, restauración y protección de posiciones ya abiertas.

## Evidencia reproducible

Validación final: 1.343 pruebas aprobadas, 4 omitidas; cobertura 78,22% (mínimo 77%).
Ruff y Pyright sin errores. El chequeo de espacios del diff también pasó.

- Replay: `uv run --no-sync python .runtime/backtests/replay_progressive.py`.
- Datos: `.runtime/backtests/recovery-history.json` y `recovery-minute-history.json`.
- Resultado completo: `.runtime/backtests/recovery-7.52.0.json`.
- Traza HUT: `output/hut-countertrend-1.10-backtest-20260828-20260904.csv`.
- Pruebas: `.runtime/recovery-7.52.0-pytest.log`.
- La selección por defecto cambia en Windows. No se desplegó esta versión en WSL.
