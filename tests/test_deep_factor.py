import sys

from mastermind_tick.deep_factor import DeepFactorConfig


def test_deep_factor_module_keeps_torch_as_worker_only_dependency() -> None:
    assert "torch" not in sys.modules
    config = DeepFactorConfig()
    assert config.instruments == ("btc_perp", "eth_perp")
    assert config.horizons == (4, 16, 96)
    assert config.sequence_length == 96
