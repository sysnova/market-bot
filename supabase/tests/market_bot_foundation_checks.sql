do $$
declare
  market_bot_table_count integer;
  rls_enabled_count integer;
  rls_forced_count integer;
begin
  select count(*)
  into market_bot_table_count
  from information_schema.tables
  where table_schema = 'market_bot';

  if market_bot_table_count <> 12 then
    raise exception 'Expected 12 market_bot tables, found %', market_bot_table_count;
  end if;

  select
    count(*) filter (where relrowsecurity),
    count(*) filter (where relforcerowsecurity)
  into rls_enabled_count, rls_forced_count
  from pg_class
  join pg_namespace on pg_namespace.oid = pg_class.relnamespace
  where pg_namespace.nspname = 'market_bot'
    and pg_class.relkind = 'r';

  if rls_enabled_count <> 9 or rls_forced_count <> 9 then
    raise exception
      'Expected RLS enabled and forced on all 9 tables, found enabled=% forced=%',
      rls_enabled_count,
      rls_forced_count;
  end if;

  if not exists (
    select 1
    from pg_roles
    where rolname = 'market_bot_runtime'
      and not rolcanlogin
      and not rolsuper
      and not rolcreatedb
      and not rolcreaterole
      and not rolreplication
      and not rolbypassrls
  ) then
    raise exception 'market_bot_runtime is missing or has unsafe attributes';
  end if;

  if not exists (
    select 1
    from pg_indexes
    where schemaname = 'market_bot'
      and indexname = 'run_strategies_one_primary_per_scope_idx'
      and indexdef like '%UNIQUE INDEX%'
      and indexdef like '%(run_id, engine_id, strategy_family)%'
      and indexdef like '%WHERE (mode = ''PRIMARY''%'
  ) then
    raise exception 'PRIMARY strategy uniqueness index is missing or malformed';
  end if;

  if exists (
    select 1
    from information_schema.role_table_grants
    where table_schema = 'market_bot'
      and grantee = 'market_bot_runtime'
      and privilege_type = 'DELETE'
  ) then
    raise exception 'market_bot_runtime must not receive DELETE privileges';
  end if;

  if exists (
    select 1
    from information_schema.role_table_grants
    where table_schema = 'market_bot'
      and grantee in ('anon', 'authenticated', 'service_role')
  ) then
    raise exception 'Supabase API roles must not receive market_bot table privileges';
  end if;

  if has_schema_privilege('anon', 'market_bot', 'usage')
    or has_schema_privilege('authenticated', 'market_bot', 'usage')
    or has_schema_privilege('service_role', 'market_bot', 'usage') then
    raise exception 'Supabase API roles must not receive market_bot schema usage';
  end if;

  if not exists (
    select 1
    from pg_constraint
    join pg_class on pg_class.oid = pg_constraint.conrelid
    join pg_namespace on pg_namespace.oid = pg_class.relnamespace
    where pg_namespace.nspname = 'market_bot'
      and pg_class.relname = 'rule_versions'
      and pg_constraint.conname = 'rule_versions_identity_key'
      and pg_get_constraintdef(pg_constraint.oid) = 'UNIQUE (engine_id, rule_id, version)'
  ) then
    raise exception 'Rule version identity must exclude implementation_hash';
  end if;

  if not exists (
    select 1
    from pg_constraint
    join pg_class on pg_class.oid = pg_constraint.conrelid
    join pg_namespace on pg_namespace.oid = pg_class.relnamespace
    where pg_namespace.nspname = 'market_bot'
      and pg_class.relname = 'strategy_versions'
      and pg_constraint.conname = 'strategy_versions_identity_key'
      and pg_get_constraintdef(pg_constraint.oid) = 'UNIQUE (engine_id, strategy_id, version)'
  ) then
    raise exception 'Strategy version identity must exclude compiled_hash';
  end if;
end
$$;

begin;
set local role market_bot_runtime;

insert into market_bot.runs (id, status, started_at)
values ('019bfe16-7690-7a11-8000-000000000001', 'CREATED', now());

update market_bot.runs
set status = 'RUNNING'
where id = '019bfe16-7690-7a11-8000-000000000001';

do $$
begin
  begin
    delete from market_bot.runs
    where id = '019bfe16-7690-7a11-8000-000000000001';
    raise exception 'market_bot_runtime unexpectedly deleted a run';
  exception
    when insufficient_privilege then null;
  end;
end
$$;

reset role;
set local role anon;

do $$
begin
  begin
    perform 1 from market_bot.runs limit 1;
    raise exception 'anon unexpectedly read market_bot.runs';
  exception
    when insufficient_privilege then null;
  end;
end
$$;

reset role;
rollback;

with checks as (
  select 'tables'::text as check_name,
    jsonb_build_object(
      'count', count(*),
      'names', jsonb_agg(table_name order by table_name)
    ) as details
  from information_schema.tables
  where table_schema = 'market_bot'

  union all

  select 'runtime_role', to_jsonb(runtime_role)
  from (
    select
      rolname,
      rolcanlogin,
      rolsuper,
      rolcreatedb,
      rolcreaterole,
      rolreplication,
      rolbypassrls
    from pg_roles
    where rolname = 'market_bot_runtime'
  ) as runtime_role

  union all

  select 'rls',
    jsonb_build_object(
      'enabled', count(*) filter (where relrowsecurity),
      'forced', count(*) filter (where relforcerowsecurity),
      'total', count(*)
    )
  from pg_class
  join pg_namespace on pg_namespace.oid = pg_class.relnamespace
  where pg_namespace.nspname = 'market_bot'
    and pg_class.relkind = 'r'

  union all

  select 'primary_index', jsonb_build_object('definition', indexdef)
  from pg_indexes
  where schemaname = 'market_bot'
    and indexname = 'run_strategies_one_primary_per_scope_idx'

  union all

  select 'delete_grants', jsonb_build_object('count', count(*))
  from information_schema.role_table_grants
  where table_schema = 'market_bot'
    and grantee = 'market_bot_runtime'
    and privilege_type = 'DELETE'

  union all

  select 'stock_schema', jsonb_build_object('table_count', count(*))
  from information_schema.tables
  where table_schema = 'stock'
)
select * from checks order by check_name;
