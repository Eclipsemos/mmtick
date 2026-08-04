"""FastAPI application for mastermind:tick paper trading."""

from __future__ import annotations

import csv
import io
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mastermind_tick.config import InstrumentSettings, Settings, load_settings
from mastermind_tick.engine import PaperEngine
from mastermind_tick.reporting import build_overview, build_return_summary
from mastermind_tick.store import PaperStore


class ControlRequest(BaseModel):
    action: Literal["pause", "resume"]


def create_app(settings: Settings | None = None, *, start_engine: bool = True) -> FastAPI:
    resolved = settings or load_settings(os.getenv("MMTICK_CONFIG", "config/settings.toml"))
    store = PaperStore(resolved.database_path)
    engine = PaperEngine(resolved, store)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if start_engine:
            await engine.start()
        yield
        if start_engine:
            await engine.stop()

    app = FastAPI(
        title="mastermind:tick API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.store = store
    app.state.engine = engine
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        states = [runtime.status for runtime in engine.runtimes.values()]
        return {
            "status": (
                "ok" if not states or any(state == "LIVE" for state in states) else "degraded"
            ),
            "service": "mastermind-tick",
            "environment": resolved.environment,
            "database": str(resolved.database_path),
        }

    @app.get("/api/overview")
    def overview() -> dict:
        return build_overview(engine, store)

    @app.get("/api/accounts/{account_id}/equity")
    def equity(
        account_id: str,
        limit: Annotated[int, Query(ge=20, le=10000)] = 1000,
        before_ms: Annotated[int | None, Query(gt=0)] = None,
    ) -> list[dict]:
        _require_account(store, account_id)
        return store.equity(account_id, limit, before_ms)

    @app.get("/api/accounts/{account_id}/returns")
    def returns(
        account_id: str,
        timezone_offset_minutes: Annotated[int, Query(ge=-720, le=840)] = 0,
    ) -> dict:
        _require_account(store, account_id)
        return build_return_summary(store, account_id, timezone_offset_minutes)

    @app.get("/api/fills")
    def fills(
        account_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        if account_id:
            _require_account(store, account_id)
        return store.fills(account_id, limit)

    @app.get("/api/orders")
    def orders(
        account_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        if account_id:
            _require_account(store, account_id)
        return store.orders(account_id, limit)

    @app.get("/api/reconstructed-signals")
    def reconstructed_signals(
        account_id: str = "soxl_perp",
        limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    ) -> list[dict]:
        _require_account(store, account_id)
        return store.reconstructed_signals(account_id, limit)

    @app.get("/api/events")
    def events(
        account_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        if account_id:
            _require_account(store, account_id)
        return store.events(account_id, limit)

    @app.get("/api/funding")
    def funding(
        account_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        if account_id:
            _require_account(store, account_id)
        return store.funding_payments(account_id, limit)

    @app.get("/api/warehouse")
    def warehouse() -> dict:
        return store.warehouse_summary(
            resolved.instruments,
            resolved.strategy.bar_minutes,
        )

    @app.get("/api/market/agg-trades")
    def agg_trades(
        instrument_id: str = "soxlb",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        instrument = _require_instrument(resolved, instrument_id)
        return store.agg_trades(instrument.market_id, limit)

    @app.get("/api/market/ohlcv")
    def ohlcv(
        instrument_id: str = "soxlb",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        before_ms: Annotated[int | None, Query(gt=0)] = None,
    ) -> list[dict]:
        instrument = _require_instrument(resolved, instrument_id)
        return store.ohlcv_bars(
            instrument.market_id,
            resolved.strategy.bar_minutes,
            limit,
            before_ms,
        )

    @app.get("/api/fills.csv")
    def export_fills(account_id: str | None = None) -> Response:
        if account_id:
            _require_account(store, account_id)
        rows = store.fills(account_id, 100_000)
        output = io.StringIO()
        fieldnames = (
            list(rows[0])
            if rows
            else [
                "id",
                "order_id",
                "account_id",
                "side",
                "timestamp_ms",
                "price",
                "quantity",
                "notional",
                "fee",
                "reason",
                "source",
            ]
        )
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=mmtick-fills.csv"},
        )

    @app.post("/api/control")
    async def control(request: ControlRequest) -> dict:
        if request.action == "pause":
            await engine.pause()
        else:
            await engine.resume()
        return {"ok": True, "trading_enabled": engine.trading_enabled}

    frontend_dist = resolved.frontend_dist
    if frontend_dist.exists():
        assets = frontend_dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str) -> FileResponse:
            requested = (frontend_dist / path).resolve()
            if path and requested.is_relative_to(frontend_dist.resolve()) and requested.is_file():
                return FileResponse(requested)
            return FileResponse(frontend_dist / "index.html")

    return app


def _require_account(store: PaperStore, account_id: str) -> None:
    try:
        store.account(account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_instrument(settings: Settings, instrument_id: str) -> InstrumentSettings:
    instrument = next((item for item in settings.instruments if item.id == instrument_id), None)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument: {instrument_id}")
    return instrument


app = create_app()
