from importlib import import_module


def test_entry_recovery_composition_imports_with_public_runtime_helpers() -> None:
    module = import_module("app.integration.entry_recovery_composition")

    assert callable(module.run_entry_recovery_process)
