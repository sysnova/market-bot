-- Cleanup archives contain full opportunity snapshots and must follow the same
-- deny-by-default security posture as every other market_bot table.

revoke all on table market_bot.entry_opportunity_cleanup_archive_20260814
    from public, anon, authenticated, service_role, market_bot_runtime;
revoke all on table market_bot.entry_opportunity_event_cleanup_archive_20260814
    from public, anon, authenticated, service_role, market_bot_runtime;

alter table market_bot.entry_opportunity_cleanup_archive_20260814
    enable row level security;
alter table market_bot.entry_opportunity_cleanup_archive_20260814
    force row level security;

alter table market_bot.entry_opportunity_event_cleanup_archive_20260814
    enable row level security;
alter table market_bot.entry_opportunity_event_cleanup_archive_20260814
    force row level security;
