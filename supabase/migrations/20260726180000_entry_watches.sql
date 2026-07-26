-- Persist Long entry theses so pullbacks can be recognized across runs and weeks.
create table market_bot.entry_watches (
  id uuid primary key,
  symbol text not null,
  status text not null check (
    status in ('ARMED', 'IN_ZONE', 'TRIGGERED', 'INVALIDATED', 'EXPIRED')
  ),
  thesis_version text not null,
  armed_at timestamptz not null,
  updated_at timestamptz not null,
  expires_at timestamptz not null,
  terminal_at timestamptz,
  zone_low numeric(28, 8) not null,
  zone_high numeric(28, 8) not null,
  invalidation numeric(28, 8) not null,
  original_price numeric(28, 8) not null,
  current_price numeric(28, 8) not null,
  correction_target_percent numeric(12, 4) not null,
  source_analysis_id uuid not null,
  source_context_hash text not null check (
    source_context_hash ~ '^sha256:[0-9a-f]{64}$'
  ),
  anchor_snapshot jsonb not null,
  created_at timestamptz not null default now(),
  constraint entry_watches_level_order_check
    check (invalidation < zone_low and zone_low <= zone_high),
  constraint entry_watches_correction_nonnegative_check
    check (correction_target_percent >= 0),
  constraint entry_watches_expiry_after_arm_check
    check (expires_at > armed_at)
);

create unique index entry_watches_one_active_per_symbol_idx
  on market_bot.entry_watches (symbol)
  where status in ('ARMED', 'IN_ZONE');
create index entry_watches_status_expires_at_idx
  on market_bot.entry_watches (status, expires_at);

create table market_bot.entry_watch_transitions (
  id uuid primary key,
  watch_id uuid not null references market_bot.entry_watches (id) on delete restrict,
  previous_status text check (
    previous_status is null
    or previous_status in ('ARMED', 'IN_ZONE', 'TRIGGERED', 'INVALIDATED', 'EXPIRED')
  ),
  status text not null check (
    status in ('ARMED', 'IN_ZONE', 'TRIGGERED', 'INVALIDATED', 'EXPIRED')
  ),
  occurred_at timestamptz not null,
  current_price numeric(28, 8) not null,
  reasons jsonb not null,
  horizons jsonb not null,
  source_analysis_ids jsonb not null,
  created_at timestamptz not null default now()
);

create index entry_watch_transitions_watch_occurred_idx
  on market_bot.entry_watch_transitions (watch_id, occurred_at);

create trigger entry_watch_transitions_immutable
  before update or delete on market_bot.entry_watch_transitions
  for each row execute function market_bot.prevent_mutation();

grant select, insert, update on market_bot.entry_watches to market_bot_runtime;
grant select, insert on market_bot.entry_watch_transitions to market_bot_runtime;

alter table market_bot.entry_watches enable row level security;
alter table market_bot.entry_watches force row level security;
alter table market_bot.entry_watch_transitions enable row level security;
alter table market_bot.entry_watch_transitions force row level security;

create policy entry_watches_runtime_select on market_bot.entry_watches
  for select to market_bot_runtime using (true);
create policy entry_watches_runtime_insert on market_bot.entry_watches
  for insert to market_bot_runtime with check (true);
create policy entry_watches_runtime_update on market_bot.entry_watches
  for update to market_bot_runtime using (true) with check (true);

create policy entry_watch_transitions_runtime_select on market_bot.entry_watch_transitions
  for select to market_bot_runtime using (true);
create policy entry_watch_transitions_runtime_insert on market_bot.entry_watch_transitions
  for insert to market_bot_runtime with check (true);
