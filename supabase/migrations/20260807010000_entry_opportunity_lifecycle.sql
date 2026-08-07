-- Consolidated Entry Watcher lifecycle: mutable root plus immutable audit events.
create table market_bot.entry_opportunities (
  id uuid primary key,
  symbol text not null,
  status text not null check (status in ('ARMED', 'IN_ZONE', 'CONFIRMING', 'OPEN', 'CLOSED')),
  current_maturity text not null check (current_maturity in ('ARMED', 'IN_ZONE', 'L1', 'L2', 'L3', 'L4')),
  peak_maturity text not null check (peak_maturity in ('ARMED', 'IN_ZONE', 'L1', 'L2', 'L3', 'L4')),
  progress_percent numeric(5, 2) not null check (progress_percent between 0 and 100),
  original_watch_id uuid references market_bot.entry_watches(id) on delete restrict,
  armed_at timestamptz not null,
  updated_at timestamptz not null,
  expires_at timestamptz not null,
  closed_at timestamptz,
  close_reason text check (close_reason is null or close_reason in (
    'ORIGINAL_THESIS_INVALIDATED', 'EXPIRED', 'UNIVERSE_REMOVED', 'ALL_HORIZONS_CLOSED'
  )),
  zone_low numeric(28, 8) not null,
  zone_high numeric(28, 8) not null,
  invalidation numeric(28, 8) not null,
  original_price numeric(28, 8) not null,
  current_price numeric(28, 8) not null,
  revision integer not null check (revision >= 1),
  payload jsonb not null,
  created_at timestamptz not null default now(),
  constraint entry_opportunities_levels_check check (
    invalidation < zone_low and zone_low <= zone_high
  ),
  constraint entry_opportunities_expiry_check check (expires_at > armed_at),
  constraint entry_opportunities_closure_evidence_check check (
    (status = 'CLOSED') = (closed_at is not null and close_reason is not null)
  )
);

create unique index entry_opportunities_one_active_per_symbol_idx
  on market_bot.entry_opportunities (symbol)
  where status <> 'CLOSED';
create index entry_opportunities_status_expires_idx
  on market_bot.entry_opportunities (status, expires_at);

create table market_bot.entry_opportunity_events (
  id uuid primary key,
  opportunity_id uuid not null
    references market_bot.entry_opportunities(id) on delete restrict,
  symbol text not null,
  occurred_at timestamptz not null,
  reasons jsonb not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create index entry_opportunity_events_opportunity_occurred_idx
  on market_bot.entry_opportunity_events (opportunity_id, occurred_at);
create index entry_opportunity_events_symbol_occurred_idx
  on market_bot.entry_opportunity_events (symbol, occurred_at);

create trigger entry_opportunity_events_immutable
  before update or delete on market_bot.entry_opportunity_events
  for each row execute function market_bot.prevent_mutation();

grant select, insert, update on market_bot.entry_opportunities to market_bot_runtime;
grant select, insert on market_bot.entry_opportunity_events to market_bot_runtime;

alter table market_bot.entry_opportunities enable row level security;
alter table market_bot.entry_opportunities force row level security;
alter table market_bot.entry_opportunity_events enable row level security;
alter table market_bot.entry_opportunity_events force row level security;

create policy entry_opportunities_runtime_select
  on market_bot.entry_opportunities for select to market_bot_runtime using (true);
create policy entry_opportunities_runtime_insert
  on market_bot.entry_opportunities for insert to market_bot_runtime with check (true);
create policy entry_opportunities_runtime_update
  on market_bot.entry_opportunities for update to market_bot_runtime
  using (true) with check (true);
create policy entry_opportunity_events_runtime_select
  on market_bot.entry_opportunity_events for select to market_bot_runtime using (true);
create policy entry_opportunity_events_runtime_insert
  on market_bot.entry_opportunity_events for insert to market_bot_runtime with check (true);
