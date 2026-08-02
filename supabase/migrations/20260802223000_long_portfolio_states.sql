-- Persist only the compact confirmation memory needed by LONG Portfolio across restarts.
create table market_bot.long_portfolio_states (
  rule_version text not null,
  symbol text not null,
  qualified_sessions jsonb not null,
  last_emitted timestamptz,
  updated_at timestamptz not null,
  primary key (rule_version, symbol),
  constraint long_portfolio_states_sessions_array_check
    check (jsonb_typeof(qualified_sessions) = 'array')
);

create index long_portfolio_states_updated_idx
  on market_bot.long_portfolio_states (updated_at);

grant select, insert, update on market_bot.long_portfolio_states to market_bot_runtime;

alter table market_bot.long_portfolio_states enable row level security;
alter table market_bot.long_portfolio_states force row level security;

create policy long_portfolio_states_runtime_select
  on market_bot.long_portfolio_states for select to market_bot_runtime using (true);
create policy long_portfolio_states_runtime_insert
  on market_bot.long_portfolio_states for insert to market_bot_runtime with check (true);
create policy long_portfolio_states_runtime_update
  on market_bot.long_portfolio_states for update to market_bot_runtime
  using (true) with check (true);
