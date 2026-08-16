# Swing Channel 4H engine ownership

- Own deterministic ascending-channel geometry over completed RTH `4Hour` bars.
- Consume contracts only; never fetch data, persist state, publish events, or place orders.
- Keep the shadow maturity independent from core Entry Opportunity L1-L4.
- Use `Decimal` arithmetic and confirmed pivots only; never use future bars to confirm a live pivot.
- Add a failing unit test before changing geometry or maturity rules.
