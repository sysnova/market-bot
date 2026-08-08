-- Rename the generic strategy comparison mode without changing paper execution semantics.
begin;

alter table market_bot.run_strategies
  drop constraint run_strategies_mode_check;

alter table market_bot.run_strategies
  add constraint run_strategies_mode_check
  check (mode in ('PRIMARY', 'CANDIDATE', 'RESEARCH', 'DISABLED')) not valid;

alter table market_bot.run_strategies
  disable trigger run_strategies_immutable;

update market_bot.run_strategies
set mode = 'CANDIDATE'
where mode = 'SHADOW';

alter table market_bot.run_strategies
  enable trigger run_strategies_immutable;

alter table market_bot.run_strategies
  validate constraint run_strategies_mode_check;

commit;
