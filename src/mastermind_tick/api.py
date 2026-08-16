"""FastAPI application for mastermind:tick paper trading."""

from __future__ import annotations

import csv
import io
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import RequestResponseEndpoint

from mastermind_tick.config import InstrumentSettings, Settings, load_settings
from mastermind_tick.engine import PaperEngine
from mastermind_tick.live_access import COOKIE_NAME, LiveAccess
from mastermind_tick.live_futures import LiveFuturesTrader, LiveOperationError
from mastermind_tick.live_futures_reporting import (
    build_live_futures_overview,
    build_live_futures_return_summary,
    live_futures_equity,
    live_futures_fills,
    live_futures_funding,
    live_futures_orders,
)
from mastermind_tick.live_store import LiveStore
from mastermind_tick.reporting import build_overview, build_return_summary
from mastermind_tick.store import PaperStore


class ControlRequest(BaseModel):
    action: Literal["pause", "resume"]


class LiveControlRequest(BaseModel):
    action: Literal["stop"]


class LiveUnlockRequest(BaseModel):
    token: str


class LiveFlattenRequest(BaseModel):
    confirm: Literal["FLATTEN_SOXLUSDT"]


def create_app(settings: Settings | None = None, *, start_engine: bool = True) -> FastAPI:
    resolved = settings or load_settings(os.getenv("MMTICK_CONFIG", "config/settings.toml"))
    store = PaperStore(resolved.database_path)
    engine = PaperEngine(resolved, store)
    live_store = LiveStore(resolved.live_futures.database_path)
    live_trader = LiveFuturesTrader(resolved, live_store)
    live_access = LiveAccess(resolved.live_futures.operator_token_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if start_engine:
            await engine.start()
            await live_trader.start(engine)
        yield
        if start_engine:
            await live_trader.stop()
            await engine.stop()

    app = FastAPI(
        title="mastermind:tick API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.store = store
    app.state.engine = engine
    app.state.live_store = live_store
    app.state.live_trader = live_trader
    app.state.live_access = live_access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def enforce_external_https(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0]
        if forwarded_proto == "http":
            return RedirectResponse(str(request.url.replace(scheme="https")), status_code=308)
        response = await call_next(request)
        if forwarded_proto == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

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
            "live_futures": {
                "status": live_trader.status,
                "order_submission_ready": live_trader.order_submission_ready,
            },
        }

    @app.get("/api/live/readiness")
    def live_readiness() -> dict:
        """Expose gates and health only; balances and order details stay private."""
        return live_trader.readiness()

    @app.get("/api/live/session")
    def live_session(request: Request) -> dict:
        return {
            "authenticated": live_access.authorized(request),
            "configured": live_access.configured,
            "local_unlock_available": live_access.is_loopback(request),
        }

    @app.post("/api/live/unlock")
    def live_unlock(payload: LiveUnlockRequest, request: Request, response: Response) -> dict:
        if not live_access.configured:
            raise HTTPException(status_code=503, detail="LIVE operator access is not configured")
        if not live_access.verify_token(payload.token):
            raise HTTPException(status_code=401, detail="Invalid LIVE operator token")
        live_access.establish(response, request)
        return {"ok": True, "authenticated": True}

    @app.post("/api/live/unlock-local")
    def live_unlock_local(request: Request, response: Response) -> dict:
        if not live_access.is_loopback(request):
            raise HTTPException(status_code=403, detail="Local LIVE unlock is unavailable")
        if not live_access.configured:
            raise HTTPException(status_code=503, detail="LIVE operator access is not configured")
        live_access.establish(response, request)
        return {"ok": True, "authenticated": True}

    @app.post("/api/live/logout")
    def live_logout(response: Response) -> dict:
        response.delete_cookie(COOKIE_NAME, path="/api/live", samesite="strict")
        return {"ok": True, "authenticated": False}

    @app.post("/api/live/control")
    async def live_control(payload: LiveControlRequest, request: Request) -> dict:
        live_access.require(request)
        return await live_trader.set_strategy_paused(True)

    @app.post("/api/live/flatten")
    async def live_flatten(payload: LiveFlattenRequest, request: Request) -> dict:
        live_access.require(request)
        try:
            return await live_trader.manual_flatten()
        except LiveOperationError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.get("/api/live/overview")
    def live_overview(request: Request) -> dict:
        live_access.require(request)
        return build_live_futures_overview(resolved, engine, live_store, live_trader)

    @app.get("/api/live/equity")
    def live_account_equity(
        request: Request,
        limit: Annotated[int, Query(ge=20, le=10000)] = 1000,
        before_ms: Annotated[int | None, Query(gt=0)] = None,
    ) -> list[dict]:
        live_access.require(request)
        return live_futures_equity(live_store, resolved.live_futures.account_id, limit, before_ms)

    @app.get("/api/live/returns")
    def live_account_returns(
        request: Request,
        timezone_offset_minutes: Annotated[int, Query(ge=-720, le=840)] = 0,
    ) -> dict:
        live_access.require(request)
        try:
            return build_live_futures_return_summary(
                live_store,
                resolved.live_futures.account_id,
                timezone_offset_minutes,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="No LIVE balance snapshots") from exc

    @app.get("/api/live/fills")
    def live_account_fills(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        live_access.require(request)
        return live_futures_fills(live_store, resolved.live_futures.account_id, limit)

    @app.get("/api/live/orders")
    def live_account_orders(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        live_access.require(request)
        return live_futures_orders(live_store, resolved.live_futures.account_id, limit)

    @app.get("/api/live/funding")
    def live_account_funding(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    ) -> list[dict]:
        live_access.require(request)
        return live_futures_funding(live_store, resolved.live_futures.account_id, limit)

    @app.get("/api/live/events")
    def live_events(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[dict]:
        live_access.require(request)
        return live_store.events(resolved.live_futures.account_id, limit)

    @app.get("/api/live/fills.csv")
    def export_live_fills(request: Request) -> Response:
        live_access.require(request)
        rows = live_futures_fills(live_store, resolved.live_futures.account_id, 100_000)
        return _fills_csv(rows, "mmtick-live-fills.csv")

    @app.get("/api/overview")
    def overview() -> dict:
        return build_overview(engine, store)

    @app.get("/api/accounts/{account_id}/equity")
    def equity(
        account_id: str,
        limit: Annotated[int, Query(ge=20, le=10000)] = 1000,
        before_ms: Annotated[int | None, Query(gt=0)] = None,
    ) -> list[dict]:
        _require_account(resolved, store, account_id)
        return store.equity(account_id, limit, before_ms)

    @app.get("/api/accounts/{account_id}/returns")
    def returns(
        account_id: str,
        timezone_offset_minutes: Annotated[int, Query(ge=-720, le=840)] = 0,
    ) -> dict:
        _require_account(resolved, store, account_id)
        return build_return_summary(store, account_id, timezone_offset_minutes)

    @app.get("/api/accounts/{account_id}/portfolio-ledger")
    def portfolio_ledger(
        account_id: str,
        ledger: Annotated[str, Query(pattern="^(base|stress)$")] = "base",
    ) -> list[dict]:
        _require_account(resolved, store, account_id)
        if not resolved.portfolio_paper.enabled or account_id != resolved.portfolio_paper.id:
            raise HTTPException(status_code=404, detail=f"not a portfolio account: {account_id}")
        return store.portfolio_ledger(account_id, ledger)

    @app.get("/api/accounts/{account_id}/portfolio-sleeve-events")
    def portfolio_sleeve_events(
        account_id: str,
        ledger: Annotated[str, Query(pattern="^(base|stress)$")] = "base",
        day: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    ) -> list[dict]:
        _require_account(resolved, store, account_id)
        if not resolved.portfolio_paper.enabled or account_id != resolved.portfolio_paper.id:
            raise HTTPException(status_code=404, detail=f"not a portfolio account: {account_id}")
        return store.portfolio_sleeve_events(account_id, ledger, day)

    @app.get("/api/fills")
    def fills(
        account_id: str = "soxl_perp_long",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        _require_account(resolved, store, account_id)
        return store.fills(account_id, limit)

    @app.get("/api/orders")
    def orders(
        account_id: str = "soxl_perp_long",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        _require_account(resolved, store, account_id)
        return store.orders(account_id, limit)

    @app.get("/api/reconstructed-signals")
    def reconstructed_signals(
        account_id: str = "soxl_perp_long",
        limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    ) -> list[dict]:
        _require_account(resolved, store, account_id)
        return store.reconstructed_signals(account_id, limit)

    @app.get("/api/events")
    def events(
        account_id: str = "soxl_perp_long",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        _require_account(resolved, store, account_id)
        return store.events(account_id, limit)

    @app.get("/api/funding")
    def funding(
        account_id: str = "soxl_perp_long",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        _require_account(resolved, store, account_id)
        return store.funding_payments(account_id, limit)

    @app.get("/api/warehouse")
    def warehouse() -> dict:
        return store.warehouse_summary(
            resolved.instruments,
            resolved.strategy.bar_minutes,
        )

    @app.get("/api/market/agg-trades")
    def agg_trades(
        instrument_id: str = "soxl_perp",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict]:
        instrument = _require_instrument(resolved, instrument_id)
        return store.agg_trades(instrument.market_id, limit)

    @app.get("/api/market/ohlcv")
    def ohlcv(
        instrument_id: str = "soxl_perp",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        before_ms: Annotated[int | None, Query(gt=0)] = None,
        interval_minutes: Annotated[int, Query()] = 15,
    ) -> list[dict]:
        if interval_minutes not in {15, 240, 1440}:
            raise HTTPException(status_code=422, detail="unsupported OHLCV interval")
        instrument = _require_instrument(resolved, instrument_id)
        return store.ohlcv_bars(
            instrument.market_id,
            interval_minutes,
            limit,
            before_ms,
        )

    @app.get("/api/fills.csv")
    def export_fills(account_id: str = "soxl_perp_long") -> Response:
        _require_account(resolved, store, account_id)
        rows = store.fills(account_id, 100_000)
        return _fills_csv(rows, "mmtick-fills.csv")

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


def _require_account(settings: Settings, store: PaperStore, account_id: str) -> None:
    configured = {instrument.id for instrument in settings.instruments if instrument.paper_enabled}
    if settings.portfolio_paper.enabled:
        configured.add(settings.portfolio_paper.id)
    if account_id not in configured:
        raise HTTPException(status_code=404, detail=f"unknown account: {account_id}")
    try:
        store.account(account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_instrument(settings: Settings, instrument_id: str) -> InstrumentSettings:
    instrument = next((item for item in settings.instruments if item.id == instrument_id), None)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument: {instrument_id}")
    return instrument


def _fills_csv(rows: list[dict], filename: str) -> Response:
    output = io.StringIO()
    fieldnames = (
        list(rows[0])
        if rows
        else [
            "id",
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
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


app = create_app()
