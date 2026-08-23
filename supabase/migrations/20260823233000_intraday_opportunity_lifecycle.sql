-- Independent paper lifecycle for multiple intraday round trips per session.
create table market_bot.intraday_opportunities (
  id uuid primary key,
  symbol text not null,
  strategy_id text not null,
  session_date date not null,
  side text not null check (side in ('LONG', 'SHORT')),
  status text not null check (status in ('OPEN', 'CLOSED')),
  opened_at timestamptz not null,
  updated_at timestamptz not null,
  expires_at timestamptz not null,
  closed_at timestamptz,
  close_reason text check (close_reason is null or close_reason in (
    'STOP', 'TARGET', 'TIME_EXIT', 'END_OF_DAY', 'FLOW_REVERSAL', 'MANUAL'
  )),
  quantity numeric(28, 8) not null check (quantity > 0),
  entry_price numeric(28, 8) not null check (entry_price > 0),
  current_price numeric(28, 8) not null check (current_price > 0),
  exit_price numeric(28, 8),
  stop_price numeric(28, 8) not null check (stop_price > 0),
  target_price numeric(28, 8) not null check (target_price > 0),
  highest_mark numeric(28, 8) not null check (highest_mark > 0),
  lowest_mark numeric(28, 8) not null check (lowest_mark > 0),
  gross_pnl numeric(28, 8) not null,
  net_pnl numeric(28, 8) not null,
  gross_pnl_percent numeric(16, 8) not null,
  net_pnl_percent numeric(16, 8) not null,
  mfe_percent numeric(16, 8) not null check (mfe_percent >= 0),
  mae_percent numeric(16, 8) not null check (mae_percent <= 0),
  fees_total numeric(28, 8) not null check (fees_total >= 0),
  revision integer not null check (revision >= 1),
  source_signal_id uuid not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  constraint intraday_opportunities_timestamps_check check (
    updated_at >= opened_at and expires_at > opened_at
  ),
  constraint intraday_opportunities_extrema_check check (lowest_mark <= highest_mark),
  constraint intraday_opportunities_levels_check check (
    (side = 'LONG' and stop_price < entry_price and entry_price < target_price)
    or
    (side = 'SHORT' and target_price < entry_price and entry_price < stop_price)
  ),
  constraint intraday_opportunities_closure_evidence_check check (
    (status = 'CLOSED') = (
      closed_at is not null and close_reason is not null and exit_price is not null
    )
  )
);

create unique index intraday_opportunities_one_active_strategy_symbol_idx
  on market_bot.intraday_opportunities (symbol, strategy_id)
  where status = 'OPEN';
create index intraday_opportunities_session_symbol_idx
  on market_bot.intraday_opportunities (session_date, symbol, opened_at);
create index intraday_opportunities_status_expiry_idx
  on market_bot.intraday_opportunities (status, expires_at);

create table market_bot.intraday_fills (
  id uuid primary key,
  opportunity_id uuid not null
    references market_bot.intraday_opportunities(id) on delete restrict,
  source_event_id uuid not null,
  occurred_at timestamptz not null,
  role text not null check (role in ('ENTRY', 'EXIT')),
  action text not null check (action in ('BUY', 'SELL')),
  quantity numeric(28, 8) not null check (quantity > 0),
  price numeric(28, 8) not null check (price > 0),
  fee numeric(28, 8) not null check (fee >= 0),
  payload jsonb not null,
  created_at timestamptz not null default now(),
  unique (opportunity_id, role),
  unique (source_event_id)
);

create index intraday_fills_opportunity_occurred_idx
  on market_bot.intraday_fills (opportunity_id, occurred_at);

create table market_bot.intraday_opportunity_events (
  id uuid primary key,
  source_event_id uuid not null unique,
  opportunity_id uuid not null
    references market_bot.intraday_opportunities(id) on delete restrict,
  symbol text not null,
  strategy_id text not null,
  session_date date not null,
  kind text not null check (kind in ('OPENED', 'MARKED', 'CLOSED')),
  occurred_at timestamptz not null,
  reasons jsonb not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create index intraday_opportunity_events_opportunity_occurred_idx
  on market_bot.intraday_opportunity_events (opportunity_id, occurred_at);
create index intraday_opportunity_events_session_symbol_idx
  on market_bot.intraday_opportunity_events (session_date, symbol, occurred_at);

create trigger intraday_fills_immutable
  before update or delete on market_bot.intraday_fills
  for each row execute function market_bot.prevent_mutation();
create trigger intraday_opportunity_events_immutable
  before update or delete on market_bot.intraday_opportunity_events
  for each row execute function market_bot.prevent_mutation();

grant select, insert, update on market_bot.intraday_opportunities to market_bot_runtime;
grant select, insert on market_bot.intraday_fills to market_bot_runtime;
grant select, insert on market_bot.intraday_opportunity_events to market_bot_runtime;

alter table market_bot.intraday_opportunities enable row level security;
alter table market_bot.intraday_opportunities force row level security;
alter table market_bot.intraday_fills enable row level security;
alter table market_bot.intraday_fills force row level security;
alter table market_bot.intraday_opportunity_events enable row level security;
alter table market_bot.intraday_opportunity_events force row level security;

create policy intraday_opportunities_runtime_select
  on market_bot.intraday_opportunities for select to market_bot_runtime using (true);
create policy intraday_opportunities_runtime_insert
  on market_bot.intraday_opportunities for insert to market_bot_runtime with check (true);
create policy intraday_opportunities_runtime_update
  on market_bot.intraday_opportunities for update to market_bot_runtime
  using (true) with check (true);
create policy intraday_fills_runtime_select
  on market_bot.intraday_fills for select to market_bot_runtime using (true);
create policy intraday_fills_runtime_insert
  on market_bot.intraday_fills for insert to market_bot_runtime with check (true);
create policy intraday_opportunity_events_runtime_select
  on market_bot.intraday_opportunity_events for select to market_bot_runtime using (true);
create policy intraday_opportunity_events_runtime_insert
  on market_bot.intraday_opportunity_events for insert to market_bot_runtime with check (true);
