from types import SimpleNamespace

from src.entry import apply_cli_overrides


def _config():
    return SimpleNamespace(alignment_params=SimpleNamespace(auto_align=False))


def test_auto_align_cli_override_is_applied_without_mutating_config():
    original = _config()
    overridden = apply_cli_overrides(original, {"autoAlign": True})

    assert overridden.alignment_params.auto_align is True
    assert original.alignment_params.auto_align is False


def test_auto_align_cli_override_preserves_config_when_flag_is_absent():
    original = _config()
    resolved = apply_cli_overrides(original, {"autoAlign": False})

    assert resolved is original
    assert resolved.alignment_params.auto_align is False
