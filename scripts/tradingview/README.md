# MarketBot Geometry Viewer

The exporter downloads completed split-adjusted `1Day` and regular-session `15Min`
bars directly from Alpaca. It aggregates completed 4H RTH segments locally and uses the
pure engines selected by the current MarketBot definition to calculate:

- daily Swing zone, support, invalidation, resistance, liquidity, target and AVWAPs;
- SwingTrade impulse, Fibonacci 61.8%-50% entry zone, 161.8% extension, 20-day
  support/resistance and targets;
- Swing Channel 4H pivots A/B/C, slope, width, diagonal support zone, middle,
  resistance and invalidation;
- 4HGERI active structural level, zone and invalidation when geometry is available.

For visualization, a reversed absolute high/low does not suppress Fibonacci. If the
operational LONG selector rejects that ordering, the exporter asks the same configured
SwingTrade engine for the widest causal low-to-later-high geometry. This preserves the
configured Fibonacci ratios, ATR, 20-day levels and targets while remaining independent
from maturity state.

It does not connect to NATS/JetStream, read prior assessments or reconstruct
Entry Watcher / Entry Opportunity state.

The CSV preserves every calculated Channel 4H geometry after a support breach. An
invalidated channel remains projected in Pine with dashed orange lines, its latest
support/middle/resistance values, and the `INVALIDATED` maturity and reason. This is
historical geometry for evaluating the prior thesis, not a current buy area. Fields
remain zero only when the engine could not establish structural A/B/C geometry at all.
A GERI resistance remains available as a fuchsia structural line but is never filled as
a purple LONG buy zone; only valid GERI support geometry is filled.

MarketBot `7.39.0` uses Swing Channel 4H `1.2.0`. Channel pivots A and B must be at
least eight completed 4H bars apart, contain a material ATR-normalized impulse and
pullback, and preserve the projected support line between both pivots. Nearby local
lows are not accepted as a channel even when their short sample has high containment.

The Pine viewer maps the Channel 4H pivot timestamps to the bars visible in the open
chart. It then draws parallel diagonal lines from pivot A through the support projected
by MarketBot at the latest completed 4H observation, and extends that channel to the
right. The exported support, middle and resistance are therefore the latest cross
section of that diagonal channel, not independent horizontal levels.

From the repository root:

```bash
uv run python scripts/export_marketbot_tradingview.py HUT XLI
```

Paste the emitted header and rows into `marketbot_operational_viewer.pine`. The Pine
script selects the row matching the open chart ticker. Re-export the CSV whenever you
want a fresh Alpaca snapshot.

If a module cannot calculate valid geometry, its numeric fields remain `0` and the CLI
writes the exact rejection reason to stderr. A missing layer is therefore explicit and
is never replaced with an invented price.

## Colors

- Swing zone: green; support: aqua; invalidation: red; resistance: purple;
  liquidity: fuchsia; target: green.
- Pivot AVWAP: yellow; breakout AVWAP: orange.
- SwingTrade Fibonacci zone: blue; impulse bounds: gray/silver; 161.8% extension:
  teal.
- Swing Channel 4H: orange.
- 4HGERI zone: purple; active support: aqua; active resistance: fuchsia.
