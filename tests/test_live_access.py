import sqlite3
from dataclasses import replace

from fastapi.testclient import TestClient

from mastermind_tick.api import create_app
from mastermind_tick.config import load_settings
from mastermind_tick.live_access import COOKIE_NAME, LiveAccess
from mastermind_tick.live_futures import LiveOperationError
from mastermind_tick.live_store import LiveStore


def _live_app(tmp_path, token: str = "a" * 48):
    token_path = tmp_path / "operator.token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    base = load_settings("config/settings.toml")
    settings = replace(
        base,
        database_path=tmp_path / "paper.db",
        frontend_dist=tmp_path / "missing-frontend",
        live_futures=replace(
            base.live_futures,
            database_path=tmp_path / "live-futures.db",
            credentials_path=None,
            operator_token_path=token_path,
        ),
    )
    app = create_app(settings, start_engine=False)
    app.state.live_store.save_futures_snapshot(
        account_id=settings.live_futures.account_id,
        timestamp_ms=1_700_000_000_000,
        wallet_balance="400",
        margin_balance="400",
        available_balance="400",
        unrealized_pnl="0",
        position_quantity="0",
        entry_price="0",
        mark_price="100",
        liquidation_price=None,
        leverage=2,
        margin_type="isolated",
        position_side="FLAT",
        atr="2.5",
        trailing_stop="96",
        relation="above",
    )
    return app


def test_live_access_rejects_insecure_token_file(tmp_path) -> None:
    token_path = tmp_path / "operator.token"
    token_path.write_text("a" * 48)
    token_path.chmod(0o644)

    access = LiveAccess(token_path)

    assert not access.configured
    assert not access.verify_token("a" * 48)


def test_live_data_requires_operator_session_and_remote_token(tmp_path) -> None:
    token = "b" * 48
    app = _live_app(tmp_path, token)

    with TestClient(app, client=("203.0.113.10", 4321)) as client:
        session = client.get("/api/live/session")
        unauthorized = client.get("/api/live/overview")
        local_unlock = client.post("/api/live/unlock-local")
        invalid = client.post("/api/live/unlock", json={"token": "wrong"})
        unlocked = client.post("/api/live/unlock", json={"token": token})
        overview = client.get("/api/live/overview")
        equity = client.get("/api/live/equity")
        logout = client.post("/api/live/logout")
        locked_again = client.get("/api/live/overview")

    assert session.json() == {
        "authenticated": False,
        "configured": True,
        "local_unlock_available": False,
    }
    assert unauthorized.status_code == 401
    assert local_unlock.status_code == 403
    assert invalid.status_code == 401
    assert unlocked.status_code == 200
    cookie = unlocked.headers["set-cookie"]
    assert COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert overview.status_code == 200
    assert [account["id"] for account in overview.json()["accounts"]] == [
        "soxl_perp_live"
    ]
    assert overview.json()["accounts"][0]["equity"] == "400"
    assert equity.json()[0]["atr"] == "2.5"
    assert logout.status_code == 200
    assert locked_again.status_code == 401

    serialized = overview.text.lower()
    assert "api_key" not in serialized
    assert "secret_key" not in serialized
    assert token not in overview.text


def test_loopback_can_establish_live_session_without_entering_token(tmp_path) -> None:
    app = _live_app(tmp_path)

    with TestClient(app) as client:
        unlocked = client.post("/api/live/unlock-local")
        session = client.get("/api/live/session")
        fills = client.get("/api/live/fills")
        events = client.get("/api/live/events")

    assert unlocked.status_code == 200
    assert session.json()["authenticated"] is True
    assert fills.status_code == 200
    assert fills.json() == []
    assert events.status_code == 200
    assert events.json() == []


def test_live_operator_actions_require_session_and_explicit_flatten_confirmation(
    tmp_path, monkeypatch
) -> None:
    token = "c" * 48
    app = _live_app(tmp_path, token)
    flatten_calls = 0

    async def fake_flatten() -> dict:
        nonlocal flatten_calls
        flatten_calls += 1
        return {
            "ok": True,
            "already_flat": False,
            "flat_confirmed": True,
            "orders": [{"status": "FILLED"}],
        }

    monkeypatch.setattr(app.state.live_trader, "manual_flatten", fake_flatten)
    with TestClient(app, client=("203.0.113.10", 4321)) as client:
        unauthorized_control = client.post("/api/live/control", json={"action": "stop"})
        unauthorized_flatten = client.post(
            "/api/live/flatten", json={"confirm": "FLATTEN_SOXLUSDT"}
        )
        assert client.post("/api/live/unlock", json={"token": token}).status_code == 200
        invalid_confirmation = client.post(
            "/api/live/flatten", json={"confirm": "not-confirmed"}
        )
        invalid_resume = client.post("/api/live/control", json={"action": "resume"})
        stopped = client.post("/api/live/control", json={"action": "stop"})
        flattened = client.post(
            "/api/live/flatten", json={"confirm": "FLATTEN_SOXLUSDT"}
        )

    assert unauthorized_control.status_code == 401
    assert unauthorized_flatten.status_code == 401
    assert invalid_confirmation.status_code == 422
    assert invalid_resume.status_code == 422
    assert stopped.json()["strategy_paused"] is True
    assert app.state.live_store.metadata("trading_paused") == "true"
    assert flattened.status_code == 200
    assert flattened.json()["orders"][0]["status"] == "FILLED"
    assert flatten_calls == 1


def test_live_flatten_conflict_is_returned_as_safe_api_error(tmp_path, monkeypatch) -> None:
    app = _live_app(tmp_path)

    async def reject_flatten() -> dict:
        raise LiveOperationError("OPEN_ORDER_PRESENT", "Open order present")

    monkeypatch.setattr(app.state.live_trader, "manual_flatten", reject_flatten)
    with TestClient(app) as client:
        assert client.post("/api/live/unlock-local").status_code == 200
        response = client.post(
            "/api/live/flatten", json={"confirm": "FLATTEN_SOXLUSDT"}
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "OPEN_ORDER_PRESENT",
        "message": "Open order present",
    }


def test_live_performance_excludes_deposits_but_keeps_actual_equity(tmp_path) -> None:
    app = _live_app(tmp_path)
    store = app.state.live_store
    store.save_futures_snapshot(
        account_id="soxl_perp_live",
        timestamp_ms=1_700_000_005_000,
        wallet_balance="2000",
        margin_balance="2000",
        available_balance="2000",
        unrealized_pnl="0",
        position_quantity="0",
        entry_price="0",
        mark_price="100",
        liquidation_price=None,
        leverage=2,
        margin_type="isolated",
        position_side="FLAT",
    )
    store.record_cash_flow(
        flow_id="confirmed-deposit",
        account_id="soxl_perp_live",
        timestamp_ms=1_700_000_005_000,
        amount_quote="1600",
        flow_type="DEPOSIT",
        reason="operator_confirmed_deposit",
        source="operator_adjustment",
        created_at_ms=1_700_000_006_000,
    )
    store.save_futures_snapshot(
        account_id="soxl_perp_live",
        timestamp_ms=1_700_000_010_000,
        wallet_balance="2020",
        margin_balance="2020",
        available_balance="2020",
        unrealized_pnl="0",
        position_quantity="0",
        entry_price="0",
        mark_price="100",
        liquidation_price=None,
        leverage=2,
        margin_type="isolated",
        position_side="FLAT",
    )

    with TestClient(app) as client:
        assert client.post("/api/live/unlock-local").status_code == 200
        account = client.get("/api/live/overview").json()["accounts"][0]
        returns = client.get("/api/live/returns?timezone_offset_minutes=0").json()

    assert account["equity"] == "2020"
    assert account["net_cash_flow"] == "1600"
    assert account["total_pnl"] == "20"
    assert account["total_return"] == 0.01
    assert returns["current_equity"] == "2020"
    assert returns["total_return"] == 0.01
    assert returns["daily"][-1]["equity"] == "2020"


def test_live_store_migrates_balance_indicator_columns(tmp_path) -> None:
    path = tmp_path / "legacy-live.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE live_balance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                base_free TEXT NOT NULL,
                base_locked TEXT NOT NULL,
                quote_free TEXT NOT NULL,
                quote_locked TEXT NOT NULL,
                reference_price TEXT,
                equity_quote TEXT,
                source TEXT NOT NULL
            )
            """
        )

    store = LiveStore(path)
    with store.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(live_balance_snapshots)")
        }

    assert {"atr", "trailing_stop", "relation"} <= columns
