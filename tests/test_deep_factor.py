import sys

from mastermind_tick.deep_factor import (
    DeepFactorConfig,
    _managed_targets,
    _signal_candidates,
    _SignalCandidate,
)


def test_deep_factor_module_keeps_torch_as_worker_only_dependency() -> None:
    assert "torch" not in sys.modules
    config = DeepFactorConfig()
    assert config.instruments == ("btc_perp", "eth_perp")
    assert config.horizons == (4, 16, 96)
    assert config.sequence_length == 96


def test_managed_targets_apply_hold_cooldown_and_confirmation_causally() -> None:
    candidate = _SignalCandidate(
        direction="long_only",
        entry_threshold=0.60,
        smoothing_bars=1,
        minimum_hold_bars=2,
        cooldown_bars=2,
        confirmation_bars=2,
    )

    targets = _managed_targets([None, 0.62, 0.63, 0.51, 0.51, 0.63, 0.63], candidate)

    # The second closed-bar confirmation opens the position. Two hold bars prevent an early
    # exit, then two flat bars are enforced before a fresh two-bar confirmation can re-enter.
    assert targets == (None, 0, 1, 1, 0, 0, 0)


def test_signal_candidate_library_is_small_and_deterministic() -> None:
    candidates = _signal_candidates()

    assert len(candidates) == 144
    assert len({candidate.id for candidate in candidates}) == len(candidates)
    assert {candidate.direction for candidate in candidates} == {"long_only", "long_short"}
