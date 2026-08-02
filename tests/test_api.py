from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from mastermind_tick.api import create_app
from mastermind_tick.config import load_settings
from mastermind_tick.models import Tick


def test_health_and_empty_overview(tmp_path) -> None:
    settings = replace(
        load_settings("config/settings.toml"),
        database_path=tmp_path / "paper.db",
        frontend_dist=tmp_path / "missing-frontend",
    )
    app = create_app(settings, start_engine=False)
    now_ms = 1_700_000_000_000
    for instrument in settings.instruments:
        app.state.store.ensure_account(instrument, settings.initial_cash, now_ms)

    with TestClient(app) as client:
        health = client.get("/api/health")
        overview = client.get("/api/overview")
        warehouse = client.get("/api/warehouse")

    assert health.status_code == 200
    assert health.json()["service"] == "mastermind-tick"
    assert overview.status_code == 200
    assert {item["id"] for item in overview.json()["accounts"]} == {"soxlb", "soxl_perp"}
    assert overview.json()["instruments"] == []
    assert overview.json()["environment"] == "paper"
    assert overview.json()["accounts"][0]["sharpe_ratio"] is None
    assert overview.json()["accounts"][0]["win_rate"] is None
    assert warehouse.status_code == 200
    assert warehouse.json()["instruments"][0]["symbol"] == "SOXLBUSDT"
    assert warehouse.json()["instruments"][1]["symbol"] == "SOXLUSDT"

    funding = client.get("/api/funding?account_id=soxl_perp")
    assert funding.status_code == 200
    assert funding.json() == []


def test_active_strategy_uses_recommended_atr_parameters() -> None:
    settings = load_settings("config/settings.toml")

    assert settings.strategy.atr_period == 21
    assert settings.strategy.atr_multiplier == 4.0
    perp = next(item for item in settings.instruments if item.id == "soxl_perp")
    assert perp.leverage == 2
    assert perp.position_fraction == 0.625
    assert perp.leverage * perp.position_fraction == 1.25


def test_return_summary_uses_period_boundary_equity(tmp_path) -> None:
    settings = replace(
        load_settings("config/settings.toml"),
        database_path=tmp_path / "paper.db",
        frontend_dist=tmp_path / "missing-frontend",
    )
    app = create_app(settings, start_engine=False)
    instrument = next(item for item in settings.instruments if item.id == "soxl_perp")
    day_ms = 86_400_000
    created_at_ms = 1_767_225_600_000  # 2026-01-01 00:00:00 UTC
    app.state.store.ensure_account(instrument, 100_000, created_at_ms)
    app.state.store.snapshot(
        instrument.id,
        Tick("day-one", created_at_ms + day_ms // 2, Decimal("100"), Decimal("1"), "test"),
    )
    with app.state.store.connection() as connection:
        connection.execute("UPDATE accounts SET cash = '101000' WHERE id = ?", (instrument.id,))
    app.state.store.snapshot(
        instrument.id,
        Tick(
            "day-two",
            created_at_ms + 2 * day_ms - 30 * 60_000,
            Decimal("100"),
            Decimal("1"),
            "test",
        ),
    )

    with TestClient(app) as client:
        response = client.get(
            f"/api/accounts/{instrument.id}/returns?timezone_offset_minutes=0"
        )
        tokyo_response = client.get(
            f"/api/accounts/{instrument.id}/returns?timezone_offset_minutes=540"
        )

    assert response.status_code == 200
    result = response.json()
    assert len(result["daily"]) == 30
    assert result["daily"][-1]["label"] == "2026-01-02"
    assert result["daily"][-1]["return"] == pytest.approx(0.01)
    assert result["return_30d"] == pytest.approx(0.01)
    assert result["current_week_return"] == pytest.approx(0.01)
    assert result["current_month_return"] == pytest.approx(0.01)
    assert result["annualized_return"] is not None
    assert tokyo_response.status_code == 200
    assert tokyo_response.json()["daily"][-1]["label"] == "2026-01-03"
    assert tokyo_response.json()["daily"][-1]["return"] == pytest.approx(0.01)
