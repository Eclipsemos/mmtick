import sys
from pathlib import Path

from mastermind_tick.bar_research import ResearchBar

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma11_coinbase_sanity import replay
from audit_btc_sma11_hybrid_forward import append_rows_once, target_changes


def _bar(index: int) -> ResearchBar:
    start = index * 15 * 60_000
    return ResearchBar(start, start + 15 * 60_000 - 1, 1, 1, 1, 1)


def test_target_changes_records_only_actual_target_transitions() -> None:
    bars = tuple(_bar(index) for index in range(7))
    changes = target_changes(
        bars,
        (None, 1.5, None, 1.5, None, 0, None),
        bars[3].start_ms,
    )

    assert changes == [
        {
            "signal_time": "1970-01-01T01:29:59.999000+00:00",
            "execution_time": "1970-01-01T01:30:00+00:00",
            "model_execution_price": "1",
            "target_exposure": "0",
        }
    ]


def test_coinbase_proxy_executes_a_daily_signal_at_the_next_open() -> None:
    bars = tuple(_bar(index) for index in range(3))
    result = replay(bars, (None, 1, 1), bars[0].start_ms, bars[-1].end_ms)

    assert result.rebalances == 1


def test_forward_csv_append_is_idempotent(tmp_path) -> None:
    path = tmp_path / "forward.csv"
    rows = [{"period_end": "2026-09-03T00:00:00+00:00", "value": 1}]

    append_rows_once(path, rows, key="period_end")
    append_rows_once(path, rows, key="period_end")

    assert path.read_text(encoding="utf-8").splitlines() == [
        "period_end,value",
        "2026-09-03T00:00:00+00:00,1",
    ]
