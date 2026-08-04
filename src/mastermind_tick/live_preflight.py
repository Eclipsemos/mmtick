"""Signed account checks and no-fill preflight for SOXL USD-M Futures."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from decimal import Decimal
from typing import Any

from mastermind_tick.binance_futures import (
    BinanceFuturesAPIError,
    BinanceFuturesClient,
    FuturesSymbolRules,
)
from mastermind_tick.config import load_settings
from mastermind_tick.live_spot import load_live_credentials
from mastermind_tick.live_store import LiveStore


async def run(
    config_path: str,
    *,
    test_order: bool,
    sign_tradfi_contract: bool = False,
    client: BinanceFuturesClient | None = None,
) -> int:
    settings = load_settings(config_path)
    live = settings.live_futures
    instrument = next(item for item in settings.instruments if item.id == live.instrument_id)
    owns_client = client is None
    store = LiveStore(live.database_path) if owns_client else None
    credential_error: str | None = None
    if client is None:
        api_key, api_secret, credential_error = load_live_credentials(
            live.credentials_path,
            live.api_key_env,
            live.api_secret_env,
        )
        client = BinanceFuturesClient(
            live.api_base_url,
            api_key,
            api_secret,
            spot_api_base_url=live.spot_api_base_url,
            recv_window_ms=live.recv_window_ms,
        )
    result: dict[str, Any] = {
        "product": "Binance USD-M Futures",
        "symbol": instrument.symbol,
        "credentials_present": client.has_credentials,
        "credential_error": credential_error,
        "real_orders_sent": False,
    }
    try:
        rules = await client.symbol_rules(instrument.symbol)
        book = await client.book_ticker(instrument.symbol)
        result["public"] = {
            "status": rules.status,
            "market_order_allowed": rules.market_order_allowed,
            "quantity_step": str(rules.quantity_step),
            "minimum_quantity": str(rules.minimum_quantity),
            "minimum_notional": str(rules.minimum_notional),
            "bid_price": str(book["bidPrice"]),
            "ask_price": str(book["askPrice"]),
        }
        if not client.has_credentials:
            result["signed"] = {"ok": False, "reason": "credentials_missing"}
            print(json.dumps(result, indent=2))
            return 2

        offset = await client.sync_time()
        if sign_tradfi_contract:
            try:
                contract = await client.sign_tradfi_perps_contract()
            except BinanceFuturesAPIError as exc:
                result["tradfi_contract"] = {
                    "ok": False,
                    "endpoint": "/fapi/v1/stock/contract",
                    "code": exc.code,
                    "message": exc.message,
                }
                print(json.dumps(result, indent=2))
                return 1
            contract_code = contract.get("code")
            contract_message = str(contract.get("msg", ""))
            result["tradfi_contract"] = {
                "ok": contract_code in {None, 200}
                and contract_message.casefold() == "success",
                "endpoint": "/fapi/v1/stock/contract",
                "code": contract_code,
                "message": contract_message,
            }
        account, positions, open_orders, restrictions, position_mode, multi_assets = (
            await asyncio.gather(
                client.account(),
                client.position_risk(instrument.symbol),
                client.open_orders(),
                client.api_restrictions(),
                client.position_mode(),
                client.multi_assets_mode(),
            )
        )
        active = next(
            (
                row
                for row in positions
                if Decimal(str(row.get("positionAmt", "0"))) != 0
            ),
            positions[0] if positions else {},
        )
        nonzero_positions = [
            row
            for row in account.get("positions", [])
            if Decimal(str(row.get("positionAmt", "0"))) != 0
        ]
        available = Decimal(str(account.get("availableBalance", "0")))
        reading_enabled = bool(restrictions.get("enableReading", False))
        result["signed"] = {
            "ok": reading_enabled,
            "can_trade": bool(account.get("canTrade", False)),
            "api_reading_enabled": reading_enabled,
            "futures_trading_permitted": bool(account.get("canTrade", False)),
            "withdrawals_enabled": bool(restrictions.get("enableWithdrawals", False)),
            "ip_restricted": bool(restrictions.get("ipRestrict", False)),
            "server_time_offset_ms": offset,
            "open_order_count": len(open_orders),
            "soxl_position_flat": not any(
                Decimal(str(row.get("positionAmt", "0"))) != 0 for row in positions
            ),
            "other_positions_flat": not any(
                row.get("symbol") != instrument.symbol for row in nonzero_positions
            ),
            "position_mode": (
                "hedge" if bool(position_mode.get("dualSidePosition")) else "one_way"
            ),
            "multi_assets_enabled": bool(multi_assets.get("multiAssetsMargin")),
            "leverage": int(active.get("leverage", 0) or 0),
            "margin_mode": str(active.get("marginType", "unknown")).lower(),
            "available_balance_positive": available > 0,
            "available_balance_covers_exchange_minimum": (
                available >= rules.minimum_notional
            ),
        }
        if test_order:
            quantity = _test_quantity(rules, Decimal(str(book["askPrice"])))
            try:
                await client.market_order(
                    symbol=instrument.symbol,
                    side="BUY",
                    position_side="LONG",
                    quantity=quantity,
                    client_order_id="mmtick-futures-preflight-test",
                    test=True,
                )
            except BinanceFuturesAPIError as exc:
                if store is not None:
                    now_ms = int(time.time() * 1000)
                    store.set_metadata("futures_test_order_passed", "false", now_ms)
                    store.set_metadata(
                        "futures_test_order_error",
                        f"{exc.code}:{exc.message}",
                        now_ms,
                    )
                result["test_order"] = {
                    "ok": False,
                    "endpoint": "/fapi/v1/order/test",
                    "code": exc.code,
                    "message": exc.message,
                    "real_orders_sent": False,
                }
                print(json.dumps(result, indent=2))
                return 1
            if store is not None:
                now_ms = int(time.time() * 1000)
                store.set_metadata("futures_test_order_passed", "true", now_ms)
                store.set_metadata("futures_test_order_error", "", now_ms)
            result["test_order"] = {
                "ok": True,
                "endpoint": "/fapi/v1/order/test",
                "side": "BUY",
                "position_side": "LONG",
                "quantity": str(quantity),
                "real_orders_sent": False,
            }
        print(json.dumps(result, indent=2))
        return 0 if reading_enabled else 1
    finally:
        if owns_client:
            await client.close()


def _test_quantity(rules: FuturesSymbolRules, price: Decimal) -> Decimal:
    target_notional = max(rules.minimum_notional * Decimal("1.02"), Decimal("5.10"))
    quantity = rules.floor_quantity(target_notional / price)
    if quantity < rules.minimum_quantity:
        quantity = rules.minimum_quantity
    while quantity * price < rules.minimum_notional:
        quantity += rules.quantity_step
    return quantity


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate Binance SOXL USD-M Futures access without sending a real order"
    )
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument(
        "--test-order",
        action="store_true",
        help="Call Binance /fapi/v1/order/test; this endpoint never creates an order",
    )
    parser.add_argument(
        "--sign-tradfi-contract",
        action="store_true",
        help=(
            "Accept Binance's TradFi-Perps agreement via /fapi/v1/stock/contract; "
            "use only after reviewing and agreeing to its terms"
        ),
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                args.config,
                test_order=args.test_order,
                sign_tradfi_contract=args.sign_tradfi_contract,
            )
        )
    )


if __name__ == "__main__":
    main()
