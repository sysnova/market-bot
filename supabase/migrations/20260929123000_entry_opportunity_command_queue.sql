create table market_bot.entry_opportunity_commands (
  id uuid primary key,
  source_event_id uuid not null,
  source_subject text not null,
  command_type text not null,
  symbol text not null,
  occurred_at timestamptz not null,
  status text not null default 'PENDING',
  attempts integer not null default 0,
  available_at timestamptz not null,
  processed_at timestamptz,
  last_error text,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  constraint entry_opportunity_commands_source_event_key unique (source_event_id),
  constraint entry_opportunity_commands_status_check
    check (status in ('PENDING', 'PROCESSING', 'PROCESSED', 'FAILED')),
  constraint entry_opportunity_commands_attempts_nonnegative check (attempts >= 0),
  constraint entry_opportunity_commands_processed_evidence_check
    check ((status = 'PROCESSED') = (processed_at is not null))
);

create index entry_opportunity_commands_pending_idx
  on market_bot.entry_opportunity_commands (available_at, occurred_at, created_at)
  where status = 'PENDING';

create index entry_opportunity_commands_symbol_status_idx
  on market_bot.entry_opportunity_commands (symbol, status);

grant select, insert, update on market_bot.entry_opportunity_commands to market_bot_runtime;

alter table market_bot.entry_opportunity_commands enable row level security;
alter table market_bot.entry_opportunity_commands force row level security;

create policy entry_opportunity_commands_runtime_select
  on market_bot.entry_opportunity_commands for select to market_bot_runtime using (true);
create policy entry_opportunity_commands_runtime_insert
  on market_bot.entry_opportunity_commands for insert to market_bot_runtime with check (true);
create policy entry_opportunity_commands_runtime_update
  on market_bot.entry_opportunity_commands for update to market_bot_runtime
  using (true) with check (true);
