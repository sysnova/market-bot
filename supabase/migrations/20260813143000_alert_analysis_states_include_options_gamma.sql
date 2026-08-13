begin;

alter table market_bot.alert_analysis_states
  drop constraint if exists alert_analysis_states_horizon_check;

alter table market_bot.alert_analysis_states
  add constraint alert_analysis_states_horizon_check
  check (
    horizon in (
      'LONG_TERM',
      'DILUTION',
      'SWING',
      'INTRADAY',
      'VOLUME_STRUCTURE',
      'OPTIONS_GAMMA'
    )
  );

commit;
