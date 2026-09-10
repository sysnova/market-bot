-- Local PostgreSQL: legacy payloads remain LONG; new SHORT stops sit above entry.
ALTER TABLE market_bot.entry_opportunities
  DROP CONSTRAINT entry_opportunities_levels_check;
ALTER TABLE market_bot.entry_opportunities
  ADD CONSTRAINT entry_opportunities_levels_check CHECK (
    zone_low <= zone_high and ((coalesce(payload->>'trade_side', 'LONG') = 'LONG' and invalidation < zone_low) or (coalesce(payload->>'trade_side', 'LONG') = 'SHORT' and invalidation > zone_high))
  );
