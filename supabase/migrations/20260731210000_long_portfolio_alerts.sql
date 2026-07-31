-- Persist immutable LONG portfolio alerts before they are published to operators.
create table market_bot.long_portfolio_alerts (
  id uuid primary key,
  deduplication_key text not null unique,
  symbol text not null,
  created_at timestamptz not null,
  expires_at timestamptz,
  rule_version text not null,
  horizon_end date not null,
  current_price numeric(28, 8) not null,
  buy_zone_low numeric(28, 8) not null,
  buy_zone_high numeric(28, 8) not null,
  invalidation numeric(28, 8) not null,
  target_weight_percent numeric(12, 4) not null,
  target_capital_usd numeric(28, 2) not null,
  tranche_percent numeric(12, 4) not null,
  tranche_usd numeric(28, 2) not null,
  suggested_whole_shares numeric(28, 0) not null,
  score numeric(7, 2) not null,
  reasons jsonb not null,
  payload jsonb not null,
  persisted_at timestamptz not null default now(),
  constraint long_portfolio_alerts_levels_check
    check (invalidation < buy_zone_low and buy_zone_low <= buy_zone_high),
  constraint long_portfolio_alerts_weight_check
    check (target_weight_percent > 0 and target_weight_percent <= 100),
  constraint long_portfolio_alerts_tranche_check
    check (tranche_percent > 0 and tranche_percent <= 100),
  constraint long_portfolio_alerts_money_check
    check (target_capital_usd > 0 and tranche_usd > 0),
  constraint long_portfolio_alerts_score_check
    check (score >= 0 and score <= 100)
);

create index long_portfolio_alerts_symbol_created_idx
  on market_bot.long_portfolio_alerts (symbol, created_at desc);

create trigger long_portfolio_alerts_immutable
  before update or delete on market_bot.long_portfolio_alerts
  for each row execute function market_bot.prevent_mutation();

grant select, insert on market_bot.long_portfolio_alerts to market_bot_runtime;

alter table market_bot.long_portfolio_alerts enable row level security;
alter table market_bot.long_portfolio_alerts force row level security;

create policy long_portfolio_alerts_runtime_select
  on market_bot.long_portfolio_alerts for select to market_bot_runtime using (true);
create policy long_portfolio_alerts_runtime_insert
  on market_bot.long_portfolio_alerts for insert to market_bot_runtime with check (true);
