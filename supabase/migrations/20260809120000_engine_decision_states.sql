begin;

create table market_bot.engine_decision_states (
  id uuid primary key,
  engine_name text not null,
  implementation_version text not null,
  state_schema_version text not null,
  payload jsonb not null,
  updated_at timestamptz not null default now(),
  constraint engine_decision_states_engine_implementation_key
    unique (engine_name, implementation_version)
);

grant select, insert, update on market_bot.engine_decision_states to market_bot_runtime;

alter table market_bot.engine_decision_states enable row level security;
alter table market_bot.engine_decision_states force row level security;

create policy engine_decision_states_runtime_select on market_bot.engine_decision_states
  for select to market_bot_runtime using (true);
create policy engine_decision_states_runtime_insert on market_bot.engine_decision_states
  for insert to market_bot_runtime with check (true);
create policy engine_decision_states_runtime_update on market_bot.engine_decision_states
  for update to market_bot_runtime using (true) with check (true);

commit;
