# SHORT observation design, 2026-09-26

## Scope revision: v1.5 / definition 7.79.0

The user owns ETF execution and risk management. In v1.5 the only ETF gate is
its current OrderFlowState buyer regime (BUY_PRESSURE or BUY_ABSORPTION, correct
symbol, non-future, at most four seconds old). No additional confidence/quality,
quote, spread, instrument analysis or instrument R/R checks apply. A price rise
cannot substitute for buyer flow. Underlying gates remain unchanged. This is
still an observation-only route; the default assembly remains 7.77.0.

The design below describes v1.4, retained for rollback; its ETF requirements
are superseded for v1.5.

## Original v1.4 design

The daily SHORT route retains v1.3 behavior. A new v1.4 implementation adds a
separate tactical observer; it cannot emit a LocalAlert or EntrySignal. Its
diagnostic contract fixes `mode=OBSERVE` and `orders_enabled=false`.

Tactical timing requires a completed mature bearish Intraday analysis. Swing is
context, not a directional veto. Fresh structural evidence caps the objective at
the nearest support below spot; recent confirmed support reactions remain a veto.
Current underlying bid and ETF ask determine remaining reward/risk. ETF stop and
target must come from its own analysis; no synthetic 2x price conversion or fixed
3% stop is used. Initial minimum remaining R/R is 1.0 after the current spread,
an observation hypothesis requiring out-of-sample validation, not an optimized rule.

One immutable intent per 15-minute impulse retains its original levels. Duplicate
or consecutive analyses cannot move its stop or create another identity. Reaching
stop, target, expiry or session close makes the intent terminal. No restart can
turn the observation into a purchase because there is no purchase publication path.

Quotes are a separate additive contract from trade-derived OrderFlowState. The
new Order Flow implementation emits a bounded latest quote snapshot (at most one
per second per tracked symbol) without rewriting causal trade evidence. It records
market, provider receipt and publication timestamps. The observer records receipt
and evaluation times. Quote freshness stays at two seconds; spread stays at 35 bps.
Trade-derived evidence is never rejuvenated by a quote-only update.

The composition consumes these contracts and persists observation reports under
new subjects; existing subjects and Redis pending intents are untouched. The new
immutable assembly is explicitly selected for testing; the configured default
remains unchanged until historical and paper observation validation is reviewed.

Validation compares daily and tactical decisions, full gate failure counts,
signal availability after bar close, next executable prices, duplicate suppression,
costs and stop/target order. OHLCV cannot establish ETF quote freshness or fills:
missing quotes must remain unavailable, never synthesized from minute bars.

## Daily SHORT scope revision: v1.6 / definition 7.80.0

The same buyer-regime-only instrument confirmation now applies to the retained
daily SHORT. Underlying daily thesis, timing, support, invalidation and expiry
are unchanged. ETF quote quality, spread, price rise and cached prior entry
cannot replace the current buyer regime. LONG keeps its previous behavior.

Compatibility review: add instrument_confirmation_basis to the assessment,
defaulting to EXECUTABLE_QUOTE for old payloads. BUYER_REGIME explicitly permits
confirmation without an executable quote, still requiring buyer flow and
structure evidence. Such assessments are analytical only and the entry adapter
must not turn them into ETF paper trades or fabricate instrument risk levels.
Existing state enums, subjects and old definitions remain available. Deploy
updated contract consumers together; older strict readers may reject the new
optional field. No runtime definition is promoted by this change.
