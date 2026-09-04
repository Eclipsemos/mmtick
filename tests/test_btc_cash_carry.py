import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_cash_carry import (  # noqa: E402
    BASIS_THRESHOLDS,
    FUNDING_THRESHOLDS,
    CarryCandidate,
    PairBar,
    bps_label,
    candidate_library,
    carry_replay,
    carry_targets,
    four_hour_signal_indices,
    gap_count,
    normalize_archive_timestamp,
)


def _bar(index: int, spot: str = "100", perp: str = "101") -> PairBar:
    start = index * 900_000
    return PairBar(
        start,
        start + 899_999,
        Decimal(spot),
        Decimal(spot),
        Decimal(spot),
        Decimal(spot),
        Decimal(perp),
        Decimal(perp),
        Decimal(perp),
        Decimal(perp),
    )


def test_cash_carry_grid_is_predeclared() -> None:
    assert len(candidate_library()) == len(BASIS_THRESHOLDS) * len(FUNDING_THRESHOLDS)


def test_bps_label_avoids_decimal_exponent_padding() -> None:
    assert bps_label(Decimal("0.002")) == "20"
    assert bps_label(Decimal("0.000025")) == "0p25"


def test_spot_microsecond_archive_timestamp_normalizes_to_milliseconds() -> None:
    assert normalize_archive_timestamp("1735689600000000") == 1_735_689_600_000


def test_four_hour_signal_is_not_emitted_until_all_sixteen_bars_close() -> None:
    assert four_hour_signal_indices([_bar(index) for index in range(17)]) == (15,)


def test_pair_continuity_accepts_adjacent_bars() -> None:
    assert gap_count([_bar(0), _bar(1)]) == 0


def test_pair_continuity_counts_unobserved_perpetual_gaps() -> None:
    assert gap_count([_bar(0), _bar(2)]) == 1


def test_carry_signal_requires_positive_basis_and_settled_funding() -> None:
    bars = [_bar(index) for index in range(16)]
    candidate = CarryCandidate(Decimal("0.005"), Decimal("0.0001"))
    funding = [[] for _ in bars]

    assert carry_targets(bars, funding, candidate)[15] == 0


def test_cash_carry_charges_both_legs_on_entry_and_exit() -> None:
    bars = [_bar(0, perp="100"), _bar(1, perp="100")]

    result = carry_replay(
        bars,
        (Decimal("1"), None),
        [[], []],
        bars[0].start_ms,
        bars[-1].end_ms,
    )

    assert result["net_return"] == -0.003
    assert result["maximum_gross_leverage"] > 1
    assert result["maximum_gross_leverage"] < 3
