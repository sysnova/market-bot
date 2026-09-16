import gc
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from time import monotonic, sleep

import pytest

from app.common import context_cache
from app.common.context_cache import context_store, grouped_context_store
from app.contracts import AnalysisHorizon, BarTimeframe, MarketBar
from app.integration import ticker_cache_transport
from app.integration.market_bar_store import MarketBarStore
from app.integration.tests.test_market_bar_store import bar
from app.integration.ticker_cache_transport import CacheClient, make_cache_server


@pytest.fixture
def cache_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[CacheClient]:
    server = make_cache_server("isolated-test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = CacheClient(server.server_port, "isolated-test-token")
    monkeypatch.setattr(ticker_cache_transport, "_client", client)
    monkeypatch.setattr(context_cache, "_backend", client)
    try:
        yield client
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shared_histories_retain_no_process_local_series(cache_client: CacheClient) -> None:
    first = MarketBarStore(capacity_per_series=3)
    second = MarketBarStore(capacity_per_series=1)
    original = bar(minute=0, close="100")
    first.add(original)
    second.add(MarketBar.model_validate_json(original.model_dump_json()))
    assert first.history("aapl", BarTimeframe.MINUTE_1) == (original,)
    assert second.history("AAPL", BarTimeframe.MINUTE_1) == (original,)
    assert first._series == second._series == {}
    assert first._pending == second._pending == []
    assert cache_client.call("stats")["unique_payloads"] == 1
    first.add(bar(minute=1, close="105"))
    assert len(first.history("AAPL", BarTimeframe.MINUTE_1)) == 2
    assert second.history("AAPL", BarTimeframe.MINUTE_1) == (original,)
    first.retain_symbols(())
    assert first.history("AAPL", BarTimeframe.MINUTE_1) == ()
    assert cache_client.call("stats")["unique_payloads"] == 1


def test_context_snapshots_preserve_enum_keys_and_mutable_mapping_semantics(
    cache_client: CacheClient,
) -> None:
    groups = grouped_context_store(AnalysisHorizon, MarketBar)
    item = bar(minute=0, close="100")
    groups.setdefault("AAPL", {})[AnalysisHorizon.SWING] = item
    assert list(groups["AAPL"]) == [AnalysisHorizon.SWING]
    assert next(iter(groups["AAPL"])).value == "SWING"
    assert list(groups["AAPL"].items()) == [(AnalysisHorizon.SWING, item)]
    other = context_store(MarketBar)
    other["AAPL"] = item
    assert cache_client.call("stats")["unique_payloads"] == 1
    assert other.pop("AAPL") == item
    with pytest.raises(KeyError):
        del other["missing"]
    assert list(groups["AAPL"].values()) == [item]


def test_separate_process_uses_same_ram_owner(cache_client: CacheClient) -> None:
    view = cache_client.view()
    cache_client.call("put", view, "AAPL", "shared-analysis")
    script = """
import json, sys
from app.integration.ticker_cache_transport import CacheClient
client = CacheClient(int(sys.argv[1]), 'isolated-test-token')
view = client.view()
client.call('put', view, 'AAPL', 'shared-analysis')
print(json.dumps(client.call('stats')))
client.close()
"""
    result = subprocess.run(  # noqa: S603 - isolated local Python child, no user command.
        [sys.executable, "-c", script, str(cache_client.port)],
        capture_output=True, text=True, check=True, timeout=20,
    )
    stats = json.loads(result.stdout)
    assert stats["unique_payloads"] == 1
    assert stats["references"] == 2
    assert cache_client.call("stats")["references"] == 1


async def test_existing_worker_behavior_with_remote_history(cache_client: CacheClient) -> None:
    from app.integration.tests.test_long_term_worker import (
        test_long_worker_owns_history_and_accepts_explicit_final_daily_bar,
        test_long_worker_reprices_completed_history_from_live_minutes,
    )
    from app.integration.tests.test_swing_trade_composition import (
        test_momentum_history_bootstraps_four_hour_and_rolls_daily_after_close,
        test_observations_refresh_without_duplicate_signals_or_transitions,
    )

    await test_long_worker_owns_history_and_accepts_explicit_final_daily_bar()
    await test_long_worker_reprices_completed_history_from_live_minutes()
    await test_momentum_history_bootstraps_four_hour_and_rolls_daily_after_close()
    await test_observations_refresh_without_duplicate_signals_or_transitions()


def test_cache_failure_never_silently_creates_a_private_copy(cache_client: CacheClient) -> None:
    store = MarketBarStore()
    cache_client.call("release")
    store.add(bar(minute=0, close="100"))
    with pytest.raises(RuntimeError, match="shared ticker cache rejected"):
        store.history("AAPL", BarTimeframe.MINUTE_1)
    assert store._series == {}


def test_discarded_view_releases_its_last_payload(cache_client: CacheClient) -> None:
    values = context_store(MarketBar)
    values["AAPL"] = bar(minute=0, close="100")
    assert cache_client.call("stats")["unique_payloads"] == 1
    del values
    gc.collect()
    assert cache_client.call("stats")["unique_payloads"] == 0


def test_invalid_token_cannot_read_cache(cache_client: CacheClient) -> None:
    invalid = CacheClient(cache_client.port, "wrong")
    with pytest.raises(RuntimeError, match="403"):
        invalid.call("stats")


def test_alert_restore_and_recovery_use_shared_contexts(cache_client: CacheClient) -> None:
    from app.alert_engine.tests.test_v3 import (
        test_v31_restart_remembers_already_confirmed_market_session,
        test_v31_restart_restores_latest_swing_and_pending_candidate,
    )
    from app.entry_recovery_engine.tests.test_engine import (
        test_recovery_emits_after_intraday_stop_when_swing_thesis_reconfirms,
        test_recovery_signal_is_idempotent_per_opportunity,
    )

    test_v31_restart_restores_latest_swing_and_pending_candidate()
    test_v31_restart_remembers_already_confirmed_market_session()
    test_recovery_emits_after_intraday_stop_when_swing_thesis_reconfirms()
    test_recovery_signal_is_idempotent_per_opportunity()


def test_cli_starts_cache_and_clients_release_their_leases(tmp_path: Path) -> None:
    endpoint = tmp_path / "endpoint.json"
    ready = tmp_path / "ready.json"
    process = subprocess.Popen(  # noqa: S603 - launch only our isolated service.
        [
            sys.executable, "-m", "app.operator_cli", "serve", "ticker-cache",
            "--endpoint-path", str(endpoint), "--ready-path", str(ready),
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    inspector = None
    try:
        deadline = monotonic() + 15
        while not ready.exists() and monotonic() < deadline and process.poll() is None:
            sleep(0.05)
        assert ready.exists(), "cache service failed to publish readiness"
        config = json.loads(endpoint.read_text())
        assert config["token"] not in ready.read_text()
        result = subprocess.run(  # noqa: S603 - the same CLI arguments the plan supplies.
            [
                sys.executable, "-m", "app.operator_cli", "--shared-cache", str(endpoint),
                "serve", "ticker-cache-stats", "--endpoint-path", str(endpoint),
            ],
            capture_output=True, text=True, check=True, timeout=15,
        )
        assert json.loads(result.stdout)["unique_payloads"] == 0
        assert config["token"] not in result.stdout
        inspector = CacheClient(config["port"], config["token"])
        assert inspector.call("stats")["owners"] == 1
    finally:
        if inspector is not None:
            inspector.close()
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
