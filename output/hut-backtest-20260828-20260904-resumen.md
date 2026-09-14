# HUT — validación Swing Trade, 28/08–04/09/2026

MarketBot 7.50.0 / Swing Trade 1.7.0 / reglas 1.4.0. Datos Alpaca SIP, ajuste split. Replay aislado de 2.842 barras de un minuto.

**Resultado: cero compras ST3/ST4.** El motor rechazó las 156 evaluaciones por `SwingTrade LONG impulse requires low before high`; no produjo assessments y no llegó a evaluar la nueva confirmación del rebote. Ninguna otra familia emitió compras confirmadas en esta corrida.

## Verificación adicional con ventana diaria actualizada

El backtester general conserva el contexto diario inicial. Para comprobar que el rechazo no dependiera de esa limitación, se recalcularon por separado las 60 ruedas disponibles antes de abrir cada sesión, sin usar la barra diaria aún en formación. No se alteró el motor ni se ejecutó otro replay completo.

| Sesión | Mínimo | Fecha mínimo | Máximo | Fecha máximo | Impulso LONG válido |
|---|---:|---|---:|---|---|
| 2026-08-28 | 76.03 | 2026-08-19 | 135.31 | 2026-06-03 | No |
| 2026-08-31 | 76.03 | 2026-08-19 | 130.58 | 2026-06-04 | No |
| 2026-09-01 | 75.71 | 2026-08-31 | 129.88 | 2026-06-22 | No |
| 2026-09-02 | 75.3096 | 2026-09-01 | 129.88 | 2026-06-22 | No |
| 2026-09-03 | 75.3096 | 2026-09-01 | 129.88 | 2026-06-22 | No |
| 2026-09-04 | 75.3096 | 2026-09-01 | 129.88 | 2026-06-22 | No |

En las seis sesiones el máximo antecede al mínimo. Por lo tanto, la regla vigente rechaza el impulso también con actualización diaria. Esto no permite medir la eficacia de ruptura/retesteo ni del stop del rebote: el descarte ocurre antes. Tampoco significa que no pueda existir un rebote local más reciente; la regla operativa usa los extremos absolutos de 60 ruedas.

JSON del replay: `.runtime/backtests/hut-20260828-20260904-v1750.json`. Auditoría diaria: `.runtime/backtests/hut-rolling-impulse-audit.json`. El reloj interno del replay desplaza fechas siete días; las fechas de este documento corresponden al mercado original.
