from inspect import getsource

from app.integration import (
    distributed_composition,
    elliott_wave_composition,
    market_rotation_composition,
    patreon_caps_composition,
    support_confirmation_composition,
)


def test_analytical_compositions_do_not_construct_alpaca_rest_clients() -> None:
    assert "_build_rest(" not in getsource(distributed_composition.run_engine_process)
    for module in (
        elliott_wave_composition,
        market_rotation_composition,
        patreon_caps_composition,
        support_confirmation_composition,
    ):
        source = getsource(module)
        assert "build_rest(" not in source
        assert "fetch_bars(" not in source


def test_websocket_composition_does_not_construct_an_alpaca_rest_client() -> None:
    assert "_build_rest(" not in getsource(distributed_composition._build_stream_engine)
