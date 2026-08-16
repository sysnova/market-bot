do $$
declare
  actual_columns text[];
begin
  if to_regclass('market_bot.news_intelligence_results') is null then
    raise exception 'news_intelligence_results table is missing';
  end if;

  select array_agg(column_name order by ordinal_position)
  into actual_columns
  from information_schema.columns
  where table_schema = 'market_bot'
    and table_name = 'news_intelligence_results';

  if actual_columns <> array[
    'provider',
    'article_id',
    'content_hash',
    'article_updated_at',
    'assessed_at',
    'model',
    'prompt_version',
    'assessment',
    'analysis_results'
  ]::text[] then
    raise exception 'Unexpected news intelligence columns: %', actual_columns;
  end if;

  if not exists (
    select 1
    from pg_constraint
    join pg_class on pg_class.oid = pg_constraint.conrelid
    join pg_namespace on pg_namespace.oid = pg_class.relnamespace
    where pg_namespace.nspname = 'market_bot'
      and pg_class.relname = 'news_intelligence_results'
      and pg_constraint.contype = 'p'
      and pg_get_constraintdef(pg_constraint.oid) = 'PRIMARY KEY (provider, article_id)'
  ) then
    raise exception 'news intelligence provider/article primary key is missing';
  end if;

  if not exists (
    select 1
    from pg_indexes
    where schemaname = 'market_bot'
      and indexname = 'news_intelligence_results_updated_idx'
      and indexdef like '%(article_updated_at, article_id)%'
  ) then
    raise exception 'news intelligence bootstrap index is missing';
  end if;

  if not exists (
    select 1
    from pg_class
    join pg_namespace on pg_namespace.oid = pg_class.relnamespace
    where pg_namespace.nspname = 'market_bot'
      and pg_class.relname = 'news_intelligence_results'
      and pg_class.relrowsecurity
      and pg_class.relforcerowsecurity
  ) then
    raise exception 'news intelligence RLS must be enabled and forced';
  end if;

  if (
    select array_agg(cmd order by cmd)
    from pg_policies
    where schemaname = 'market_bot'
      and tablename = 'news_intelligence_results'
      and 'market_bot_runtime' = any(roles)
  ) <> array['INSERT', 'SELECT', 'UPDATE']::text[] then
    raise exception 'news intelligence runtime policies are incomplete';
  end if;

  if not exists (
    select 1
    from pg_constraint
    join pg_class on pg_class.oid = pg_constraint.conrelid
    join pg_namespace on pg_namespace.oid = pg_class.relnamespace
    where pg_namespace.nspname = 'market_bot'
      and pg_class.relname = 'alert_analysis_states'
      and pg_constraint.conname = 'alert_analysis_states_horizon_check'
      and pg_get_constraintdef(pg_constraint.oid) like '%NEWS%'
  ) then
    raise exception 'alert analysis horizon constraint does not accept NEWS';
  end if;
end
$$;

begin;
set local role market_bot_runtime;

insert into market_bot.news_intelligence_results (
  provider,
  article_id,
  content_hash,
  article_updated_at,
  assessed_at,
  model,
  prompt_version,
  assessment,
  analysis_results
) values (
  'ci',
  1,
  'sha256:test',
  now(),
  now(),
  'test-model',
  'test-prompt-v1',
  '{"materiality":"HIGH"}'::jsonb,
  '[]'::jsonb
);

update market_bot.news_intelligence_results
set content_hash = 'sha256:updated'
where provider = 'ci' and article_id = 1;

do $$
begin
  if not exists (
    select 1
    from market_bot.news_intelligence_results
    where provider = 'ci'
      and article_id = 1
      and content_hash = 'sha256:updated'
  ) then
    raise exception 'runtime role could not round-trip news intelligence';
  end if;

  begin
    delete from market_bot.news_intelligence_results
    where provider = 'ci' and article_id = 1;
    raise exception 'runtime role unexpectedly deleted news intelligence';
  exception
    when insufficient_privilege then null;
  end;
end
$$;

rollback;
