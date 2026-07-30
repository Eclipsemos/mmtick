from dataclasses import replace

from fastapi.testclient import TestClient

from mastermind_tick.api import create_app
from mastermind_tick.config import load_settings


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

    assert health.status_code == 200
    assert health.json()["service"] == "mastermind-tick"
    assert overview.status_code == 200
    assert {item["id"] for item in overview.json()["accounts"]} == {"soxlb"}
    assert overview.json()["instruments"] == []
    assert overview.json()["environment"] == "paper"
