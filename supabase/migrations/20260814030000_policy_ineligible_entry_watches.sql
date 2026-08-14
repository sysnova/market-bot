-- Make the modern frozen-snapshot arm gate a durable terminal lifecycle outcome.

alter table market_bot.entry_watches
    drop constraint if exists entry_watches_status_check;

alter table market_bot.entry_watches
    add constraint entry_watches_status_check check (
        status in (
            'ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED', 'TRIGGERED',
            'POLICY_INELIGIBLE', 'INVALIDATED', 'EXPIRED'
        )
    );

alter table market_bot.entry_watch_transitions
    drop constraint if exists entry_watch_transitions_status_check;

alter table market_bot.entry_watch_transitions
    add constraint entry_watch_transitions_status_check check (
        status in (
            'ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED', 'TRIGGERED',
            'POLICY_INELIGIBLE', 'INVALIDATED', 'EXPIRED'
        )
    );

alter table market_bot.entry_watch_transitions
    drop constraint if exists entry_watch_transitions_previous_status_check;

alter table market_bot.entry_watch_transitions
    add constraint entry_watch_transitions_previous_status_check check (
        previous_status is null or previous_status in (
            'ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED', 'TRIGGERED',
            'POLICY_INELIGIBLE', 'INVALIDATED', 'EXPIRED'
        )
    );

alter table market_bot.entry_opportunities
    drop constraint if exists entry_opportunities_close_reason_check;

alter table market_bot.entry_opportunities
    add constraint entry_opportunities_close_reason_check check (
        close_reason is null or close_reason in (
            'POLICY_INELIGIBLE', 'ORIGINAL_THESIS_INVALIDATED', 'EXPIRED',
            'UNIVERSE_REMOVED', 'ALL_HORIZONS_CLOSED'
        )
    );
