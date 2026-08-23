# Intraday Opportunity Engine

Lifecycle paper-only para round trips intraday. Registra fills conservadores contra
bid/ask, P/L bruto y neto, MFE/MAE, stop, target, vencimiento temporal y cierre de
rueda. Permite múltiples operaciones por símbolo y sesión, con un máximo de una
posición activa por `symbol + strategy_id`.

No contiene adaptadores de broker ni envía órdenes.
