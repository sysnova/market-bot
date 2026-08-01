-- Durable shadow state and immutable transition history for PatreonCaps v1.
create table market_bot.patreon_caps_watches (
  id uuid primary key,
  symbol text not null,
  rule_version text not null,
  state text not null check (state in (
    'WATCH_ZONE', 'SUPPORT_TEST', 'CONFIRMED_V', 'CONFIRMED_BASE',
    'IMPULSE_RETEST', 'INVALIDATED', 'EXPIRED'
  )),
  armed_at timestamptz not null,
  updated_at timestamptz not null,
  expires_at timestamptz not null,
  zone_low numeric(28, 8) not null,
  zone_center numeric(28, 8) not null,
  zone_high numeric(28, 8) not null,
  invalidation numeric(28, 8) not null,
  highest_price numeric(28, 8) not null,
  tranche_stage integer not null default 0 check (tranche_stage between 0 and 5),
  saw_macro_shock boolean not null default false,
  support_sources jsonb not null,
  source_analysis_ids jsonb not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  constraint patreon_caps_watches_levels_check check (
    invalidation < zone_low and zone_low <= zone_center and zone_center <= zone_high
  ),
  constraint patreon_caps_watches_expiry_check check (expires_at > armed_at)
);

create unique index patreon_caps_one_active_per_symbol_version_idx
  on market_bot.patreon_caps_watches (symbol, rule_version)
  where state not in ('INVALIDATED', 'EXPIRED');
create index patreon_caps_watches_state_expires_idx
  on market_bot.patreon_caps_watches (state, expires_at);

create table market_bot.patreon_caps_transitions (
  id uuid primary key,
  deduplication_key text not null unique,
  watch_id uuid not null references market_bot.patreon_caps_watches(id) on delete restrict,
  symbol text not null,
  previous_state text,
  state text not null,
  occurred_at timestamptz not null,
  rule_version text not null,
  current_price numeric(28, 8) not null,
  patreon_score numeric(7, 2) not null check (patreon_score between 0 and 100),
  tranche_stage integer check (tranche_stage between 1 and 5),
  suggested_tranche_usd numeric(28, 2),
  suggested_whole_shares numeric(28, 0),
  payload jsonb not null,
  persisted_at timestamptz not null default now()
);

create index patreon_caps_transitions_symbol_occurred_idx
  on market_bot.patreon_caps_transitions (symbol, occurred_at desc);
create index patreon_caps_transitions_watch_occurred_idx
  on market_bot.patreon_caps_transitions (watch_id, occurred_at);

create trigger patreon_caps_transitions_immutable
  before update or delete on market_bot.patreon_caps_transitions
  for each row execute function market_bot.prevent_mutation();

grant select, insert, update on market_bot.patreon_caps_watches to market_bot_runtime;
grant select, insert on market_bot.patreon_caps_transitions to market_bot_runtime;

alter table market_bot.patreon_caps_watches enable row level security;
alter table market_bot.patreon_caps_watches force row level security;
alter table market_bot.patreon_caps_transitions enable row level security;
alter table market_bot.patreon_caps_transitions force row level security;

create policy patreon_caps_watches_runtime_select
  on market_bot.patreon_caps_watches for select to market_bot_runtime using (true);
create policy patreon_caps_watches_runtime_insert
  on market_bot.patreon_caps_watches for insert to market_bot_runtime with check (true);
create policy patreon_caps_watches_runtime_update
  on market_bot.patreon_caps_watches for update to market_bot_runtime
  using (true) with check (true);
create policy patreon_caps_transitions_runtime_select
  on market_bot.patreon_caps_transitions for select to market_bot_runtime using (true);
create policy patreon_caps_transitions_runtime_insert
  on market_bot.patreon_caps_transitions for insert to market_bot_runtime with check (true);
