# Entry Watcher

## Versiones

- `EntryWatcherV1` / `EntryWatcher`: confirmación multi-horizonte `1.0.0` original.
- `EntryWatcherV2`: versión activa `2.0.0`. Requiere un setup Swing favorable reconocido y
  un trigger Intraday alcista nominal (`bullish_breakout`, `bullish_vwap_reclaim` o
  `bullish_entry_confirmation`).

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
