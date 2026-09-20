ALTER TABLE market_bot.entry_opportunities
  DROP CONSTRAINT entry_opportunities_levels_check;

ALTER TABLE market_bot.entry_opportunities
  ADD CONSTRAINT entry_opportunities_levels_check CHECK (
    zone_low <= zone_high
    AND (
      (
        coalesce(payload->>'trade_side', 'LONG') = 'LONG'
        AND (
          (
            payload->>'primary_signal_family' = 'SWING_TRADE'
            AND invalidation < original_price
          )
          OR (
            coalesce(payload->>'primary_signal_family', '') <> 'SWING_TRADE'
            AND invalidation < zone_low
          )
        )
      )
      OR (
        coalesce(payload->>'trade_side', 'LONG') = 'SHORT'
        AND invalidation > zone_high
      )
    )
  );
