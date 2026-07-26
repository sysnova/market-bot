-- MarketBot private persistence foundation. UUIDv7 identifiers are supplied by Python.
create schema market_bot authorization postgres;

revoke all on schema market_bot from public, anon, authenticated, service_role;

do $$
declare
  runtime_role record;
begin
  select
    rolcanlogin,
    rolsuper,
    rolcreatedb,
    rolcreaterole,
    rolreplication,
    rolbypassrls
  into runtime_role
  from pg_roles
  where rolname = 'market_bot_runtime';

  if not found then
    create role market_bot_runtime nologin;
  elsif runtime_role.rolcanlogin
    or runtime_role.rolsuper
    or runtime_role.rolcreatedb
    or runtime_role.rolcreaterole
    or runtime_role.rolreplication
    or runtime_role.rolbypassrls then
    raise exception 'existing market_bot_runtime role has unsafe attributes';
  end if;
end
$$;

create table market_bot.runs (
  id uuid primary key,
  status text not null check (
    status in ('CREATED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')
  ),
  started_at timestamptz not null,
  completed_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint runs_completed_after_start_check
    check (completed_at is null or completed_at >= started_at)
);

create table market_bot.rule_versions (
  id uuid primary key,
  rule_id text not null,
  version text not null,
  family text not null,
  engine_id text not null,
  implementation_hash text not null check (
    implementation_hash ~ '^sha256:[0-9a-f]{64}$'
  ),
  manifest jsonb not null,
  created_at timestamptz not null default now(),
  constraint rule_versions_identity_key
    unique (engine_id, rule_id, version)
);

create table market_bot.strategy_versions (
  id uuid primary key,
  strategy_id text not null,
  version text not null,
  family text not null,
  engine_id text not null,
  compiled_hash text not null check (
    compiled_hash ~ '^sha256:[0-9a-f]{64}$'
  ),
  definition jsonb not null,
  created_at timestamptz not null default now(),
  constraint strategy_versions_identity_key
    unique (engine_id, strategy_id, version)
);

create table market_bot.run_strategies (
  id uuid primary key,
  run_id uuid not null references market_bot.runs (id) on delete restrict,
  strategy_version_id uuid not null
    references market_bot.strategy_versions (id) on delete restrict,
  engine_id text not null,
  strategy_family text not null,
  mode text not null check (mode in ('PRIMARY', 'SHADOW', 'RESEARCH', 'DISABLED')),
  created_at timestamptz not null default now(),
  constraint run_strategies_assignment_key unique (run_id, strategy_version_id)
);

create unique index run_strategies_one_primary_per_scope_idx
  on market_bot.run_strategies (run_id, engine_id, strategy_family)
  where mode = 'PRIMARY';

create index run_strategies_strategy_version_id_idx
  on market_bot.run_strategies (strategy_version_id);

create table market_bot.processed_events (
  id uuid primary key,
  consumer_name text not null,
  event_id uuid not null,
  run_id uuid references market_bot.runs (id) on delete restrict,
  subject text not null,
  payload_hash text not null check (payload_hash ~ '^sha256:[0-9a-f]{64}$'),
  processed_at timestamptz not null default now(),
  constraint processed_events_delivery_key unique (consumer_name, event_id)
);

create index processed_events_run_id_idx on market_bot.processed_events (run_id);

create table market_bot.outbox_events (
  id uuid primary key,
  aggregate_type text not null,
  aggregate_id text not null,
  event_type text not null,
  subject text not null,
  payload jsonb not null,
  headers jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null,
  available_at timestamptz not null,
  published_at timestamptz,
  attempts integer not null default 0 check (attempts >= 0),
  last_error text,
  created_at timestamptz not null default now(),
  constraint outbox_events_published_order_check
    check (published_at is null or published_at >= occurred_at)
);

create index outbox_events_pending_idx
  on market_bot.outbox_events (available_at, created_at)
  where published_at is null;

create table market_bot.consumer_checkpoints (
  id uuid primary key,
  consumer_name text not null,
  stream text not null,
  sequence bigint not null check (sequence >= 0),
  updated_at timestamptz not null default now(),
  constraint consumer_checkpoints_position_key unique (consumer_name, stream)
);

create table market_bot.service_health (
  id uuid primary key,
  service_name text not null,
  status text not null check (status in ('HEALTHY', 'DEGRADED', 'UNHEALTHY', 'UNKNOWN')),
  details jsonb not null default '{}'::jsonb,
  observed_at timestamptz not null,
  updated_at timestamptz not null default now(),
  constraint service_health_service_name_key unique (service_name)
);

create table market_bot.control_events (
  id uuid primary key,
  event_type text not null,
  run_id uuid references market_bot.runs (id) on delete restrict,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index control_events_run_id_created_at_idx
  on market_bot.control_events (run_id, created_at);

create function market_bot.prevent_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception '% is immutable', tg_table_name using errcode = '55000';
end
$$;

revoke all on function market_bot.prevent_mutation() from public, anon, authenticated, service_role;

create trigger rule_versions_immutable
  before update or delete on market_bot.rule_versions
  for each row execute function market_bot.prevent_mutation();
create trigger strategy_versions_immutable
  before update or delete on market_bot.strategy_versions
  for each row execute function market_bot.prevent_mutation();
create trigger run_strategies_immutable
  before update or delete on market_bot.run_strategies
  for each row execute function market_bot.prevent_mutation();
create trigger processed_events_immutable
  before update or delete on market_bot.processed_events
  for each row execute function market_bot.prevent_mutation();
create trigger control_events_immutable
  before update or delete on market_bot.control_events
  for each row execute function market_bot.prevent_mutation();

grant usage on schema market_bot to market_bot_runtime;
grant market_bot_runtime to postgres;
grant select, insert, update on market_bot.runs to market_bot_runtime;
grant select, insert on market_bot.rule_versions to market_bot_runtime;
grant select, insert on market_bot.strategy_versions to market_bot_runtime;
grant select, insert on market_bot.run_strategies to market_bot_runtime;
grant select, insert on market_bot.processed_events to market_bot_runtime;
grant select, insert, update on market_bot.outbox_events to market_bot_runtime;
grant select, insert, update on market_bot.consumer_checkpoints to market_bot_runtime;
grant select, insert, update on market_bot.service_health to market_bot_runtime;
grant select, insert on market_bot.control_events to market_bot_runtime;

alter default privileges for role postgres in schema market_bot
  revoke all on tables from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema market_bot
  revoke all on sequences from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema market_bot
  revoke all on functions from public, anon, authenticated, service_role;

alter table market_bot.runs enable row level security;
alter table market_bot.runs force row level security;
alter table market_bot.rule_versions enable row level security;
alter table market_bot.rule_versions force row level security;
alter table market_bot.strategy_versions enable row level security;
alter table market_bot.strategy_versions force row level security;
alter table market_bot.run_strategies enable row level security;
alter table market_bot.run_strategies force row level security;
alter table market_bot.processed_events enable row level security;
alter table market_bot.processed_events force row level security;
alter table market_bot.outbox_events enable row level security;
alter table market_bot.outbox_events force row level security;
alter table market_bot.consumer_checkpoints enable row level security;
alter table market_bot.consumer_checkpoints force row level security;
alter table market_bot.service_health enable row level security;
alter table market_bot.service_health force row level security;
alter table market_bot.control_events enable row level security;
alter table market_bot.control_events force row level security;

create policy runs_runtime_select on market_bot.runs
  for select to market_bot_runtime using (true);
create policy runs_runtime_insert on market_bot.runs
  for insert to market_bot_runtime with check (true);
create policy runs_runtime_update on market_bot.runs
  for update to market_bot_runtime using (true) with check (true);

create policy rule_versions_runtime_select on market_bot.rule_versions
  for select to market_bot_runtime using (true);
create policy rule_versions_runtime_insert on market_bot.rule_versions
  for insert to market_bot_runtime with check (true);

create policy strategy_versions_runtime_select on market_bot.strategy_versions
  for select to market_bot_runtime using (true);
create policy strategy_versions_runtime_insert on market_bot.strategy_versions
  for insert to market_bot_runtime with check (true);

create policy run_strategies_runtime_select on market_bot.run_strategies
  for select to market_bot_runtime using (true);
create policy run_strategies_runtime_insert on market_bot.run_strategies
  for insert to market_bot_runtime with check (true);

create policy processed_events_runtime_select on market_bot.processed_events
  for select to market_bot_runtime using (true);
create policy processed_events_runtime_insert on market_bot.processed_events
  for insert to market_bot_runtime with check (true);

create policy outbox_events_runtime_select on market_bot.outbox_events
  for select to market_bot_runtime using (true);
create policy outbox_events_runtime_insert on market_bot.outbox_events
  for insert to market_bot_runtime with check (true);
create policy outbox_events_runtime_update on market_bot.outbox_events
  for update to market_bot_runtime using (true) with check (true);

create policy consumer_checkpoints_runtime_select on market_bot.consumer_checkpoints
  for select to market_bot_runtime using (true);
create policy consumer_checkpoints_runtime_insert on market_bot.consumer_checkpoints
  for insert to market_bot_runtime with check (true);
create policy consumer_checkpoints_runtime_update on market_bot.consumer_checkpoints
  for update to market_bot_runtime using (true) with check (true);

create policy service_health_runtime_select on market_bot.service_health
  for select to market_bot_runtime using (true);
create policy service_health_runtime_insert on market_bot.service_health
  for insert to market_bot_runtime with check (true);
create policy service_health_runtime_update on market_bot.service_health
  for update to market_bot_runtime using (true) with check (true);

create policy control_events_runtime_select on market_bot.control_events
  for select to market_bot_runtime using (true);
create policy control_events_runtime_insert on market_bot.control_events
  for insert to market_bot_runtime with check (true);
