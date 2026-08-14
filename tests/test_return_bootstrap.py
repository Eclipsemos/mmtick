import pytest

from mastermind_tick.return_bootstrap import circular_block_bootstrap


def test_positive_constant_path_reaches_target_without_drawdown() -> None:
    result = circular_block_bootstrap(
        [0.06] * 10,
        horizon_days=30,
        block_size=3,
        simulations=100,
        seed=7,
    )

    assert result["probability_target_reached"] == 1
    assert result["probability_terminal_loss"] == 0
    assert result["probability_daily_close_ruin"] == 0
    assert result["geometric_daily_return"]["median"] == pytest.approx(0.06)
    assert result["max_daily_close_drawdown"]["median"] == 0


def test_bootstrap_is_deterministic_and_preserves_adjacent_blocks() -> None:
    arguments = {
        "horizon_days": 20,
        "block_size": 4,
        "simulations": 250,
        "seed": 20260814,
    }

    first = circular_block_bootstrap([0.10, -0.05, 0.02, 0.00], **arguments)
    second = circular_block_bootstrap([0.10, -0.05, 0.02, 0.00], **arguments)

    assert first == second
    assert first["source_days"] == 4
    assert 0 <= first["probability_target_reached"] <= 1
    assert first["max_daily_close_drawdown"]["p05"] <= first["max_daily_close_drawdown"]["p95"]


def test_compounding_loss_path_reports_near_ruin_drawdown() -> None:
    result = circular_block_bootstrap(
        [-0.60],
        horizon_days=2,
        block_size=1,
        simulations=10,
        seed=1,
    )

    assert result["probability_daily_close_drawdown_80"] == 1
    assert result["probability_daily_close_ruin"] == 0


@pytest.mark.parametrize(
    ("returns", "kwargs", "message"),
    [
        ([], {}, "must not be empty"),
        ([-1.0], {}, "greater than -100%"),
        ([0.01], {"block_size": 0}, "must be positive"),
    ],
)
def test_invalid_bootstrap_inputs_are_rejected(returns, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        circular_block_bootstrap(
            returns,
            horizon_days=30,
            block_size=kwargs.get("block_size", 1),
            simulations=10,
            seed=1,
        )
