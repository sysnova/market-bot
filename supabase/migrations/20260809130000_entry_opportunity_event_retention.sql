-- Safe bounded pruning for legacy non-material Entry Opportunity evidence snapshots.

create index if not exists entry_opportunity_events_legacy_evidence_retention_idx
  on market_bot.entry_opportunity_events (opportunity_id, occurred_at, id)
  where reasons in (
    '["long_term_evidence_updated"]'::jsonb,
    '["swing_evidence_updated"]'::jsonb,
    '["intraday_evidence_updated"]'::jsonb
  );

create policy entry_opportunity_events_maintenance_delete
  on market_bot.entry_opportunity_events
  for delete
  using (
    reasons in (
      '["long_term_evidence_updated"]'::jsonb,
      '["swing_evidence_updated"]'::jsonb,
      '["intraday_evidence_updated"]'::jsonb
    )
  );

create function market_bot.prune_entry_opportunity_evidence_events(
  p_cutoff timestamptz,
  p_retain_per_opportunity integer,
  p_batch_size integer
) returns table (deleted_rows bigint, deleted_bytes bigint)
language plpgsql
security definer
set search_path = pg_catalog, market_bot, pg_temp
as $$
begin
  if p_cutoff is null or p_cutoff >= now() then
    raise exception 'entry-opportunity retention cutoff must be in the past';
  end if;
  if p_retain_per_opportunity < 1 then
    raise exception 'entry-opportunity retained event count must be positive';
  end if;
  if p_batch_size < 1 or p_batch_size > 10000 then
    raise exception 'entry-opportunity retention batch must be between 1 and 10000';
  end if;

  return query
  with retention_boundaries as materialized (
    select
      opportunity.id as opportunity_id,
      boundary.occurred_at as protected_occurred_at,
      boundary.id as protected_event_id
    from market_bot.entry_opportunities as opportunity
    cross join lateral (
      select event.occurred_at, event.id
      from market_bot.entry_opportunity_events as event
      where event.opportunity_id = opportunity.id
      order by event.occurred_at desc, event.id desc
      offset (p_retain_per_opportunity - 1)
      limit 1
    ) as boundary
  ),
  locked_candidates as materialized (
    select event.id, pg_column_size(event) as row_bytes
    from retention_boundaries as boundary
    join market_bot.entry_opportunity_events as event
      on event.opportunity_id = boundary.opportunity_id
     and (event.occurred_at, event.id) < (
       boundary.protected_occurred_at,
       boundary.protected_event_id
     )
    where event.occurred_at < p_cutoff
      and event.reasons in (
        '["long_term_evidence_updated"]'::jsonb,
        '["swing_evidence_updated"]'::jsonb,
        '["intraday_evidence_updated"]'::jsonb
      )
    order by event.occurred_at, event.id
    limit p_batch_size
    for update of event skip locked
  ),
  deleted as (
    delete from market_bot.entry_opportunity_events as target
    using locked_candidates as candidate
    where target.id = candidate.id
    returning target.id
  )
  select
    count(*)::bigint,
    coalesce(sum(candidate.row_bytes), 0)::bigint
  from deleted
  join locked_candidates as candidate on candidate.id = deleted.id;
end;
$$;

revoke all on function market_bot.prune_entry_opportunity_evidence_events(
  timestamptz, integer, integer
) from public;
grant execute on function market_bot.prune_entry_opportunity_evidence_events(
  timestamptz, integer, integer
) to market_bot_runtime;

create function market_bot.prevent_entry_opportunity_event_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, market_bot, pg_temp
as $$
declare
  maintenance_owner name;
begin
  select pg_get_userbyid(proowner)
    into maintenance_owner
    from pg_catalog.pg_proc
   where oid = (
     'market_bot.prune_entry_opportunity_evidence_events(timestamptz,integer,integer)'
   )::regprocedure;

  if tg_op = 'DELETE'
     and current_user = maintenance_owner
     and old.reasons in (
       '["long_term_evidence_updated"]'::jsonb,
       '["swing_evidence_updated"]'::jsonb,
       '["intraday_evidence_updated"]'::jsonb
     ) then
    return old;
  end if;
  raise exception '% is append-only', tg_table_name using errcode = '55000';
end;
$$;

revoke all on function market_bot.prevent_entry_opportunity_event_mutation() from public;

drop trigger entry_opportunity_events_immutable
  on market_bot.entry_opportunity_events;
create trigger entry_opportunity_events_immutable
  before update or delete on market_bot.entry_opportunity_events
  for each row execute function market_bot.prevent_entry_opportunity_event_mutation();
