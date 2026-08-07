# Entry Watcher

## Versiones

- `EntryWatcherV1` / `EntryWatcher`: confirmación multi-horizonte `1.0.0` original.
- `EntryWatcherV2`: versión activa `2.0.0`. Requiere un setup Swing favorable reconocido y
  un trigger Intraday alcista nominal (`bullish_breakout`, `bullish_vwap_reclaim` o
  `bullish_entry_confirmation`).
- `EntryWatcherV3`: conserva la continuación breakaway versionada en `3.0.0`.
- `EntryWatcherV4`: versión activa `4.0.0`. Requiere Intraday v4 eficiente, `strong`, higher low
  de cinco minutos y una segunda confirmación fresca al menos tres minutos después. Tras disparar,
  no rearma otra zona Long del mismo símbolo durante 30 minutos, evitando L4 duplicadas.

The Entry Watcher preserves a Long entry thesis across later market evaluations. It freezes
the original buy zone, invalidation, expected correction, source analysis, and expiry instead
of recalculating those levels away when price finally pulls back.

An opportunity starts as `ARMED`, moves to `IN_ZONE` when price reaches the original area,
and becomes `TRIGGERED` when fresh Long, Swing, and Intraday analyses agree. SEC dilution evidence
is attached as a warning and never gates or invalidates an entry. Explicit Long structural failure
or a breach of the original invalidation changes it to `INVALIDATED`; elapsed time changes it to
`EXPIRED`.

En V2 una perforación intradiaria aislada no invalida la tesis congelada. La invalidación por
precio debe confirmarse en el análisis Long, o Long debe emitir dirección bajista/`AVOID`.

The engine is analysis-only. It emits transitions for human alerts and has no order port.

Entry Watcher stops at detection and publishes its durable transitions. Paper-trade progression,
L1-L4 checkpoints, horizon legs, and gain/loss auditing belong to the independent
`EntryOpportunityEngine`; see `app/entry_opportunity_engine/README.md`.

## Breakaway continuation after a zone touch

V3 remembers the latest real zone touch for 72 hours so the thesis can survive the next session's
open and a weekend. If price moves above the frozen zone before full confirmation arrives, it
emits `breakaway_continuation_pending` and keeps the thesis eligible only when:

- extension is within both 4% and 0.75 ATR from the frozen zone high;
- Swing v3 remains bullish and retains its anchored-VWAP gate, even if acceleration has already
  classified it as `extended`/`CAUTION`;
- Intraday v3 confirms a valid breakout, VWAP reclaim, or entry-confirmation setup;
- reward/risk recalculated from the live price, nearest invalidation, and published target is >=2.

The final state is still `TRIGGERED`; its reasons distinguish
`breakaway_continuation_confirmed` from ordinary in-zone confirmation. Beyond the chase cap the
watch returns to `ARMED` with `continuation_chase_cap_exceeded` and waits for a retest. Human alerts
render these stages as `ENTRY IN_ZONE EARLY WATCH`, `ENTRY BREAKAWAY WATCH`, and
`ENTRY EXTENDED WAIT`.

En V4 esa continuación tampoco puede confirmar sobre un impulso extendido: primero debe volver a
la ventana eficiente de Intraday y formar el retest/higher low. La primera lectura madura prepara
la confirmación; la segunda lectura fresca puede disparar `ENTRY TRIGGERED`.
