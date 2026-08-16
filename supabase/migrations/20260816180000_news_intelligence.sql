begin;

create table market_bot.news_intelligence_results (
  provider text not null,
  article_id bigint not null,
  content_hash text not null,
  article_updated_at timestamptz not null,
  assessed_at timestamptz not null,
  model text not null,
  prompt_version text not null,
  assessment jsonb not null,
  analysis_results jsonb not null,
  primary key (provider, article_id)
);

create index news_intelligence_results_updated_idx
  on market_bot.news_intelligence_results (article_updated_at, article_id);

grant select, insert, update on market_bot.news_intelligence_results
  to market_bot_runtime;

alter table market_bot.news_intelligence_results enable row level security;
alter table market_bot.news_intelligence_results force row level security;

create policy news_intelligence_results_runtime_select
  on market_bot.news_intelligence_results
  for select to market_bot_runtime using (true);
create policy news_intelligence_results_runtime_insert
  on market_bot.news_intelligence_results
  for insert to market_bot_runtime with check (true);
create policy news_intelligence_results_runtime_update
  on market_bot.news_intelligence_results
  for update to market_bot_runtime using (true) with check (true);

alter table market_bot.alert_analysis_states
  drop constraint if exists alert_analysis_states_horizon_check;
alter table market_bot.alert_analysis_states
  add constraint alert_analysis_states_horizon_check
  check (
    horizon in (
      'LONG_TERM', 'DILUTION', 'SWING', 'INTRADAY',
      'VOLUME_STRUCTURE', 'OPTIONS_GAMMA', 'NEWS'
    )
  );

commit;
