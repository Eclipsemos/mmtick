import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

import audit_btc_sma12_40_forward as forward  # noqa: E402


def test_forward_ledger_does_not_duplicate_period_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(forward, "OUTPUT", tmp_path)
    payload = {
        "generated_at": "2026-09-03T00:30:00+00:00",
        "period": ["2026-09-02T08:00:00+00:00", "2026-09-03T00:00:00+00:00"],
        "forward_bars": 65,
        "strategy_return": -0.01,
        "benchmark_return": -0.004,
        "excess": -0.006,
        "strategy_drawdown": -0.02,
        "benchmark_drawdown": -0.01,
        "maximum_intrabar_leverage": 2.1,
        "liquidated": False,
    }

    forward.append_ledger(payload)
    forward.append_ledger(payload)

    with (tmp_path / "ledger.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2026-09-03T00:00:00+00:00"
