alter table market_bot.entry_watches
    drop constraint if exists entry_watches_status_check;

alter table market_bot.entry_watches
    add constraint entry_watches_status_check check (
        status in (
            'ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED',
            'TRIGGERED', 'INVALIDATED', 'EXPIRED'
        )
    );

alter table market_bot.entry_watch_transitions
    drop constraint if exists entry_watch_transitions_status_check;

alter table market_bot.entry_watch_transitions
    add constraint entry_watch_transitions_status_check check (
        status in (
            'ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED',
            'TRIGGERED', 'INVALIDATED', 'EXPIRED'
        )
    );

alter table market_bot.entry_watch_transitions
    drop constraint if exists entry_watch_transitions_previous_status_check;

alter table market_bot.entry_watch_transitions
    add constraint entry_watch_transitions_previous_status_check check (
        previous_status is null or previous_status in (
            'ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED',
            'TRIGGERED', 'INVALIDATED', 'EXPIRED'
        )
    );

drop index if exists market_bot.entry_watches_one_active_per_symbol_idx;

create unique index entry_watches_one_active_per_symbol_idx
    on market_bot.entry_watches (symbol)
    where status in ('ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED');
