from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from mastermind_tick.api import create_app
from mastermind_tick.config import instrument_strategy, load_settings
from mastermind_tick.models import Bar, Tick


def test_health_and_empty_overview(tmp_path) -> None:
    base_settings = load_settings("config/settings.toml")
    settings = replace(
        base_settings,
        database_path=tmp_path / "paper.db",
        frontend_dist=tmp_path / "missing-frontend",
        live_futures=replace(
            base_settings.live_futures,
            database_path=tmp_path / "live-futures.db",
            credentials_path=None,
        ),
    )
    app = create_app(settings, start_engine=False)
    now_ms = 1_700_000_000_000
    for instrument in settings.instruments:
        app.state.store.ensure_account(instrument, settings.initial_cash, now_ms)

    with TestClient(app) as client:
        health = client.get("/api/health")
        overview = client.get("/api/overview")
        warehouse = client.get("/api/warehouse")
        live_readiness = client.get("/api/live/readiness")

    assert health.status_code == 200
    assert health.json()["service"] == "mastermind-tick"
    assert overview.status_code == 200
    assert [item["id"] for item in overview.json()["accounts"]] == [
        "soxl_perp_long",
        "soxl_perp",
    ]
    assert {item["id"] for item in overview.json()["accounts"]} == {
        "soxl_perp",
        "soxl_perp_long",
    }
    assert overview.json()["instruments"] == []
    assert overview.json()["environment"] == "paper"
    assert overview.json()["accounts"][0]["sharpe_ratio"] is None
    assert overview.json()["accounts"][0]["win_rate"] is None
    assert warehouse.status_code == 200
    assert warehouse.json()["instruments"][0]["instrument_id"] == "soxl_perp_long"
    assert warehouse.json()["instruments"][0]["market_data_id"] == "soxl_perp"
    assert warehouse.json()["instruments"][1]["symbol"] == "SOXLUSDT"
    assert live_readiness.status_code == 200
    assert live_readiness.json()["status"] == "STARTING"
    assert live_readiness.json()["order_submission_ready"] is False
    assert live_readiness.json()["credentials_present"] is False

    funding = client.get("/api/funding?account_id=soxl_perp")
    assert funding.status_code == 200
    assert funding.json() == []

    archived = client.get("/api/fills?account_id=soxlb")
    assert archived.status_code == 404


def test_active_strategy_uses_recommended_atr_parameters() -> None:
    settings = load_settings("config/settings.toml")

    assert settings.strategy.atr_period == 21
    assert settings.strategy.atr_multiplier == 4.0
    perp = next(item for item in settings.instruments if item.id == "soxl_perp")
    assert perp.leverage == 2
    assert perp.position_fraction == 0.625
    assert perp.leverage * perp.position_fraction == 1.25
    long_only = next(item for item in settings.instruments if item.id == "soxl_perp_long")
    assert long_only.market_id == perp.id
    assert not long_only.short_enabled
    assert settings.instruments[0].id == long_only.id
    long_strategy = instrument_strategy(settings, long_only)
    assert long_strategy.name == "soxl_long_atr32x3_v1"
    assert long_strategy.atr_period == 32
    assert long_strategy.atr_multiplier == 3.0
    assert long_strategy.position_fraction == 0.70
    assert long_strategy.reversal_confirmation_atr == 0.0
    perp_strategy = instrument_strategy(settings, perp)
    assert perp_strategy.atr_period == 21
    assert perp_strategy.atr_multiplier == 4.0
    assert {item.id for item in settings.instruments} == {"soxl_perp", "soxl_perp_long"}
    assert settings.live_spot.enabled is False
    assert settings.live_futures.strategy_name == "soxl_long_atr32x3_v1"
    assert settings.live_futures.allow_short is False
    assert settings.live_futures.atr_period == 32
    assert settings.live_futures.atr_multiplier == 3.0
    assert settings.live_futures.position_fraction == 0.70
    assert settings.live_futures.profit_activation_atr == 0
    assert settings.live_futures.continuation_reentry_atr == 0


def test_chart_endpoints_page_backwards_with_time_cursor(tmp_path) -> None:
    settings = replace(
        load_settings("config/settings.toml"),
        database_path=tmp_path / "paper.db",
        frontend_dist=tmp_path / "missing-frontend",
    )
    app = create_app(settings, start_engine=False)
    instrument = next(item for item in settings.instruments if item.id == "soxl_perp")
    app.state.store.ensure_account(instrument, 100_000, 1)
    for timestamp_ms in range(1, 26):
        app.state.store.snapshot(
            instrument.id,
            Tick(
                f"tick-{timestamp_ms}",
                timestamp_ms,
                Decimal("100"),
                Decimal("1"),
                "test",
            ),
        )
    app.state.store.upsert_history_bars(
        instrument,
        15,
        [
            Bar(
                start_ms=start_ms,
                end_ms=start_ms + 899_999,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
                trade_count=1,
            )
            for start_ms in (0, 900_000, 1_800_000)
        ],
        "test_history",
    )

    with TestClient(app) as client:
        equity = client.get(
            f"/api/accounts/{instrument.id}/equity?limit=20&before_ms=23"
        )
        ohlcv = client.get(
            f"/api/market/ohlcv?instrument_id={instrument.id}&limit=2&before_ms=1800000"
        )

    assert equity.status_code == 200
    assert [row["timestamp_ms"] for row in equity.json()] == list(range(3, 23))
    assert ohlcv.status_code == 200
    assert [row["start_ms"] for row in ohlcv.json()] == [900_000, 0]


def test_reconstructed_signals_endpoint_is_separate_from_fills(tmp_path) -> None:
    settings = replace(
        load_settings("config/settings.toml"),
        database_path=tmp_path / "paper.db",
        frontend_dist=tmp_path / "missing-frontend",
    )
    app = create_app(settings, start_engine=False)
    instrument = next(item for item in settings.instruments if item.id == "soxl_perp")
    app.state.store.ensure_account(instrument, settings.initial_cash, 1)
    with app.state.store.connection() as connection:
        connection.execute(
            """
            INSERT INTO reconstructed_signals (
                id, account_id, timestamp_ms, side, action, price, atr,
                trailing_stop, reason, source, replay_start_ms, replay_end_ms,
                created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "reconstructed:test",
                instrument.id,
                100,
                "BUY",
                "CLOSE",
                "123",
                "2",
                "121",
                "replayed_cross",
                "reconstructed_aggtrade_rest",
                1,
                200,
                300,
            ),
        )

    with TestClient(app) as client:
        signals = client.get(f"/api/reconstructed-signals?account_id={instrument.id}")
        fills = client.get(f"/api/fills?account_id={instrument.id}")

    assert signals.status_code == 200
    assert signals.json()[0]["action"] == "CLOSE"
    assert fills.json() == []


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
