begin;

create table market_bot.alert_analysis_states (
  id uuid primary key,
  engine_name text not null,
  implementation_version text not null,
  symbol text not null,
  horizon text not null check (horizon in ('LONG_TERM', 'SWING', 'INTRADAY')),
  analysis_id uuid not null,
  payload jsonb not null,
  updated_at timestamptz not null default now(),
  constraint alert_analysis_states_identity_key
    unique (engine_name, implementation_version, symbol, horizon)
);

create table market_bot.alert_continuation_candidates (
  id uuid primary key,
  engine_name text not null,
  implementation_version text not null,
  symbol text not null,
  active boolean not null,
  payload jsonb not null,
  updated_at timestamptz not null default now(),
  constraint alert_continuation_candidates_identity_key
    unique (engine_name, implementation_version, symbol)
);

create table market_bot.alert_continuation_sessions (
  id uuid primary key,
  engine_name text not null,
  implementation_version text not null,
  symbol text not null,
  market_session date not null,
  updated_at timestamptz not null default now(),
  constraint alert_continuation_sessions_identity_key
    unique (engine_name, implementation_version, symbol)
);

grant select, insert, update on market_bot.alert_analysis_states to market_bot_runtime;
grant select, insert, update on market_bot.alert_continuation_candidates to market_bot_runtime;
grant select, insert, update on market_bot.alert_continuation_sessions to market_bot_runtime;

alter table market_bot.alert_analysis_states enable row level security;
alter table market_bot.alert_analysis_states force row level security;
alter table market_bot.alert_continuation_candidates enable row level security;
alter table market_bot.alert_continuation_candidates force row level security;
alter table market_bot.alert_continuation_sessions enable row level security;
alter table market_bot.alert_continuation_sessions force row level security;

create policy alert_analysis_states_runtime_select on market_bot.alert_analysis_states
  for select to market_bot_runtime using (true);
create policy alert_analysis_states_runtime_insert on market_bot.alert_analysis_states
  for insert to market_bot_runtime with check (true);
create policy alert_analysis_states_runtime_update on market_bot.alert_analysis_states
  for update to market_bot_runtime using (true) with check (true);

create policy alert_continuation_candidates_runtime_select
  on market_bot.alert_continuation_candidates
  for select to market_bot_runtime using (true);
create policy alert_continuation_candidates_runtime_insert
  on market_bot.alert_continuation_candidates
  for insert to market_bot_runtime with check (true);
create policy alert_continuation_candidates_runtime_update
  on market_bot.alert_continuation_candidates
  for update to market_bot_runtime using (true) with check (true);

create policy alert_continuation_sessions_runtime_select
  on market_bot.alert_continuation_sessions
  for select to market_bot_runtime using (true);
create policy alert_continuation_sessions_runtime_insert
  on market_bot.alert_continuation_sessions
  for insert to market_bot_runtime with check (true);
create policy alert_continuation_sessions_runtime_update
  on market_bot.alert_continuation_sessions
  for update to market_bot_runtime using (true) with check (true);

commit;
