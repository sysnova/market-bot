# Entry Watcher

## Versiones

- `EntryWatcherV1` / `EntryWatcher`: confirmación multi-horizonte `1.0.0` original.
- `EntryWatcherV2`: versión histórica `2.0.0`. Requiere un setup Swing favorable reconocido y
  un trigger Intraday alcista nominal (`bullish_breakout`, `bullish_vwap_reclaim` o
  `bullish_entry_confirmation`).
- `EntryWatcherV3`: conserva la continuación breakaway versionada en `3.0.0`.
- `EntryWatcherV4`: versión de replay `4.0.0`. Requiere Intraday v4 eficiente, `strong`, higher low
  de cinco minutos y una segunda confirmación fresca al menos tres minutos después. Tras disparar,
  no rearma otra zona Long del mismo símbolo durante 30 minutos, evitando L4 duplicadas.

`EntryWatcherV5` (`5.0.0`) is the active no-retest continuation policy. It preserves every V4
anti-chase gate and may confirm L4 without a prior Long-zone touch after two fresh, strong,
price-efficient Intraday readings report a five-minute higher low and live reward/risk is at least 2.

`EntryWatcherV52` (`5.2.0`) limits initial radar creation to non-extended Long candidates
scoring at least 50 and no more than 4% and 2 ATR above the frozen zone. These limits affect
only the initial `ARMED`; once a qualified watch exists, the existing zone, continuation,
confirmation, invalidation, and expiry policies remain unchanged.

`EntryWatcherV53` (`5.3.0`) preserves V5.2 initial `ARMED`, `IN_ZONE`, frozen levels, and tracking
without admitting any new radar candidate. It changes only final confirmation: the first Intraday
result that already passes every mature, strong, five-minute higher-low, price-efficiency,
Swing anchored-VWAP, freshness, extension, and live reward/risk gate moves the existing watch to
`TRIGGERED`. That analysis price becomes the real L4 entry; there is no intermediate `CONFIRMING`
state and no second three-minute reconfirmation delay.

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

## No-retest higher-low continuation

V5 measures a no-touch continuation from the current Intraday trigger instead of the frozen Long
zone. The first mature higher-low reading arms the candidate; a distinct mature reading after the
configured delay may produce `ENTRY TRIGGERED` with
`no_retest_higher_low_continuation_confirmed`. The Intraday efficiency cap, extension cap, fresh
Long/Swing evidence, anchored-VWAP gate, and live reward/risk gate remain mandatory.

V5.3 keeps those safety gates but treats a fully mature first reading as the final confirmation,
so L4 records the actionable price instead of waiting for a later duplicate analysis.

En V4 esa continuación tampoco puede confirmar sobre un impulso extendido: primero debe volver a
la ventana eficiente de Intraday y formar el retest/higher low. La primera lectura madura prepara
la confirmación; la segunda lectura fresca puede disparar `ENTRY TRIGGERED`.
