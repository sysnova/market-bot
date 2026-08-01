# PatreonCaps v1

PatreonCaps is an analysis-only `SHADOW` engine based on the support-confirmation,
multi-timeframe, macro-context, and staged-position concepts summarized from the user's
Patreon Learning Centre collection. Exact ATR distances, scores, and thresholds are
engineering defaults in versioned YAML artifacts; they are intended for replay and calibration
rather than being represented as literal lesson rules.

It consumes final market bars plus versioned Long, Swing, and Intraday `AnalysisResult`
events. It never submits an order and cannot gate the existing alert engines while the
rule remains in shadow mode.

`replay_outcomes()` mide cada transicion BUY contra barras diarias posteriores, nunca contra la
barra que genero la senal. Produce retornos a 5, 20 y 60 ruedas, MFE, MAE, invalidacion y horas
desde `WATCH_ZONE` hasta confirmacion, permitiendo comparar `rule_version` antes de promover v1.

## Versiones

- `1.0.0`: confluencia, confirmacion, alineacion multi-engine y macro.
- `1.1.0` (activa): agrega la leccion consolidada de SMA50/200 diaria y 1H, pendiente,
  Golden/Death Cross, triangulo ascendente, Wave 2 sosteniendo 0.618 y retest del maximo de Wave 1.

En `1.1.0`, la zona puede permanecer en observacion, pero un BUY queda bloqueado si el precio
diario esta bajo SMA200 o aparece un Death Cross diario reciente. La capa Lesson aporta 20% del
Patreon Score; `1.0.0` conserva exactamente los pesos 40/30/30 y no aplica ese gate.
