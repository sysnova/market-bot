# Entry Opportunity Engine

`EntryOpportunityEngine v1` is the lifecycle and paper-trade engine for candidate buys. It
merges Entry Watcher transitions, L1-L4 alerts, analysis results, and final one-minute bars
into one active opportunity per ticker while preserving the original thesis.

It owns maturity progress, horizon legs, invalidation/session closure, gain/loss, MFE/MAE,
and immutable audit events. In distributed operation it runs as `entry-opportunity-v1` and
publishes `marketbot.v1.entry-opportunity.event.>` for Alert Engine and the confirmed-buy
monitor.

Commands:

```bash
uv run marketbot entry-opportunity serve
uv run marketbot entry-opportunity report
```
