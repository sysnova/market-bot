-- Remove opportunities incorrectly bootstrapped from an IMPULSE_EXTENDED transition.
-- Preserve complete JSON snapshots so the cleanup remains recoverable and auditable.

create table if not exists market_bot.entry_opportunity_cleanup_archive_20260814 (
    opportunity_id uuid primary key,
    symbol text not null,
    original_watch_id uuid,
    opportunity_snapshot jsonb not null,
    archived_at timestamptz not null default now()
);

create table if not exists market_bot.entry_opportunity_event_cleanup_archive_20260814 (
    event_id uuid primary key,
    opportunity_id uuid not null,
    symbol text not null,
    event_snapshot jsonb not null,
    archived_at timestamptz not null default now()
);

insert into market_bot.entry_opportunity_cleanup_archive_20260814 (
    opportunity_id,
    symbol,
    original_watch_id,
    opportunity_snapshot
)
select o.id, o.symbol, o.original_watch_id, to_jsonb(o)
from market_bot.entry_opportunities o
where exists (
    select 1
    from market_bot.entry_opportunity_events e
    where e.opportunity_id = o.id
      and e.reasons @> '[
          "opportunity_created",
          "entry_window_missed",
          "impulse_extended_awaiting_pullback"
      ]'::jsonb
)
on conflict (opportunity_id) do nothing;

insert into market_bot.entry_opportunity_event_cleanup_archive_20260814 (
    event_id,
    opportunity_id,
    symbol,
    event_snapshot
)
select e.id, e.opportunity_id, e.symbol, to_jsonb(e)
from market_bot.entry_opportunity_events e
join market_bot.entry_opportunity_cleanup_archive_20260814 archived
  on archived.opportunity_id = e.opportunity_id
on conflict (event_id) do nothing;

-- The source table is append-only. A maintenance owner may remove these rows only
-- after the complete snapshots above exist, and the immutable trigger is restored
-- inside the same transaction before the parent opportunities are removed.
alter table market_bot.entry_opportunity_events
    disable trigger entry_opportunity_events_immutable;

delete from market_bot.entry_opportunity_events e
using market_bot.entry_opportunity_cleanup_archive_20260814 archived
where e.opportunity_id = archived.opportunity_id;

alter table market_bot.entry_opportunity_events
    enable trigger entry_opportunity_events_immutable;

delete from market_bot.entry_opportunities o
using market_bot.entry_opportunity_cleanup_archive_20260814 archived
where o.id = archived.opportunity_id;

drop trigger if exists entry_opportunity_cleanup_archive_20260814_immutable
    on market_bot.entry_opportunity_cleanup_archive_20260814;
create trigger entry_opportunity_cleanup_archive_20260814_immutable
    before update or delete on market_bot.entry_opportunity_cleanup_archive_20260814
    for each row execute function market_bot.prevent_entry_opportunity_event_mutation();

drop trigger if exists entry_opportunity_event_cleanup_archive_20260814_immutable
    on market_bot.entry_opportunity_event_cleanup_archive_20260814;
create trigger entry_opportunity_event_cleanup_archive_20260814_immutable
    before update or delete on market_bot.entry_opportunity_event_cleanup_archive_20260814
    for each row execute function market_bot.prevent_entry_opportunity_event_mutation();
