-- Correct databases that received the foundation before exact-version identity
-- excluded content hashes. A version coordinate is immutable and may name only
-- one implementation or compiled definition.
alter table market_bot.rule_versions
  drop constraint rule_versions_identity_key;
alter table market_bot.rule_versions
  add constraint rule_versions_identity_key unique (engine_id, rule_id, version);

alter table market_bot.strategy_versions
  drop constraint strategy_versions_identity_key;
alter table market_bot.strategy_versions
  add constraint strategy_versions_identity_key unique (engine_id, strategy_id, version);

grant market_bot_runtime to postgres;
