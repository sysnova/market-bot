# SwingTrade engine ownership

- Own deterministic LONG Fibonacci SwingTrade calculations, tests, and focused docs.
- Consume only completed normalized bars and stable contracts supplied by integration.
- Never fetch data, write persistence, publish transport messages, or place orders.
- Keep Decimal calculations causal and reproducible; add failing tests before rule changes.
