-- FIFO head and bounded backlog checks must not scan processed history.
create index if not exists entry_opportunity_commands_fifo_idx
  on market_bot.entry_opportunity_commands (created_at, id)
  where status != 'PROCESSED';
