-- Recoverable normalized historical-bar cache owned by MarketData History.
create table market_bot.market_bars (
  symbol text not null,
  timeframe text not null check (timeframe in ('1Min', '15Min', '1Hour', '1Day', '1Week')),
  timestamp timestamptz not null,
  open numeric(28, 8) not null,
  high numeric(28, 8) not null,
  low numeric(28, 8) not null,
  close numeric(28, 8) not null,
  volume numeric(28, 8) not null check (volume >= 0),
  trade_count bigint,
  vwap numeric(28, 8),
  source text not null,
  feed text not null,
  is_final boolean not null default true,
  downloaded_at timestamptz not null default now(),
  primary key (symbol, timeframe, timestamp),
  constraint market_bars_ohlc_check check (
    high >= open and high >= low and high >= close
    and low <= open and low <= high and low <= close
  )
);

create index market_bars_history_idx
  on market_bot.market_bars (symbol, timeframe, timestamp desc);

grant select, insert, update on market_bot.market_bars to market_bot_runtime;

alter table market_bot.market_bars enable row level security;
alter table market_bot.market_bars force row level security;

create policy market_bars_runtime_select
  on market_bot.market_bars for select to market_bot_runtime using (true);
create policy market_bars_runtime_insert
  on market_bot.market_bars for insert to market_bot_runtime with check (true);
create policy market_bars_runtime_update
  on market_bot.market_bars for update to market_bot_runtime
  using (true) with check (true);

create function market_bot.prune_market_bars(
  p_timeframe text,
  p_keep_per_symbol integer
) returns bigint
language plpgsql
security definer
set search_path = market_bot, pg_temp
as $$
declare
  removed bigint;
begin
  if p_timeframe not in ('1Min', '15Min', '1Hour', '1Day', '1Week') then
    raise exception 'unsupported market-bar timeframe';
  end if;
  if p_keep_per_symbol < 1 then
    raise exception 'market-bar retention must be positive';
  end if;

  with stale as (
    select symbol, timeframe, timestamp
    from (
      select symbol, timeframe, timestamp,
             row_number() over (
               partition by symbol, timeframe order by timestamp desc
             ) as position
      from market_bot.market_bars
      where timeframe = p_timeframe
    ) ranked
    where position > p_keep_per_symbol
  )
  delete from market_bot.market_bars target
  using stale
  where target.symbol = stale.symbol
    and target.timeframe = stale.timeframe
    and target.timestamp = stale.timestamp;

  get diagnostics removed = row_count;
  return removed;
end;
$$;

revoke all on function market_bot.prune_market_bars(text, integer) from public;
grant execute on function market_bot.prune_market_bars(text, integer)
  to market_bot_runtime;
